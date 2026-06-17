"""Tests for the coordinator of the Smappee EV integration."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.smappee_ev.coordinator import (
    SmappeeCoordinator,
    _amps_from_ma,
    _pick,
    _to_int,
)
from custom_components.smappee_ev.data import (
    ConnectorState,
    IntegrationData,
    RuntimeData,
    StationState,
)
from custom_components.smappee_ev.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.sensor import ConnectorSessionEnergySensor

CHARGINGSTATE_TOPIC = (
    "servicelocation/site/etc/carcharger/acchargingcontroller/v1"
    "/devices/test_uuid/property/chargingstate"
)


def test_helper_functions():
    """Test standalone helper functions in coordinator.py."""
    # Test _to_int
    assert _to_int(10) == 10
    assert _to_int("10") == 10
    assert _to_int(None) == 0
    assert _to_int(None, 5) == 5
    assert _to_int("invalid", 5) == 5

    # Test _pick
    seq = [1, 2, 3, 4, 5]
    idxs = [0, 2, 4]
    assert _pick(seq, idxs) == [1, 3, 5]
    assert _pick(seq, [10, 20]) == [0, 0]  # Out of range indices return 0
    assert _pick([], [0, 1]) == [0, 0]  # Empty sequence returns [0, 0] for the given indices
    assert _pick(seq, []) == []  # Empty indices returns empty list
    assert _pick("not a list", [0, 1]) == []  # Non-list sequence returns empty list

    # Test _amps_from_ma
    assert _amps_from_ma([1000, 2000, 3000]) == [1.0, 2.0, 3.0]
    assert _amps_from_ma([1234, 5678]) == [1.234, 5.678]
    assert _amps_from_ma([]) == []
    assert _amps_from_ma(None) == []


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    hass.async_create_task.side_effect = lambda coro: asyncio.create_task(coro)
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock ConfigEntry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.state = "loaded"
    entry.domain = "smappee_ev"
    return entry


@pytest.fixture
def mock_runtime_data():
    """Create a mock RuntimeData."""
    api_client = MagicMock(spec=SmappeeDeviceHandle)
    mqtt_client = MagicMock()
    sites = {12345: {"stations": {"station1": {"station_client": api_client}}}}
    return RuntimeData(api=api_client, sites=sites, mqtt=mqtt_client)


@pytest.fixture
def mock_station_client():
    """Create a mock SmappeeDeviceHandle for station."""
    client = MagicMock(spec=SmappeeDeviceHandle)
    client.service_location_id = 12345
    client.async_get_smartdevices = AsyncMock(return_value=[])
    client.async_get_recent_sessions = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_connector_client():
    """Create a mock SmappeeDeviceHandle for connector."""
    client = MagicMock(spec=SmappeeDeviceHandle)
    client.service_location_id = 12345
    client.smart_device_id = "test_device_id"
    client.connector_number = 1
    client.charging_station_serial = "STATION123"
    client.min_current = 6
    client.max_current = 32
    client.serial = "GATEWAY123"
    client.async_get_recent_sessions = AsyncMock(return_value=[])
    return client


@pytest.fixture
def coordinator(mock_hass, mock_station_client, mock_connector_client, mock_config_entry):
    """Create a SmappeeCoordinator instance."""
    connector_clients = {"test_uuid": mock_connector_client}
    coord = SmappeeCoordinator(
        hass=mock_hass,
        station_client=mock_station_client,
        connector_clients=connector_clients,
        update_interval=60,
        config_entry=mock_config_entry,
    )
    # Initialize with basic data
    coord.data = IntegrationData(
        station=StationState(led_brightness=70, available=True),
        connectors={"test_uuid": ConnectorState(connector_number=1)},
    )
    return coord


class TestSmappeeCoordinator:
    """Test cases for SmappeeCoordinator."""

    @pytest.mark.asyncio
    async def test_initialization(self, coordinator, mock_station_client):
        """Test coordinator initialization."""
        assert coordinator.station_client is mock_station_client
        assert "test_uuid" in coordinator.connector_clients
        assert coordinator.update_interval == timedelta(seconds=60)
        assert coordinator.name == "Smappee EV Coordinator"

    @pytest.mark.asyncio
    async def test_dashboard_refresh_merges_config_without_live_overwrite(self, coordinator):
        """Dashboard refresh should merge config/cache fields without touching live MQTT data."""
        dashboard = MagicMock()
        dashboard.async_get_charging_station_details = AsyncMock(
            return_value={
                "available": True,
                "features": ["SOLAR_SURPLUS_CHARGING", "MAX_CURRENT"],
                "maximumCapacity": 25,
                "offlineCharging": {"enabled": True, "failSafe": 6},
                "modules": [
                    {
                        "position": 0,
                        "smartDevice": {
                            "id": "LED-controller-123",
                            "type": {"category": "LED"},
                            "configurationProperties": [
                                {
                                    "spec": {
                                        "name": "etc.smart.device.type.car.charger.led.config.brightness"
                                    },
                                    "values": [{"Integer": 70}],
                                },
                            ],
                        },
                    },
                    {
                        "position": 1,
                        "smartDevice": {
                            "id": "CARCHARGER-acchargingcontroller-123",
                            "type": {"category": "CARCHARGER"},
                            "configurationProperties": [
                                {
                                    "spec": {
                                        "name": "etc.smart.device.type.car.charger.config.max.current"
                                    },
                                    "values": [{"Quantity": {"value": 32, "unit": "A"}}],
                                },
                                {
                                    "spec": {
                                        "name": "etc.smart.device.type.car.charger.config.min.current"
                                    },
                                    "values": [{"Quantity": {"value": 6, "unit": "A"}}],
                                },
                                {
                                    "spec": {
                                        "name": "etc.smart.device.type.car.charger.config.min.excesspct"
                                    },
                                    "values": [{"Integer": 84}],
                                },
                            ],
                            "carCharger": {
                                "chargingMode": "NORMAL",
                                "connectionStatus": "CONNECTED",
                                "iecStatus": "B2",
                                "status": {"current": "AVAILABLE", "stoppedByCloud": False},
                                "chargingStateUpdateChannel": {
                                    "name": "servicelocation/site/etc/carcharger/acchargingcontroller/v1/devices/test_uuid/property/chargingstate"
                                },
                            },
                        },
                    },
                ],
            }
        )
        dashboard.async_get_capacity_protection = AsyncMock(
            return_value={"active": True, "capacityMaximumPower": 5.0}
        )
        dashboard.async_get_overload_protection = AsyncMock(
            return_value={"active": True, "maximumLoad": 25}
        )
        dashboard.async_get_highlevel_configuration = AsyncMock(return_value={})
        dashboard.async_get_appliances = AsyncMock(return_value=[])
        coordinator.dashboard_client = dashboard
        coordinator.station_client.service_location_id = 317443
        coordinator.station_client.site_location_id = 317418
        coordinator.station_client.charging_station_serial = "STATION123"
        coordinator.station_client.serial = "GATEWAY123"

        data = coordinator.data
        conn = data.connectors["test_uuid"]
        conn.raw_charging_mode = "SMART"
        conn.power_total = 1234
        conn.current_phases = [1.0, 2.0, 3.0]
        conn.energy_import_kwh = 12.3

        changed = await coordinator._maybe_refresh_dashboard_data(data, force=True)

        assert changed
        assert data.station.station_features == ["SOLAR_SURPLUS_CHARGING", "MAX_CURRENT"]
        assert data.station.maximum_capacity_a == 25
        assert data.station.offline_charging_enabled is True
        assert data.station.capacity_protection_active is True
        assert data.station.capacity_maximum_power_kw == 5.0
        assert data.station.overload_protection_active is True
        assert data.station.overload_maximum_load_a == 25
        assert data.station.led_brightness == 70
        assert data.station.dashboard_led_device_id == "LED-controller-123"
        assert coordinator.station_client.dashboard_device_id == "LED-controller-123"
        assert coordinator.station_client.dashboard_client is dashboard
        assert conn.dashboard_device_id == "CARCHARGER-acchargingcontroller-123"
        assert (
            coordinator.connector_clients["test_uuid"].dashboard_device_id
            == "CARCHARGER-acchargingcontroller-123"
        )
        assert coordinator.connector_clients["test_uuid"].dashboard_client is dashboard
        assert conn.min_current == 6
        assert conn.max_current == 32
        assert conn.min_surpluspct == 84
        assert conn.connection_status == "CONNECTED"
        assert conn.iec_status == "B2"
        assert conn.raw_charging_mode == "SMART"
        assert conn.power_total == 1234
        assert conn.current_phases == [1.0, 2.0, 3.0]
        assert conn.energy_import_kwh == 12.3
        dashboard.async_get_capacity_protection.assert_awaited_once_with(317418)
        dashboard.async_get_overload_protection.assert_awaited_once_with(317418)
        dashboard.async_get_highlevel_configuration.assert_awaited_once_with(317443)
        dashboard.async_get_appliances.assert_awaited_once_with(317443)

    @pytest.mark.asyncio
    async def test_dashboard_refresh_is_throttled(self, coordinator):
        """Dashboard refresh should skip calls until the slow interval expires."""
        dashboard = MagicMock()
        dashboard.async_get_charging_station_details = AsyncMock(return_value={})
        dashboard.async_get_capacity_protection = AsyncMock(return_value={})
        dashboard.async_get_overload_protection = AsyncMock(return_value={})
        dashboard.async_get_highlevel_configuration = AsyncMock(return_value={})
        dashboard.async_get_appliances = AsyncMock(return_value=[])
        coordinator.dashboard_client = dashboard
        coordinator.station_client.charging_station_serial = "STATION123"
        coordinator.station_client.serial = "GATEWAY123"

        await coordinator._maybe_refresh_dashboard_data(coordinator.data, force=True)
        dashboard.async_get_charging_station_details.reset_mock()

        changed = await coordinator._maybe_refresh_dashboard_data(coordinator.data)

        assert not changed
        dashboard.async_get_charging_station_details.assert_not_called()

    @pytest.mark.asyncio
    async def test_dashboard_refresh_failure_does_not_fail_integration(self, coordinator):
        """Dashboard failures should be logged/throttled and keep existing data."""
        dashboard = MagicMock()
        dashboard.async_get_charging_station_details = AsyncMock(
            side_effect=RuntimeError("dashboard down")
        )
        dashboard.async_get_capacity_protection = AsyncMock(return_value=None)
        dashboard.async_get_overload_protection = AsyncMock(return_value=None)
        dashboard.async_get_highlevel_configuration = AsyncMock(return_value=None)
        dashboard.async_get_appliances = AsyncMock(return_value=None)
        coordinator.dashboard_client = dashboard
        coordinator.station_client.charging_station_serial = "STATION123"
        coordinator.station_client.serial = "GATEWAY123"

        changed = await coordinator._maybe_refresh_dashboard_data(coordinator.data, force=True)

        assert not changed

    @pytest.mark.asyncio
    async def test_dashboard_refresh_auth_failure_triggers_reauth(self, coordinator):
        """Dashboard auth failures should bubble out of gathered refresh calls."""
        dashboard = MagicMock()
        dashboard.async_get_charging_station_details = AsyncMock(
            side_effect=ConfigEntryAuthFailed("dashboard auth failed")
        )
        dashboard.async_get_capacity_protection = AsyncMock(return_value={})
        dashboard.async_get_overload_protection = AsyncMock(return_value={})
        dashboard.async_get_highlevel_configuration = AsyncMock(return_value={})
        dashboard.async_get_appliances = AsyncMock(return_value=[])
        coordinator.dashboard_client = dashboard
        coordinator.station_client.charging_station_serial = "STATION123"
        coordinator.station_client.serial = "GATEWAY123"

        with pytest.raises(ConfigEntryAuthFailed, match="dashboard auth failed"):
            await coordinator._maybe_refresh_dashboard_data(coordinator.data, force=True)

    @pytest.mark.asyncio
    async def test_dashboard_load_management_auth_failure_triggers_reauth(self, coordinator):
        """Per-connector load-management auth failures should not be logged only."""
        dashboard = MagicMock()
        dashboard.async_get_load_management = AsyncMock(
            side_effect=ConfigEntryAuthFailed("load management auth failed")
        )
        coordinator.dashboard_client = dashboard
        coordinator.data.connectors["test_uuid"].dashboard_device_id = "device-1"

        with pytest.raises(ConfigEntryAuthFailed, match="load management auth failed"):
            await coordinator._refresh_dashboard_load_management(coordinator.data)

    @pytest.mark.asyncio
    async def test_recent_session_refresh_is_throttled(self, coordinator, mock_connector_client):
        """Test recent session refresh cache and throttle."""
        mock_connector_client.async_get_recent_sessions.return_value = [{"energy": 1.2}]

        await coordinator._async_refresh_recent_sessions("test", force=True)
        await coordinator._async_refresh_recent_sessions("test")

        mock_connector_client.async_get_recent_sessions.assert_called_once()
        assert coordinator.data.recent_sessions == [{"energy": 1.2}]

    @pytest.mark.asyncio
    async def test_recent_session_partial_auth_failure_triggers_reauth(self, coordinator):
        """Any connector auth failure should trigger reauth, even with partial success."""
        ok_client = MagicMock(spec=SmappeeDeviceHandle)
        ok_client.async_get_recent_sessions = AsyncMock(return_value=[{"energy": 1.2}])
        auth_client = MagicMock(spec=SmappeeDeviceHandle)
        auth_client.async_get_recent_sessions = AsyncMock(
            side_effect=ConfigEntryAuthFailed("recent sessions auth failed")
        )
        coordinator.connector_clients = {"ok": ok_client, "auth": auth_client}

        with pytest.raises(ConfigEntryAuthFailed, match="recent sessions auth failed"):
            await coordinator._async_get_recent_sessions()

    @pytest.mark.asyncio
    async def test_recent_session_partial_runtime_failure_keeps_successful_sessions(
        self, coordinator
    ):
        """A down connector should not discard recent sessions from healthy connectors."""
        ok_client = MagicMock(spec=SmappeeDeviceHandle)
        ok_client.async_get_recent_sessions = AsyncMock(
            return_value=[{"energy": 1.2}, "malformed", {"energy": 2.3}]
        )
        failing_client = MagicMock(spec=SmappeeDeviceHandle)
        failing_client.async_get_recent_sessions = AsyncMock(side_effect=RuntimeError("offline"))
        coordinator.connector_clients = {"ok": ok_client, "failing": failing_client}

        sessions = await coordinator._async_get_recent_sessions()

        assert sessions == [{"energy": 1.2}, {"energy": 2.3}]
        assert coordinator._connector_session_available == {"ok": True, "failing": False}

    @pytest.mark.asyncio
    async def test_recent_session_all_runtime_failures_raise_first_error(self, coordinator):
        """All connector session endpoints failing should surface an error to the refresh path."""
        first_client = MagicMock(spec=SmappeeDeviceHandle)
        first_client.async_get_recent_sessions = AsyncMock(side_effect=RuntimeError("first"))
        second_client = MagicMock(spec=SmappeeDeviceHandle)
        second_client.async_get_recent_sessions = AsyncMock(side_effect=RuntimeError("second"))
        coordinator.connector_clients = {"first": first_client, "second": second_client}

        with pytest.raises(RuntimeError, match="first"):
            await coordinator._async_get_recent_sessions()

        assert coordinator._connector_session_available == {"first": False, "second": False}

    @pytest.mark.asyncio
    async def test_recent_session_refresh_failure_is_attempt_throttled(
        self, coordinator, mock_connector_client
    ):
        """Test failed recent session refreshes are throttled by last attempt time."""
        mock_connector_client.async_get_recent_sessions.side_effect = RuntimeError("cloud down")

        with patch("custom_components.smappee_ev.coordinator._now", side_effect=(1000.0, 1060.0)):
            await coordinator._async_refresh_recent_sessions("test")
            await coordinator._async_refresh_recent_sessions("test")

        mock_connector_client.async_get_recent_sessions.assert_called_once()
        assert coordinator._last_session_api_attempt == 1000.0
        assert coordinator._last_session_api_update == 0.0

    @pytest.mark.asyncio
    async def test_active_session_loop_cancels_without_active_connectors(
        self, coordinator, mock_connector_client
    ):
        """The periodic session loop should stop before API I/O when no session is active."""
        scheduled = []
        active_loop_unsub = MagicMock()

        def capture_interval(_hass, callback, interval):
            scheduled.append((callback, interval))
            return active_loop_unsub

        with patch(
            "custom_components.smappee_ev.coordinator.async_track_time_interval",
            side_effect=capture_interval,
        ):
            conn = coordinator.data.connectors["test_uuid"]
            conn.session_state = "CHARGING"
            coordinator._ensure_active_session_loop()
            conn.session_state = "AVAILABLE"
            await scheduled[0][0](None)

        active_loop_unsub.assert_called_once()
        mock_connector_client.async_get_recent_sessions.assert_not_called()
        assert coordinator._session_active_loop_unsub is None

    @pytest.mark.asyncio
    async def test_active_session_loop_reschedules_when_session_becomes_paused(self, coordinator):
        """Active-session polling should slow down when the only active session is paused."""
        scheduled = []
        unsubs = [MagicMock(), MagicMock()]

        def capture_interval(_hass, callback, interval):
            scheduled.append((callback, interval))
            return unsubs[len(scheduled) - 1]

        async def pause_during_refresh(_reason, *, force=False):
            conn = coordinator.data.connectors["test_uuid"]
            conn.raw_charging_mode = "PAUSED"

        with patch(
            "custom_components.smappee_ev.coordinator.async_track_time_interval",
            side_effect=capture_interval,
        ):
            conn = coordinator.data.connectors["test_uuid"]
            conn.session_state = "CHARGING"
            coordinator._async_refresh_recent_sessions = AsyncMock(side_effect=pause_during_refresh)
            coordinator._ensure_active_session_loop()
            await scheduled[0][0](None)

        assert len(scheduled) == 2
        assert scheduled[0][1] != scheduled[1][1]
        unsubs[0].assert_called_once()
        assert coordinator._session_active_loop_unsub is unsubs[1]

    @pytest.mark.asyncio
    async def test_chargingstate_started_schedules_session_refresh(
        self, coordinator, mock_connector_client
    ):
        """Test STARTED MQTT state schedules a debounced session refresh."""
        mock_connector_client.async_get_recent_sessions.return_value = [
            {"energy": 2.0, "connectorUuid": "test_uuid"}
        ]
        scheduled: list[tuple[int, object]] = []

        def capture_call_later(_hass, delay, callback):
            scheduled.append((delay, callback))
            return MagicMock()

        with (
            patch("custom_components.smappee_ev.coordinator.SESSION_START_REFRESH_DELAY", 0),
            patch(
                "custom_components.smappee_ev.coordinator.async_call_later",
                side_effect=capture_call_later,
            ),
            patch(
                "custom_components.smappee_ev.coordinator.async_track_time_interval",
                return_value=MagicMock(),
            ),
        ):
            coordinator.async_start_session_tracking()
            conn = coordinator.data.connectors["test_uuid"]
            coordinator.apply_mqtt_properties(CHARGINGSTATE_TOPIC, {"chargingState": "STARTED"})
            assert [delay for delay, _ in scheduled] == [0, 0]
            assert conn.session_state == "STARTED"
            await scheduled[-1][1](None)

        mock_connector_client.async_get_recent_sessions.assert_called_once()
        assert coordinator.data.recent_sessions == [{"energy": 2.0, "connectorUuid": "test_uuid"}]
        sensor = ConnectorSessionEnergySensor(
            coordinator, mock_connector_client, 12345, "station_uuid", "test_uuid"
        )
        assert sensor.native_value == 2.0
        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_chargingstate_active_update_does_not_reschedule_start_refresh(self, coordinator):
        """Test repeated active MQTT updates do not keep delaying the start refresh."""
        scheduled: list[tuple[int, object]] = []

        def capture_call_later(_hass, delay, callback):
            scheduled.append((delay, callback))
            return MagicMock()

        with (
            patch("custom_components.smappee_ev.coordinator.SESSION_START_REFRESH_DELAY", 60),
            patch(
                "custom_components.smappee_ev.coordinator.async_call_later",
                side_effect=capture_call_later,
            ),
            patch(
                "custom_components.smappee_ev.coordinator.async_track_time_interval",
                return_value=MagicMock(),
            ),
        ):
            coordinator.async_start_session_tracking()
            coordinator.apply_mqtt_properties(CHARGINGSTATE_TOPIC, {"chargingState": "STARTED"})

            coordinator.apply_mqtt_properties(CHARGINGSTATE_TOPIC, {"chargingState": "STARTED"})

        assert [delay for delay, _ in scheduled] == [0, 60]
        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_chargingstate_stopped_schedules_final_session_refresh(
        self, coordinator, mock_connector_client
    ):
        """Test STOPPED MQTT state schedules final session refresh."""
        mock_connector_client.async_get_recent_sessions.return_value = [
            {"energy": 4.0, "connectorUuid": "test_uuid"}
        ]
        conn = coordinator.data.connectors["test_uuid"]
        conn.session_state = "CHARGING"
        scheduled: list[tuple[int, object]] = []
        active_loop_unsub = MagicMock()

        def capture_call_later(_hass, delay, callback):
            scheduled.append((delay, callback))
            return MagicMock()

        with (
            patch("custom_components.smappee_ev.coordinator.SESSION_FINAL_REFRESH_DELAYS", (0,)),
            patch(
                "custom_components.smappee_ev.coordinator.async_call_later",
                side_effect=capture_call_later,
            ),
            patch(
                "custom_components.smappee_ev.coordinator.async_track_time_interval",
                return_value=active_loop_unsub,
            ),
        ):
            coordinator.async_start_session_tracking()
            coordinator.apply_mqtt_properties(CHARGINGSTATE_TOPIC, {"chargingState": "STOPPED"})
            assert [delay for delay, _ in scheduled] == [0, 0]
            await scheduled[-1][1](None)

        mock_connector_client.async_get_recent_sessions.assert_called_once()
        assert coordinator.data.recent_sessions == [{"energy": 4.0, "connectorUuid": "test_uuid"}]
        active_loop_unsub.assert_called_once()
        sensor = ConnectorSessionEnergySensor(
            coordinator, mock_connector_client, 12345, "station_uuid", "test_uuid"
        )
        assert sensor.native_value == 4.0
        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_fetch_station_state_success(self, coordinator):
        """Test successful station state fetch."""
        coordinator.station_client.async_get_smartdevices = AsyncMock(
            return_value=[
                {
                    "configurationProperties": [
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.led.config.brightness"
                            },
                            "value": {"value": 75},
                        }
                    ]
                }
            ]
        )

        result = await coordinator._fetch_station_state(coordinator.station_client)

        assert isinstance(result, StationState)
        assert result.led_brightness == 75
        assert result.available is True
        assert result.api_available is True

    @pytest.mark.asyncio
    async def test_fetch_station_state_network_error(self, coordinator):
        """Test station state fetch with network error."""
        coordinator.station_client.async_get_smartdevices = AsyncMock(side_effect=ClientError())

        result = await coordinator._fetch_station_state(coordinator.station_client)

        assert isinstance(result, StationState)
        assert result.led_brightness is None
        assert result.available is True
        assert result.api_available is False

    @pytest.mark.asyncio
    async def test_fetch_station_state_default_brightness(self, coordinator):
        """Test station state fetch with default brightness when API fails."""
        coordinator.station_client.async_get_smartdevices = AsyncMock(
            side_effect=RuntimeError("Not found")
        )

        result = await coordinator._fetch_station_state(coordinator.station_client)

        assert isinstance(result, StationState)
        assert result.led_brightness is None
        assert result.available is True
        assert result.api_available is False

    @pytest.mark.asyncio
    async def test_fetch_connector_state_success(self, coordinator):
        """Test successful connector state fetch."""
        client = coordinator.connector_clients["test_uuid"]
        client.async_get_smartdevice = AsyncMock(
            return_value={
                "properties": [
                    {"spec": {"name": "chargingState"}, "value": "Available"},
                    {"spec": {"name": "percentageLimit"}, "value": "80"},
                ],
                "configurationProperties": [
                    {
                        "spec": {"name": "etc.smart.device.type.car.charger.config.max.current"},
                        "value": {"value": 32},
                    },
                    {
                        "spec": {"name": "etc.smart.device.type.car.charger.config.min.current"},
                        "value": {"value": 6},
                    },
                    {
                        "spec": {"name": "etc.smart.device.type.car.charger.config.min.excesspct"},
                        "value": {"value": 10},
                    },
                ],
            }
        )

        result = await coordinator._fetch_connector_state(client)

        assert isinstance(result, ConnectorState)
        assert result.session_state == "Available"
        assert result.selected_percentage_limit == 80
        assert result.max_current == 32
        assert result.min_current == 6
        assert result.min_surpluspct == 10
        assert result.support_grid is None

    @pytest.mark.asyncio
    async def test_fetch_connector_state_support_grid(self, coordinator):
        """Test connector state fetch derives support_grid from config properties."""
        client = coordinator.connector_clients["test_uuid"]
        client.async_get_smartdevice = AsyncMock(
            return_value={
                "properties": [],
                "configurationProperties": [
                    {
                        "spec": {
                            "name": "etc.smart.device.type.car.charger.config.max.gridassistanceamps"
                        },
                        "value": 4,
                    }
                ],
            }
        )

        result = await coordinator._fetch_connector_state(client)

        assert result.support_grid == 4

    @pytest.mark.asyncio
    async def test_fetch_connector_state_api_error(self, coordinator):
        """Test connector state fetch with API error."""
        client = coordinator.connector_clients["test_uuid"]
        client.async_get_smartdevice = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="smartdevice fetch.*returned no data"):
            await coordinator._fetch_connector_state(client)

    @pytest.mark.asyncio
    async def test_update_data_success(self, coordinator):
        """Test successful data update."""
        # Mock station state fetch
        mock_station_state = StationState(led_brightness=75, available=True)
        coordinator._fetch_station_state = AsyncMock(return_value=mock_station_state)

        # Mock connector state fetch
        mock_connector_state = ConnectorState(
            connector_number=1,
            session_state="Available",
            selected_current_limit=None,
            selected_percentage_limit=80,
            selected_mode="NORMAL",
            min_current=6,
            max_current=32,
            min_surpluspct=10,
        )
        coordinator._fetch_connector_state = AsyncMock(return_value=mock_connector_state)

        # Mock metering configuration
        coordinator._ensure_power_index_map = AsyncMock()

        result = await coordinator._async_update_data()

        assert isinstance(result, IntegrationData)
        assert result.station.led_brightness == mock_station_state.led_brightness
        assert "test_uuid" in result.connectors
        assert result.connectors["test_uuid"].session_state == mock_connector_state.session_state

    @pytest.mark.asyncio
    async def test_update_data_connector_exception(self, coordinator):
        """Test data update with connector exception."""
        # Mock station state fetch
        mock_station_state = StationState(led_brightness=75, available=True)
        coordinator._fetch_station_state = AsyncMock(return_value=mock_station_state)
        previous_state = coordinator.data.connectors["test_uuid"]
        previous_state.session_state = "Charging"
        previous_state.selected_mode = "SMART"
        previous_state.min_surpluspct = 42

        # Mock connector state fetch with exception
        coordinator._fetch_connector_state = AsyncMock(side_effect=Exception("Connection failed"))

        # Mock metering configuration
        coordinator._ensure_power_index_map = AsyncMock()

        result = await coordinator._async_update_data()

        assert isinstance(result, IntegrationData)
        assert result.station.led_brightness == mock_station_state.led_brightness
        assert "test_uuid" in result.connectors

        # Should preserve last-known state while marking the connector API unavailable.
        fallback_state = result.connectors["test_uuid"]
        assert fallback_state.session_state == "Charging"
        assert fallback_state.selected_mode == "SMART"
        assert fallback_state.min_surpluspct == 42
        assert fallback_state.api_available is False

    @pytest.mark.asyncio
    async def test_update_data_station_exception(self, coordinator):
        """Test data update with station exception."""
        # Mock station state fetch with exception
        coordinator._fetch_station_state = AsyncMock(side_effect=ClientError("Network error"))

        with pytest.raises(UpdateFailed, match="Error fetching Smappee data"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_update_data_preserves_mqtt_only_state(self, coordinator):
        """Test REST refresh does not wipe MQTT-only station and connector fields."""
        previous = coordinator.data
        previous.station.mqtt_connected = True
        previous.station.last_mqtt_rx = 123.4
        previous.station.grid_power_total = 456
        previous.connectors["test_uuid"].iec_status = "B1"
        previous.connectors["test_uuid"].selected_mode = "SMART"
        previous.connectors["test_uuid"].power_total = 789

        coordinator._fetch_station_state = AsyncMock(
            return_value=StationState(led_brightness=80, api_available=True)
        )
        coordinator._fetch_connector_state = AsyncMock(
            return_value=ConnectorState(
                connector_number=1,
                session_state="Available",
                selected_percentage_limit=80,
                selected_mode=None,
                min_current=6,
                max_current=32,
                min_surpluspct=10,
                api_available=True,
            )
        )
        coordinator._ensure_power_index_map = AsyncMock()

        result = await coordinator._async_update_data()

        assert result.station.led_brightness == 80
        assert result.station.mqtt_connected is True
        assert result.station.last_mqtt_rx == 123.4
        assert result.station.grid_power_total == 456

        conn = result.connectors["test_uuid"]
        assert conn.session_state == "Available"
        assert conn.selected_mode == "SMART"
        assert conn.iec_status == "B1"
        assert conn.power_total == 789

    @pytest.mark.asyncio
    async def test_update_data_connector_auth_exception_triggers_reauth(self, coordinator):
        """Test connector auth failure is not swallowed by fallback state."""
        coordinator._fetch_station_state = AsyncMock(
            return_value=StationState(led_brightness=75, available=True)
        )
        coordinator._fetch_connector_state = AsyncMock(
            side_effect=ConfigEntryAuthFailed("connector auth failed")
        )

        with pytest.raises(ConfigEntryAuthFailed, match="connector auth failed"):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_update_data_station_cancelled(self, coordinator):
        """Test data update lets task cancellation bubble up."""
        coordinator._fetch_station_state = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_ensure_power_index_map(self, coordinator):
        """Test _ensure_power_index_map method."""
        topic = "servicelocation/site/power"
        highlevel_config = {
            "measurements": [
                {
                    "type": "GRID",
                    "updateChannels": {
                        "activePower": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.channelData[9]"}],
                        },
                        "current": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.currentData[3]"}],
                        },
                        "meterReadings": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.importActiveEnergyData[3]"}],
                        },
                    },
                },
                {
                    "type": "PRODUCTION",
                    "updateChannels": {
                        "activePower": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.channelData[15]"}],
                        },
                        "current": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.currentData[6]"}],
                        },
                        "meterReadings": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.importActiveEnergyData[6]"}],
                        },
                    },
                },
                {
                    "type": "APPLIANCE",
                    "name": "Charger - 1",
                    "appliance": {"type": "CAR_CHARGER"},
                    "updateChannels": {
                        "activePower": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.channelData[3]"}],
                        },
                        "current": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.currentData[0]"}],
                        },
                        "meterReadings": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.importActiveEnergyData[0]"}],
                        },
                    },
                },
            ],
        }
        dashboard = MagicMock()
        dashboard.async_get_highlevel_configuration = AsyncMock(return_value=highlevel_config)
        coordinator.dashboard_client = dashboard

        # First call should set up the map
        await coordinator._ensure_power_index_map()
        assert coordinator._power_index_maps_by_topic is not None
        topic_map = coordinator._power_index_maps_by_topic[topic]
        assert topic_map["grid"]["power"] == [9]
        assert topic_map["grid"]["power_field"] == "channelData"
        assert topic_map["grid"]["current"] == [3]
        assert topic_map["grid"]["energy"] == [3]
        assert topic_map["pv"]["power"] == [15]
        assert topic_map["pv"]["power_field"] == "channelData"
        assert topic_map["pv"]["current"] == [6]
        assert "test_uuid" in topic_map["cars"]
        assert topic_map["cars"]["test_uuid"]["power"] == [3]
        assert topic_map["cars"]["test_uuid"]["power_field"] == "channelData"
        assert topic_map["cars"]["test_uuid"]["current"] == [0]
        assert topic_map["cars"]["test_uuid"]["energy"] == [0]

        # Reset the mock to verify it's not called again
        dashboard.async_get_highlevel_configuration.reset_mock()

        # Second call should use cached map
        await coordinator._ensure_power_index_map()
        dashboard.async_get_highlevel_configuration.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_power_index_map_retries_after_missing_config(self, coordinator):
        """Test highlevel config fetch failure does not cache an empty map."""
        topic = "servicelocation/site/power"
        highlevel_config = {
            "measurements": [
                {
                    "type": "GRID",
                    "updateChannels": {
                        "activePower": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.channelData[9]"}],
                        },
                        "meterReadings": {
                            "protocol": "MQTT",
                            "name": topic,
                            "aspectPaths": [{"path": "$.importActiveEnergyData[3]"}],
                        },
                    },
                },
            ],
        }
        dashboard = MagicMock()
        dashboard.async_get_highlevel_configuration = AsyncMock(
            side_effect=[
                None,
                highlevel_config,
            ]
        )
        coordinator.dashboard_client = dashboard

        await coordinator._ensure_power_index_map()
        assert coordinator._power_index_maps_by_topic is None

        await coordinator._ensure_power_index_map()
        assert coordinator._power_index_maps_by_topic is not None
        assert coordinator._power_index_maps_by_topic[topic]["grid"]["power"] == [9]
        assert dashboard.async_get_highlevel_configuration.call_count == 2

    def test_mqtt_connection_change(self, coordinator):
        """Test MQTT connection status change."""
        # Create mock data
        mock_station = coordinator.data.station
        mock_station.mqtt_connected = False

        # Mock async_set_updated_data
        coordinator.async_set_updated_data = MagicMock()

        # Test connection up
        coordinator.apply_mqtt_connection_change(True)
        assert mock_station.mqtt_connected is True
        assert hasattr(mock_station, "last_mqtt_rx")
        coordinator.async_set_updated_data.assert_called_with(coordinator.data)

        # Test connection already up (should still update timestamp)
        old_timestamp = mock_station.last_mqtt_rx
        coordinator.apply_mqtt_connection_change(True)
        assert mock_station.mqtt_connected is True
        assert mock_station.last_mqtt_rx >= old_timestamp

        # Test connection down (not yet implemented, should remain up)
        coordinator.apply_mqtt_connection_change(False)
        assert mock_station.mqtt_connected is False
        coordinator.async_set_updated_data.assert_called_with(coordinator.data)

    def test_mqtt_topic_parsing(self, coordinator):
        """Test MQTT topic parsing methods."""
        # Test device UUID extraction
        assert coordinator._device_uuid_from_topic("/devices/abc123/property/state") == "abc123"
        assert coordinator._device_uuid_from_topic("/devices/") is None
        assert coordinator._device_uuid_from_topic("/not-devices/abc123") is None

        # Test property name extraction
        assert (
            coordinator._property_name_from_topic("/property/chargingstate/value")
            == "chargingstate"
        )
        assert coordinator._property_name_from_topic("/property/") is None
        assert coordinator._property_name_from_topic("/not-property/state") is None

        # Test station serial extraction
        assert (
            coordinator._station_serial_from_topic("/acchargingstation/v1/SN12345/state")
            == "SN12345"
        )
        assert coordinator._station_serial_from_topic("/acchargingstation/v1/") is None
        assert coordinator._station_serial_from_topic("/not-station/SN12345") is None

    def test_as_int_conversion(self, coordinator):
        """Test _as_int conversion method."""
        assert coordinator._as_int(10) == 10
        assert coordinator._as_int("20") == 20
        assert coordinator._as_int(None) is None
        assert coordinator._as_int(None, 5) == 5
        assert coordinator._as_int("invalid") is None
        assert coordinator._as_int("invalid", 5) == 5

    def test_derive_base_mode(self, coordinator):
        """Test UI mode derivation from strategy."""
        # NORMAL and PAUSED both map to STANDARD in the UI
        assert coordinator._derive_base_mode("NORMAL", "EXCESS_ONLY") == "STANDARD"
        assert coordinator._derive_base_mode("NORMAL", "SCHEDULES_FIRST_THEN_EXCESS") == "STANDARD"
        assert coordinator._derive_base_mode("NORMAL", "NONE") == "STANDARD"
        assert coordinator._derive_base_mode("NORMAL", None) == "STANDARD"
        assert coordinator._derive_base_mode("PAUSED", None) == "STANDARD"
        assert coordinator._derive_base_mode("STANDARD", None) == "STANDARD"
        assert coordinator._derive_base_mode("SMART", "EXCESS_ONLY") == "SOLAR"
        assert coordinator._derive_base_mode("SMART", "SCHEDULES_FIRST_THEN_EXCESS") == "SMART"
        assert coordinator._derive_base_mode("SMART", "NONE") == "SMART"

    def test_is_paused(self, coordinator):
        """Test pause detection logic."""
        assert coordinator._is_paused("PAUSED", "Available", None) is True
        assert coordinator._is_paused("NORMAL", "SUSPENDED", "SUSPENDED_EVSE_USER") is True
        assert coordinator._is_paused("NORMAL", "Available", None) is False

    def test_derive_evcc_letter(self, coordinator):
        """Test EVCC state letter derivation."""
        assert coordinator._derive_evcc_letter("A1") == "A"
        assert coordinator._derive_evcc_letter("B2") == "B"
        assert coordinator._derive_evcc_letter("C3") == "C"
        assert coordinator._derive_evcc_letter("Invalid") is None
        assert coordinator._derive_evcc_letter(None) is None

    def test_evcc_code(self, coordinator):
        """Test EVCC state code mapping."""
        assert coordinator._evcc_code("A") == 0
        assert coordinator._evcc_code("B") == 1
        assert coordinator._evcc_code("C") == 2
        assert coordinator._evcc_code("E") == 3
        assert coordinator._evcc_code("F") == 4
        assert coordinator._evcc_code("Invalid") is None

    def test_set_if_changed(self, coordinator):
        """Test _set_if_changed helper."""
        obj = MagicMock()
        obj.test_attr = "old_value"

        # Should change value and return True
        assert coordinator._set_if_changed(obj, "test_attr", "new_value") is True
        assert obj.test_attr == "new_value"

        # Same value should return False (no change)
        assert coordinator._set_if_changed(obj, "test_attr", "new_value") is False

        # None value should return False (no change)
        assert coordinator._set_if_changed(obj, "test_attr", None) is False
        assert obj.test_attr == "new_value"

    def test_handle_connector_devices_updated(self, coordinator):
        """Test handling of connector devices updated MQTT messages."""
        # Setup test data
        conn = coordinator.data.connectors["test_uuid"]
        conn.min_current = 6
        conn.max_current = 32
        conn.min_surpluspct = 10
        conn.selected_percentage_limit = 50
        conn.optimization_strategy = "NONE"

        # Test with all fields
        payload = {
            "deviceUUID": "test_uuid",
            "minimumCurrent": 8,
            "maximumCurrent": 24,
            "customConfigurationProperties": {
                "etc.smart.device.type.car.charger.config.min.excesspct": 15,
                "etc.smart.device.type.car.charger.smappee.charger.number": 2,
            },
            "percentageLimit": 75,
        }

        assert coordinator._handle_connector_devices_updated(payload) is True
        assert conn.min_current == 8
        assert conn.max_current == 24
        assert conn.min_surpluspct == 15
        assert conn.support_grid is None
        assert conn.connector_number == 2
        assert conn.selected_percentage_limit == 75

        payload["customConfigurationProperties"][
            "etc.smart.device.type.car.charger.config.max.gridassistanceamps"
        ] = 5
        assert coordinator._handle_connector_devices_updated(payload) is True
        assert conn.support_grid == 5

        # Test with unknown device UUID
        payload["deviceUUID"] = "unknown"
        assert coordinator._handle_connector_devices_updated(payload) is False

        # Test with optimization strategy that's not NONE
        payload["deviceUUID"] = "test_uuid"
        conn.optimization_strategy = "EXCESS_ONLY"
        conn.selected_percentage_limit = 50

        # Implementation actually doesn't process percentageLimit for non-NONE strategy
        assert coordinator._handle_connector_devices_updated(payload) is False
        assert conn.selected_percentage_limit == 50  # Should not change

    def test_handle_connector_state(self, coordinator):
        """Test _handle_connector_state method."""
        conn = coordinator.data.connectors["test_uuid"]

        # Test connection status change
        payload = {"connectionStatus": "CONNECTED"}
        assert coordinator._handle_connector_state(conn, payload) is True
        assert conn.connection_status == "CONNECTED"

        # Test no change (same value)
        assert coordinator._handle_connector_state(conn, payload) is False

        # Test configuration errors
        payload = {"configurationErrors": ["ERROR1", "ERROR2"]}
        assert coordinator._handle_connector_state(conn, payload) is True
        assert conn.configuration_errors == ["ERROR1", "ERROR2"]

        # Test both fields
        payload = {"connectionStatus": "DISCONNECTED", "configurationErrors": ["ERROR3"]}
        assert coordinator._handle_connector_state(conn, payload) is True
        assert conn.connection_status == "DISCONNECTED"
        assert conn.configuration_errors == ["ERROR3"]

    def test_handle_connector_property_chargingstate(self, coordinator):
        """Test _handle_connector_property_chargingstate method."""
        with (
            patch.object(coordinator, "_merge_cs_primary", return_value=True) as mock_primary,
            patch.object(coordinator, "_merge_cs_context", return_value=True) as mock_context,
            patch.object(coordinator, "_merge_cs_modes", return_value=True) as mock_modes,
            patch.object(
                coordinator, "_merge_cs_limits_availability", return_value=False
            ) as mock_limits,
            patch.object(coordinator, "_update_evcc", return_value=True) as mock_evcc,
        ):
            conn = coordinator.data.connectors["test_uuid"]
            payload = {"chargingState": "Charging"}

            assert coordinator._handle_connector_property_chargingstate(conn, payload) is True
            mock_primary.assert_called_once_with(conn, payload)
            mock_context.assert_called_once_with(conn, payload)
            mock_modes.assert_called_once_with(conn, payload)
            mock_limits.assert_called_once_with(conn, payload)
            mock_evcc.assert_called_once_with(conn)

    def test_merge_cs_primary(self, coordinator):
        """Test _merge_cs_primary method."""
        conn = coordinator.data.connectors["test_uuid"]

        # Test with chargingState
        payload = {"chargingState": "Charging"}
        assert coordinator._merge_cs_primary(conn, payload) is True
        assert conn.session_state == "Charging"

        # Test with lowercase variant
        payload = {"chargingstate": "Available"}
        assert coordinator._merge_cs_primary(conn, payload) is True
        assert conn.session_state == "Available"

        # Test with missing field
        payload = {"otherField": "value"}
        assert coordinator._merge_cs_primary(conn, payload) is False

    def test_merge_cs_context(self, coordinator):
        """Test _merge_cs_context method."""
        conn = coordinator.data.connectors["test_uuid"]

        # Test with status object
        payload = {"status": {"current": "Running", "stoppedByCloud": True}}
        assert coordinator._merge_cs_context(conn, payload) is True
        assert conn.session_cause == "Running"
        assert conn.status_current == "Running"
        assert conn.stopped_by_cloud is True

        # Test with iec status
        payload = {"iecStatus": {"current": "B"}}
        assert coordinator._merge_cs_context(conn, payload) is True
        assert conn.iec_status == "B"

        # Test with lowercase variant
        payload = {"iecstatus": "C"}
        assert coordinator._merge_cs_context(conn, payload) is True
        assert conn.iec_status == "C"

    def test_merge_cs_modes(self, coordinator):
        """Test _merge_cs_modes method."""
        conn = coordinator.data.connectors["test_uuid"]

        # Test with charging mode and strategy
        payload = {"chargingMode": "NORMAL", "optimizationStrategy": "NONE", "available": True}
        assert coordinator._merge_cs_modes(conn, payload) is True
        assert conn.raw_charging_mode == "NORMAL"
        assert conn.optimization_strategy == "NONE"
        assert conn.ui_mode_base == "STANDARD"  # NORMAL maps to STANDARD in the UI
        assert conn.selected_mode == "STANDARD"
        assert conn.paused is False

        # Test PAUSED mode
        payload = {"chargingMode": "PAUSED"}
        assert coordinator._merge_cs_modes(conn, payload) is True
        assert conn.raw_charging_mode == "PAUSED"
        assert conn.paused is True

        # Test different optimization strategy (must reset chargingMode to SMART so EXCESS_ONLY takes effect)
        payload = {"chargingMode": "SMART", "optimizationStrategy": "EXCESS_ONLY"}
        assert coordinator._merge_cs_modes(conn, payload) is True
        assert conn.optimization_strategy == "EXCESS_ONLY"
        assert conn.ui_mode_base == "SOLAR"
        assert conn.selected_mode == "SOLAR"

    def test_merge_cs_limits_availability(self, coordinator):
        """Test _merge_cs_limits_availability method."""
        conn = coordinator.data.connectors["test_uuid"]
        conn.optimization_strategy = "NONE"
        conn.min_current = 6
        conn.max_current = 32

        # Test with percentage limit in NORMAL mode
        payload = {"percentageLimit": 75}
        assert coordinator._merge_cs_limits_availability(conn, payload) is True
        assert conn.selected_percentage_limit == 75
        assert conn.selected_current_limit is not None

        # Test with lowercase variant
        payload = {"percentagelimit": 50}
        assert coordinator._merge_cs_limits_availability(conn, payload) is True
        assert conn.selected_percentage_limit == 50

        # Test with available flag
        payload = {"available": False}
        assert coordinator._merge_cs_limits_availability(conn, payload) is True
        assert conn.available is False

        # Test with SOLAR mode (should not update percentage)
        conn.optimization_strategy = "EXCESS_ONLY"
        conn.selected_percentage_limit = 25
        payload = {"percentageLimit": 80}
        assert coordinator._merge_cs_limits_availability(conn, payload) is False
        assert conn.selected_percentage_limit == 25  # Unchanged

    def test_chargingstate_available_updates_station_availability(self, coordinator):
        """Test MQTT chargingstate.available drives the station availability switch state."""
        station = coordinator.data.station
        conn = coordinator.data.connectors["test_uuid"]
        station.available = True
        conn.available = True

        coordinator.async_set_updated_data = MagicMock()
        coordinator.apply_mqtt_properties(
            "/etc/carcharger/acchargingcontroller/v1/devices/test_uuid/property/chargingstate",
            {
                "available": False,
                "percentageLimit": 0,
                "chargingState": "STARTED",
                "chargingMode": "NORMAL",
                "optimizationStrategy": "NONE",
                "iecStatus": {"previous": "F", "current": "B2"},
                "status": {
                    "previous": "UNAVAILABLE",
                    "current": "CHARGING_FINISHED",
                    "stoppedByCloud": False,
                    "errors": [],
                },
            },
        )

        assert conn.available is False
        assert station.available is False
        coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)

    def test_update_evcc(self, coordinator):
        """Test _update_evcc method."""
        conn = coordinator.data.connectors["test_uuid"]

        # Test with valid IEC status
        conn.iec_status = "A"
        assert coordinator._update_evcc(conn) is True
        assert conn.evcc_state == "A"
        assert conn.evcc_state_code == 0

        # Test with different IEC status
        conn.iec_status = "B"
        assert coordinator._update_evcc(conn) is True
        assert conn.evcc_state == "B"
        assert conn.evcc_state_code == 1

        # Test with invalid IEC status
        conn.iec_status = "X"
        assert coordinator._update_evcc(conn) is False
        assert conn.evcc_state == "B"  # Unchanged

        # Test with None
        conn.iec_status = None
        assert coordinator._update_evcc(conn) is False

    def test_handle_power(self, coordinator):
        """Test _handle_power method for power data processing."""
        # Setup power index map
        topic = "servicelocation/site/power"
        coordinator._power_index_maps_by_topic = {
            topic: {
                "grid": {"power": [0, 1], "cons": [10, 11], "energy": [9, 10, 1]},
                "pv": {"power": [2], "cons": [12], "energy": [11, 0]},
                "cars": {
                    "test_uuid": {
                        "power": [3],
                        "cons": [13],
                        "energy": [13],
                        "position": 1,
                        "serial": "SN001",
                    }
                },
            }
        }

        # Test payload with all data fields
        payload = {
            "activePowerData": [100, 200, 300, 400],
            "currentData": [1000, 2000, 3000, 4000],
            "importActiveEnergyData": [
                10000,
                20000,
                30000,
                40000,
                50000,
                60000,
                70000,
                80000,
                90000,
                100000,
                110000,
                120000,
                130000,
                140000,
            ],
            "exportActiveEnergyData": [
                1000,
                2000,
                3000,
                4000,
                5000,
                6000,
                7000,
                8000,
                9000,
                10000,
                11000,
                12000,
                13000,
                14000,
            ],
            "consumptionPower": 500,
            "solarPower": 600,
        }

        assert coordinator._handle_power(topic, payload) is True

        # Check station data
        station = coordinator.data.station
        assert station.grid_power_phases == [100, 200]
        assert station.grid_power_total == 300
        assert station.grid_current_phases == [1.0, 2.0]
        assert station.grid_energy_import_kwh == 230.0  # (100000+110000+20000)/1000
        assert station.grid_energy_export_kwh == 23.0  # (10000+11000+2000)/1000

        assert station.pv_power_phases == [300]
        assert station.pv_power_total == 600  # From solarPower
        assert station.pv_current_phases == [3.0]
        assert station.pv_energy_import_kwh == 130.0  # 120000+10000/1000

        assert station.house_consumption_power == 500

        # Check connector data
        conn = coordinator.data.connectors["test_uuid"]
        assert conn.power_phases == [400]
        assert conn.power_total == 400
        assert conn.current_phases == [4.0]
        assert conn.energy_import_kwh == 140.0  # 140000/1000

        # Test with minimal data
        payload = {"activePowerData": [100, 200, 300, 400], "currentData": [1000, 2000, 3000, 4000]}

        assert coordinator._handle_power(topic, payload) is True

        # Grid and PV should update, but energy values are reset to 0.0 when not in payload
        assert station.grid_power_phases == [100, 200]
        assert station.grid_power_total == 300
        assert station.grid_current_phases == [1.0, 2.0]
        # Energy values are reset when not provided in payload
        assert station.grid_energy_import_kwh == 0.0  # Reset to 0
        assert station.grid_energy_export_kwh == 0.0  # Reset to 0
        assert station.pv_energy_import_kwh == 0.0  # Reset to 0
        # Connector power/current updates
        assert conn.power_phases == [400]
        assert conn.power_total == 400
        assert conn.current_phases == [4.0]
        assert conn.energy_import_kwh == 0.0  # Reset to 0

    def test_handle_power_uses_configured_active_power_array(self, coordinator):
        """Test active power indexes are read from their configured MQTT array."""
        topic = "servicelocation/site/power"
        coordinator._power_index_maps_by_topic = {
            topic: {
                "grid": {
                    "power": [0],
                    "power_field": "channelData",
                    "current": [],
                    "energy": [],
                },
                "pv": {"power": [], "current": [], "energy": []},
                "cars": {
                    "test_uuid": {
                        "power": [0],
                        "power_field": "activePowerData",
                        "current": [],
                        "energy": [],
                    }
                },
            },
        }

        payload = {
            "activePowerData": [7200],
            "channelData": [1127],
        }

        assert coordinator._handle_power(topic, payload) is True
        assert coordinator.data.station.grid_power_total == 1127
        assert coordinator.data.connectors["test_uuid"].power_total == 7200

    def test_handle_power_uses_topic_specific_maps_for_parent_child(self, coordinator):
        """Test parent grid and child charger power topics do not overwrite each other."""
        parent_topic = "servicelocation/parent_uuid/power"
        child_topic = "servicelocation/child_uuid/power"
        parent_highlevel = {
            "measurements": [
                {
                    "type": "GRID",
                    "updateChannels": {
                        "activePower": {
                            "protocol": "MQTT",
                            "name": parent_topic,
                            "aspectPaths": [
                                {"path": "$.channelData[3]"},
                                {"path": "$.channelData[5]"},
                                {"path": "$.channelData[7]"},
                            ],
                        }
                    },
                },
            ]
        }
        child_highlevel = {
            "measurements": [
                {
                    "type": "APPLIANCE",
                    "name": "EV Wall - 1",
                    "appliance": {"type": "CAR_CHARGER"},
                    "updateChannels": {
                        "activePower": {
                            "protocol": "MQTT",
                            "name": child_topic,
                            "aspectPaths": [
                                {"path": "$.activePowerData[0]"},
                                {"path": "$.activePowerData[1]"},
                                {"path": "$.activePowerData[2]"},
                            ],
                        },
                        "current": {
                            "protocol": "MQTT",
                            "name": child_topic,
                            "aspectPaths": [
                                {"path": "$.currentData[0]"},
                                {"path": "$.currentData[1]"},
                                {"path": "$.currentData[2]"},
                            ],
                        },
                    },
                },
            ]
        }
        coordinator._power_index_maps_by_topic = (
            coordinator._build_measurement_index_maps_by_topic_from_highlevel_configs(
                {317418: parent_highlevel, 317443: child_highlevel}
            )
        )

        parent_payload = {
            "channelData": [0, 0, 0, 100, 0, 200, 0, 300],
            "activePowerData": [9000, 9000, 9000],
            "currentData": [1000, 2000, 3000],
        }
        child_payload = {
            "activePowerData": [1000, 2000, 3000],
            "channelData": [900, 900, 900, 900, 900, 900, 900, 900],
            "currentData": [4000, 5000, 6000],
        }

        assert coordinator._handle_power(parent_topic, parent_payload) is True
        assert coordinator.data.station.grid_power_total == 600
        assert coordinator.data.connectors["test_uuid"].power_total is None

        assert coordinator._handle_power(child_topic, child_payload) is True
        assert coordinator.data.station.grid_power_total == 600
        connector = coordinator.data.connectors["test_uuid"]
        assert connector.power_total == 6000
        assert connector.current_phases == [4.0, 5.0, 6.0]

    def test_handle_station_properties(self, coordinator):
        """Test _handle_station_properties method."""
        station = coordinator.data.station
        station.led_brightness = 50
        station.available = True

        # Test with both fields
        payload = {"available": False, "ledBrightness": 75}
        assert coordinator._handle_station_properties(payload) is True
        assert station.available is False
        assert station.led_brightness == 75

        # Test with just LED brightness
        payload = {"ledBrightness": 100}
        assert coordinator._handle_station_properties(payload) is True
        assert station.led_brightness == 100

        # Test with availability
        payload = {"available": True}
        assert coordinator._handle_station_properties(payload) is True
        assert station.available is True

        # Test with no change
        assert coordinator._handle_station_properties(payload) is False

    def test_handle_led_updated(self, coordinator):
        """Test _handle_led_updated method."""
        station = coordinator.data.station
        station.led_brightness = 50

        # Test with valid payload
        payload = {
            "configurationPropertyValues": [
                {
                    "propertySpecName": "etc.smart.device.type.car.charger.led.config.brightness",
                    "value": 75,
                }
            ]
        }
        assert coordinator._handle_led_updated(payload) is True
        assert station.led_brightness == 75

        # Test with no change
        assert coordinator._handle_led_updated(payload) is False

        # Test with invalid payload structure
        payload = {"configurationPropertyValues": "not a list"}
        assert coordinator._handle_led_updated(payload) is False
        assert station.led_brightness == 75  # Unchanged

        # Test with missing property name
        payload = {
            "configurationPropertyValues": [{"propertySpecName": "something.else", "value": 100}]
        }
        assert coordinator._handle_led_updated(payload) is False
        assert station.led_brightness == 75  # Unchanged

    def test_apply_mqtt_properties(self, coordinator):
        """Test apply_mqtt_properties method with different topic types."""
        # Setup mock methods
        with (
            patch.object(
                coordinator, "_handle_connector_devices_updated", return_value=True
            ) as mock1,
            patch.object(coordinator, "_handle_connector_mqtt", return_value=True) as mock2,
            patch.object(coordinator, "_handle_power", return_value=True) as mock3,
            patch.object(coordinator, "_handle_station_properties", return_value=True) as mock4,
            patch.object(coordinator, "_handle_led_updated", return_value=True) as mock5,
        ):
            # Basic heartbeat/connection update
            coordinator.data.station.mqtt_connected = False
            coordinator.apply_mqtt_properties("/any/topic", {})
            assert coordinator.data.station.mqtt_connected is True
            assert coordinator.data.station.last_mqtt_rx is not None

            # Connector devices updated
            coordinator.apply_mqtt_properties(
                "/etc/carcharger/acchargingcontroller/v1/devices/updated", {}
            )
            mock1.assert_called_once()

            # Connector-device messages
            coordinator.apply_mqtt_properties(
                "/etc/carcharger/acchargingcontroller/v1/devices/test_uuid/state", {}
            )
            mock2.assert_called_once()

            # Power
            coordinator.apply_mqtt_properties("/some/topic/power", {})
            mock3.assert_called_once()

            # Station properties
            coordinator.apply_mqtt_properties(
                "/etc/chargingstation/acchargingstation/v1/properties", {}
            )
            mock4.assert_called_once()

            # LED brightness
            coordinator.apply_mqtt_properties("/etc/led/acledcontroller/v1/devices/updated", {})
            mock5.assert_called_once()

    def test_apply_mqtt_properties_notifies_on_heartbeat_only(self, coordinator):
        """Test heartbeat-only MQTT messages notify last-seen updates."""
        coordinator.data.station.mqtt_connected = True
        coordinator.data.station.last_mqtt_rx = 1.0
        coordinator.async_set_updated_data = MagicMock()

        coordinator.apply_mqtt_properties("/homeassistant/heartbeat", {})

        assert coordinator.data.station.mqtt_connected is True
        assert coordinator.data.station.last_mqtt_rx > 1.0
        coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)

    def test_handle_connector_mqtt(self, coordinator):
        """Test _handle_connector_mqtt method."""
        # Setup mocks
        with (
            patch.object(coordinator, "_handle_connector_state", return_value=True) as mock_state,
            patch.object(
                coordinator, "_handle_connector_property_chargingstate", return_value=True
            ) as mock_charging,
        ):
            # Test state topic
            coordinator.apply_mqtt_properties(
                "/etc/carcharger/acchargingcontroller/v1/devices/test_uuid/state", {}
            )
            mock_state.assert_called_once()

            # Test charging state property
            coordinator.apply_mqtt_properties(
                "/etc/carcharger/acchargingcontroller/v1/devices/test_uuid/property/chargingstate",
                {},
            )
            mock_charging.assert_called_once()

            # Test unknown UUID
            with patch.object(coordinator, "_device_uuid_from_topic", return_value="unknown_uuid"):
                result = coordinator._handle_connector_mqtt("some/topic", {})
                assert result is False

            # Test other property (not state or chargingstate)
            with (
                patch.object(coordinator, "_device_uuid_from_topic", return_value="test_uuid"),
                patch.object(
                    coordinator, "_property_name_from_topic", return_value="otherproperty"
                ),
            ):
                result = coordinator._handle_connector_mqtt("some/topic/property/otherproperty", {})
                assert result is False

    def test_get_any(self, coordinator):
        """Test _get_any helper method."""
        # Test with exact match
        data = {"key1": "value1", "key2": "value2"}
        assert coordinator._get_any(data, "key1", "key2") == "value1"
        assert coordinator._get_any(data, "key2", "key1") == "value2"

        # Test with case-insensitive match
        data = {"KeY1": "value1", "Key2": "value2"}
        assert coordinator._get_any(data, "key1", "key2") == "value1"
        assert coordinator._get_any(data, "key2", "key1") == "value2"

        # Test with missing keys
        assert coordinator._get_any(data, "key3", "key4") is None

        # Test with empty dict
        assert coordinator._get_any({}, "key1") is None

    def test_apply_station_group(self, coordinator):
        """Test _apply_station_group method."""
        station = coordinator.data.station

        # Test grid data
        payload = {
            "activePowerData": [100, 200, 300],
            "currentData": [1000, 2000, 3000],
            "importActiveEnergyData": [10000, 20000, 30000],
            "exportActiveEnergyData": [1000, 2000, 3000],
        }

        # Apply grid data
        assert coordinator._apply_station_group(station, payload, [0, 1], [0, 1], "grid") is True
        assert station.grid_power_phases == [100, 200]
        assert station.grid_power_total == 300
        assert station.grid_current_phases == [1.0, 2.0]
        assert station.grid_energy_import_kwh == 30.0
        assert station.grid_energy_export_kwh == 3.0

        # Apply PV data
        assert coordinator._apply_station_group(station, payload, [2], [2], "pv") is True
        assert station.pv_power_phases == [300]
        assert station.pv_power_total == 300
        assert station.pv_current_phases == [3.0]
        assert station.pv_energy_import_kwh == 30.0

        # Test with empty indices
        assert coordinator._apply_station_group(station, payload, [], [], "grid") is False

    def test_apply_connector_values(self, coordinator):
        """Test _apply_connector_values method."""
        conn = coordinator.data.connectors["test_uuid"]

        # Test with all data
        payload = {
            "activePowerData": [100, 200, 300],
            "currentData": [1000, 2000, 3000],
            "importActiveEnergyData": [10000, 20000, 30000],
        }

        assert coordinator._apply_connector_values(conn, payload, [0, 1], [0, 1]) is True
        assert conn.power_phases == [100, 200]
        assert conn.power_total == 300
        assert conn.current_phases == [1.0, 2.0]
        assert conn.energy_import_kwh == 30.0

        # Test with identical energy values
        payload["importActiveEnergyData"] = [10000, 10000, 10000]
        assert coordinator._apply_connector_values(conn, payload, [0, 1], [0, 1]) is True
        assert conn.energy_import_kwh == 10.0

        # Test with no energy indices (should still return True because power/current are updated)
        assert coordinator._apply_connector_values(conn, payload, [0, 1], []) is False
        assert conn.power_phases == [100, 200]
        assert conn.power_total == 300
        assert conn.energy_import_kwh == 10.0  # Unchanged without energy indices
