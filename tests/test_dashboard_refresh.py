"""Dashboard refresh: merging station details, error handling and scheduling."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed
import pytest

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.coordinator import SmappeeStationCoordinator
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState


def _station_coordinator(hass) -> SmappeeStationCoordinator:
    station_client = MagicMock(spec=SmappeeDeviceHandle)
    station_client.service_location_id = 200
    station_client.site_location_id = 100
    station_client.serial = "SERIAL"
    station_client.charging_station_serial = "STATION-1"
    connector_one = MagicMock(spec=SmappeeDeviceHandle)
    connector_one.connector_number = 1
    connector_two = MagicMock(spec=SmappeeDeviceHandle)
    connector_two.connector_number = 2
    coord = SmappeeStationCoordinator(
        hass,
        station_client=station_client,
        connector_clients={"conn-1": connector_one, "conn-2": connector_two},
        update_interval=60,
    )
    coord.data = IntegrationData(
        station=StationState(),
        connectors={
            "conn-1": ConnectorState(connector_number=1),
            "conn-2": ConnectorState(connector_number=2),
        },
    )
    return coord


def test_station_dashboard_details_merge_led_connector_and_fallback_state(hass):
    coord = _station_coordinator(hass)
    details = {
        "available": False,
        "features": ["MAX_CURRENT"],
        "maximumCapacity": "32",
        "offlineCharging": {"enabled": True, "failSafe": "10"},
        "modules": [
            {
                "smartDevice": {
                    "id": "led-device",
                    "type": {"category": "LED"},
                    "configurationProperties": [
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.led.config.brightness"
                            },
                            "values": [{"Integer": 42}],
                        }
                    ],
                }
            },
            {
                "position": 1,
                "smartDevice": {
                    "id": "conn-device",
                    "uuid": "conn-1",
                    "name": "Cable 1",
                    "type": {"category": "CARCHARGER"},
                    "configurationProperties": [
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.config.max.current"
                            },
                            "values": [{"Quantity": {"value": "24", "unit": "A"}}],
                        },
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.config.min.current"
                            },
                            "value": {"value": "7"},
                        },
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.config.min.excesspct"
                            },
                            "values": [{"Integer": "66"}],
                        },
                    ],
                    "carCharger": {
                        "connectionStatus": "CONNECTED",
                        "iecStatus": "C1",
                        "chargingMode": "SMART",
                        "optimizationStrategy": "SCHEDULES_FIRST_THEN_EXCESS",
                        "status": {"current": "CHARGING", "stoppedByCloud": True},
                    },
                },
            },
            {"smartDevice": {"type": {"category": "UNKNOWN"}}},
        ],
    }

    assert coord._merge_dashboard_station_details(coord.data, details) is True

    station = coord.data.station
    conn = coord.data.connectors["conn-1"]
    assert station.dashboard_available is False
    assert station.station_features == ["MAX_CURRENT"]
    assert station.maximum_capacity_a == 32
    assert station.offline_charging_enabled is True
    assert station.offline_failsafe_current_a == 10
    assert station.dashboard_led_device_id == "led-device"
    assert station.led_brightness == 42
    assert conn.dashboard_device_id == "conn-device"
    assert conn.dashboard_device_name == "Cable 1"
    assert conn.max_current == 24
    assert conn.min_current == 7
    assert conn.min_surpluspct == 66
    assert conn.connection_status == "CONNECTED"
    assert conn.evcc_state == "C"
    assert conn.evcc_state_code == 2
    assert conn.selected_mode == "SMART"
    assert conn.stopped_by_cloud is True


@pytest.mark.asyncio
async def test_station_dashboard_refresh_handles_partial_errors_and_auth_failures(hass):
    coord = _station_coordinator(hass)
    dashboard = MagicMock()
    dashboard.async_get_charging_station_details = AsyncMock(return_value={"available": True})
    dashboard.async_get_capacity_protection = AsyncMock(side_effect=RuntimeError("capacity down"))
    dashboard.async_get_overload_protection = AsyncMock(
        return_value={"active": True, "maximumLoad": 25}
    )
    dashboard.async_get_highlevel_configuration = AsyncMock(return_value=None)
    dashboard.async_get_appliances = AsyncMock(return_value=[])
    dashboard.async_get_load_management = AsyncMock(
        return_value={"optimizationStrategy": "EXCESS_ONLY"}
    )
    coord.dashboard_client = dashboard
    coord.data.connectors["conn-1"].dashboard_device_id = "device-1"

    assert await coord._maybe_refresh_dashboard_data(coord.data, force=True) is True
    assert coord.data.station.dashboard_available is True
    assert coord.data.station.overload_maximum_load_a == 25
    assert coord.data.connectors["conn-1"].selected_mode == "SOLAR"

    dashboard.async_get_charging_station_details.side_effect = ConfigEntryAuthFailed("reauth")
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._maybe_refresh_dashboard_data(coord.data, force=True)


@pytest.mark.asyncio
async def test_station_dashboard_scheduled_refresh_reauth_and_cleanup(hass):
    entry = MagicMock()
    coord = _station_coordinator(hass)
    coord.config_entry = entry
    coord._maybe_refresh_dashboard_data = AsyncMock(side_effect=ConfigEntryAuthFailed("reauth"))

    await coord._async_dashboard_refresh_now()

    entry.async_start_reauth_if_available.assert_called_once_with(hass)
    assert coord._dashboard_refresh_task is None


@pytest.mark.asyncio
async def test_station_dashboard_scheduled_refresh_skips_during_shutdown(hass):
    coord = _station_coordinator(hass)
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=True)

    coord.cancel_delayed_refreshes()
    await coord._async_dashboard_refresh_now()

    coord._maybe_refresh_dashboard_data.assert_not_called()
    assert coord._dashboard_refresh_task is None


def test_dashboard_delayed_refresh_uses_unsub_and_leaves_no_sleeping_task_on_shutdown(hass):
    coord = _station_coordinator(hass)
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=True)
    unsub = MagicMock()

    with patch(
        "custom_components.smappee_ev.coordinators.dashboard_merge.async_call_later",
        return_value=unsub,
    ):
        coord.async_schedule_dashboard_refresh(delay=30)

    assert coord._dashboard_refresh_unsub is unsub
    assert coord._dashboard_refresh_task is None

    coord.cancel_delayed_refreshes()

    unsub.assert_called_once()
    assert coord._dashboard_refresh_unsub is None
    assert coord._dashboard_refresh_task is None
    coord._maybe_refresh_dashboard_data.assert_not_called()


def test_dashboard_merge_rejects_an_invalid_connector_current_range(hass):
    coord = _station_coordinator(hass)
    module = {
        "position": 1,
        "smartDevice": {
            "uuid": "conn-1",
            "type": {"category": "CARCHARGER"},
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.min.current"},
                    "values": [{"Quantity": {"value": 10, "unit": "A"}}],
                },
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.max.current"},
                    "values": [{"Quantity": {"value": 8, "unit": "A"}}],
                },
            ],
        },
    }

    coord._merge_dashboard_module(coord.data, module)

    conn = coord.data.connectors["conn-1"]
    assert (conn.min_current, conn.max_current) == (6, 32)


def test_dashboard_disjoint_range_updates_without_invalid_intermediate_state(hass):
    """Dashboard must move max first when the new range is above the old range."""
    coord = _station_coordinator(hass)
    conn = coord.data.connectors["conn-1"]
    conn.min_current = 6
    conn.max_current = 10
    observed_ranges = []
    original_set_if_changed = coord._set_if_changed

    def record_range_mutations(obj, attr, value):
        changed = original_set_if_changed(obj, attr, value)
        if changed and obj is conn and attr in {"min_current", "max_current"}:
            observed_ranges.append((conn.min_current, conn.max_current))
        return changed

    module = {
        "position": 1,
        "smartDevice": {
            "uuid": "conn-1",
            "type": {"category": "CARCHARGER"},
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.min.current"},
                    "value": 15,
                },
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.max.current"},
                    "value": 25,
                },
            ],
        },
    }

    with patch.object(coord, "_set_if_changed", side_effect=record_range_mutations):
        coord._merge_dashboard_module(coord.data, module)

    assert observed_ranges == [
        (6, 25),
        (15, 25),
    ]
    assert (conn.min_current, conn.max_current) == (15, 25)
