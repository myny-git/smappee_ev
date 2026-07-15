"""Regression tests for runtime stability boundaries."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.smappee_ev.api.discovery import MqttChannelSpec
from custom_components.smappee_ev.api.errors import SmappeeConnectionError
from custom_components.smappee_ev.coordinator import SmappeeCoordinator, SmappeeSiteCoordinator
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.models.state import (
    IntegrationData,
    SiteData,
    SiteState,
    StationState,
)
from custom_components.smappee_ev.mqtt_setup import (
    MqttFreshnessState,
    _handle_mqtt_connection_change,
    _setup_mqtt,
)
from custom_components.smappee_ev.runtime_assembly import _create_coordinators
from custom_components.smappee_ev.runtime_lifecycle import ensure_runtime_shutdown
from tests.factories import make_site_runtime, make_station_runtime


@pytest.mark.asyncio
async def test_station_none_is_unavailable_and_valid_empty_recovers(hass):
    station_client = MagicMock()
    station_client.async_get_smartdevices = AsyncMock(side_effect=[None, []])
    station_client.service_location_id = 1
    station_client.site_location_id = 1
    station_client.charging_station_serial = "station"
    station_client.serial = "station"
    coordinator = SmappeeCoordinator(
        hass,
        station_client=station_client,
        connector_clients={},
        update_interval=60,
    )

    failed = await coordinator._fetch_station_state(station_client)
    recovered = await coordinator._fetch_station_state(station_client)

    assert failed.api_available is False
    assert recovered.api_available is True


@pytest.mark.asyncio
async def test_dashboard_throttle_advances_only_for_usable_response(hass):
    station_client = MagicMock()
    station_client.service_location_id = 1
    station_client.site_location_id = 1
    station_client.charging_station_serial = "station"
    station_client.serial = "station"
    dashboard = MagicMock()
    for method in (
        "async_get_charging_station_details",
        "async_get_capacity_protection",
        "async_get_overload_protection",
        "async_get_highlevel_configuration",
        "async_get_appliances",
    ):
        setattr(dashboard, method, AsyncMock(return_value=None))
    dashboard.async_get_load_management = AsyncMock(return_value=None)
    coordinator = SmappeeCoordinator(
        hass,
        station_client=station_client,
        connector_clients={},
        update_interval=60,
        dashboard_client=dashboard,
    )
    coordinator.data = IntegrationData(station=StationState(), connectors={})

    await coordinator._maybe_refresh_dashboard_data(coordinator.data, force=True)
    assert coordinator._last_dashboard_refresh == 0.0

    dashboard.async_get_appliances.return_value = []
    await coordinator._maybe_refresh_dashboard_data(coordinator.data, force=True)
    assert coordinator._last_dashboard_refresh > 0.0


@pytest.mark.asyncio
async def test_power_mapping_valid_empty_is_negative_cached(hass):
    station_client = MagicMock()
    station_client.service_location_id = 1
    dashboard = MagicMock()
    dashboard.async_get_highlevel_configuration = AsyncMock(return_value={})
    coordinator = SmappeeCoordinator(
        hass,
        station_client=station_client,
        connector_clients={},
        update_interval=60,
        dashboard_client=dashboard,
    )

    await coordinator._ensure_power_index_map()
    await coordinator._ensure_power_index_map()

    assert coordinator._power_index_maps_by_topic == {}
    dashboard.async_get_highlevel_configuration.assert_awaited_once()


@pytest.mark.asyncio
async def test_power_mapping_transient_failure_retries_only_after_backoff(hass):
    station_client = MagicMock()
    station_client.service_location_id = 1
    dashboard = MagicMock()
    dashboard.async_get_highlevel_configuration = AsyncMock(
        side_effect=[SmappeeConnectionError("offline"), {}]
    )
    coordinator = SmappeeCoordinator(
        hass,
        station_client=station_client,
        connector_clients={},
        update_interval=60,
        dashboard_client=dashboard,
    )

    with patch(
        "custom_components.smappee_ev.coordinators.power.monotonic",
        side_effect=[0.0, 0.0, 30.0, 61.0],
    ):
        await coordinator._ensure_power_index_map()
        await coordinator._ensure_power_index_map()
        await coordinator._ensure_power_index_map()

    assert coordinator._power_index_maps_by_topic == {}
    assert dashboard.async_get_highlevel_configuration.await_count == 2


def test_mqtt_freshness_separates_heartbeat_charger_power_and_clients():
    freshness = MqttFreshnessState(clients_connected={0: False, 1: False})
    assert freshness.mqtt_transport_connected is False
    assert freshness.record_connection(0, True) is False
    assert freshness.mqtt_transport_connected is False
    assert freshness.record_connection(1, True) is True
    assert freshness.mqtt_transport_connected is True
    assert freshness.record_connection(1, True) is False

    freshness.record_message("servicelocation/x/homeassistant/heartbeat")
    assert freshness.last_heartbeat_rx is not None
    assert freshness.last_real_charger_rx is None
    assert freshness.last_real_power_rx is None

    freshness.record_message(
        "servicelocation/x/etc/carcharger/acchargingcontroller/v1/devices/y/state"
    )
    assert freshness.last_real_charger_rx is not None
    assert freshness.last_real_power_rx is None

    freshness.record_message("servicelocation/x/power")
    assert freshness.last_real_power_rx is not None
    freshness.record_connection(0, False)
    assert freshness.mqtt_transport_connected is False


def test_mqtt_transport_connect_never_disables_rest_fallback():
    coordinator = MagicMock()
    coordinator.update_interval = timedelta(seconds=60)
    bucket = make_station_runtime(coordinator=coordinator)
    schedule_refresh = MagicMock()

    _handle_mqtt_connection_change(True, None, {"station": bucket}, 60, schedule_refresh)
    _handle_mqtt_connection_change(True, None, {"station": bucket}, 60, schedule_refresh)

    assert coordinator.update_interval == timedelta(seconds=60)
    schedule_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_runtime_shutdown_is_shared_for_concurrent_callers(hass):
    coordinator = MagicMock()
    coordinator.async_shutdown = AsyncMock()
    mqtt = MagicMock()
    mqtt.stop = AsyncMock()
    runtime = RuntimeData(
        api=MagicMock(),
        sites={
            1: MagicMock(
                site_coordinator=None,
                stations={"station": make_station_runtime(coordinator=coordinator)},
            )
        },
        mqtt={1: mqtt},
    )

    first = ensure_runtime_shutdown(hass, runtime)
    second = ensure_runtime_shutdown(hass, runtime)
    assert first is second
    await asyncio.gather(first, second)
    assert ensure_runtime_shutdown(hass, runtime) is first
    coordinator.async_shutdown.assert_awaited_once()
    mqtt.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_station_failure_rolls_back_first_without_starting_tracking(hass):
    first = MagicMock()
    first.async_config_entry_first_refresh = AsyncMock()
    first.async_shutdown = AsyncMock()
    first.async_start_session_tracking = MagicMock()
    second = MagicMock()
    second.async_config_entry_first_refresh = AsyncMock(side_effect=RuntimeError("failed"))
    second.async_shutdown = AsyncMock()
    second.async_start_session_tracking = MagicMock()
    stations = {
        "one": make_station_runtime(station_uuid="one"),
        "two": make_station_runtime(station_uuid="two"),
    }

    with (
        patch(
            "custom_components.smappee_ev.runtime_assembly.SmappeeCoordinator",
            side_effect=[first, second],
        ),
        pytest.raises(RuntimeError, match="failed"),
    ):
        await _create_coordinators(hass, stations, 60)

    first.async_start_session_tracking.assert_not_called()
    second.async_start_session_tracking.assert_not_called()
    first.async_shutdown.assert_awaited_once()
    second.async_shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_mqtt_freshness_fallback_and_concurrent_shutdown_end_to_end(hass):
    """Exercise transport, real data, heartbeat-only time, fallback, and shutdown."""
    coordinator = MagicMock()
    coordinator.update_interval = timedelta(seconds=60)
    coordinator._shutting_down = False
    coordinator.apply_mqtt_connection_change = MagicMock()
    coordinator.apply_mqtt_properties = MagicMock()
    coordinator.async_shutdown = AsyncMock()
    coordinator.cancel_delayed_refreshes = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    station = make_station_runtime(coordinator=coordinator)
    stations = {"station": station}
    mqtt = MagicMock()
    mqtt.start = AsyncMock()
    mqtt.stop = AsyncMock()
    mqtt.track_start_task = MagicMock()

    with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt", return_value=mqtt) as cls:
        mqtt_runtime = _setup_mqtt(
            hass,
            "site-uuid",
            "station",
            1,
            stations,
            "client",
            60,
        )
        on_connection = cls.call_args.kwargs["on_connection_change"]
        on_properties = cls.call_args.kwargs["on_properties"]

        on_connection(True)
        assert coordinator.update_interval == timedelta(seconds=60)
        with patch("custom_components.smappee_ev.mqtt_setup.datetime") as mocked_datetime:
            mocked_datetime.now.side_effect = [
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
                datetime(2026, 1, 1, 0, 11, tzinfo=UTC),
            ]
            on_properties(
                "servicelocation/x/etc/carcharger/acchargingcontroller/v1/devices/y/state",
                {"chargingState": "CHARGING"},
            )
            charger_rx = coordinator.last_real_charger_rx
            on_properties("servicelocation/x/homeassistant/heartbeat", {})
            assert coordinator.last_real_charger_rx == charger_rx
            assert coordinator.last_heartbeat_rx > charger_rx
            on_properties("servicelocation/x/power", {"activePowerData": [1]})
            assert coordinator.last_real_power_rx > coordinator.last_heartbeat_rx

        # Ten heartbeat-only minutes never disabled the slow REST safety net.
        assert coordinator.update_interval == timedelta(seconds=60)

    runtime = RuntimeData(
        api=MagicMock(),
        sites={1: make_site_runtime(stations=stations)},
        mqtt={1: mqtt_runtime},
    )
    first = ensure_runtime_shutdown(hass, runtime)
    second = ensure_runtime_shutdown(hass, runtime)
    await asyncio.gather(first, second)

    assert first is second
    coordinator.async_shutdown.assert_awaited_once()
    mqtt.stop.assert_awaited_once()


def test_route_aware_power_freshness_is_copied_to_site_coordinator(hass):
    station_coordinator = MagicMock()
    station_coordinator.update_interval = timedelta(seconds=60)
    site_coordinator = MagicMock()
    station = make_station_runtime(coordinator=station_coordinator)
    topic = "custom/realtime/grid-values"
    spec = MqttChannelSpec(1, "grid", "activePower", topic, None, None, [])
    mqtt = MagicMock()
    mqtt.start = AsyncMock()
    mqtt.track_start_task = MagicMock()

    with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt", return_value=mqtt) as cls:
        _setup_mqtt(
            hass,
            "site-uuid",
            "station",
            1,
            {"station": station},
            "client",
            60,
            mqtt_specs=[spec],
            site_coordinator=site_coordinator,
        )
        on_properties = cls.call_args.kwargs["on_properties"]

        on_properties(topic, {"activePowerData": [100]})

    assert site_coordinator.last_real_power_rx is not None
    assert station_coordinator.last_real_power_rx == site_coordinator.last_real_power_rx
    site_coordinator.apply_mqtt_properties.assert_called_once_with(
        topic, {"activePowerData": [100]}
    )


@pytest.mark.parametrize(
    ("role", "payload", "state_attr", "expected"),
    [
        ("consumption", {"consumptionPower": 4100}, "house_consumption_power", 4100),
        ("production_total", {"solarPower": 2300}, "pv_power_total", 2300),
        ("always_on", {"alwaysOn": 175}, "always_on_power", 175),
    ],
)
def test_routed_site_aggregate_updates_freshness_and_state_without_index_map(
    hass, role, payload, state_attr, expected
):
    """One routed aggregate message must update freshness and its entity state."""
    topic = f"custom/realtime/{role}"
    coordinator = SmappeeSiteCoordinator(
        hass,
        site_location_id=1,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="gateway",
        gateway_type="Genius",
        update_interval=60,
    )
    coordinator.data = SiteData(site=SiteState())
    assert coordinator._power_index_maps_by_topic is None
    spec = MqttChannelSpec(1, role, "activePower", topic, None, None, [])
    mqtt = MagicMock()
    mqtt.start = AsyncMock()
    mqtt.track_start_task = MagicMock()

    with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt", return_value=mqtt) as cls:
        _setup_mqtt(
            hass,
            "site-uuid",
            "station",
            1,
            {},
            "client",
            60,
            mqtt_specs=[spec],
            site_coordinator=coordinator,
        )
        cls.call_args.kwargs["on_properties"](topic, payload)

    assert coordinator.last_real_power_rx is not None
    assert getattr(coordinator.data.site, state_attr) == expected
