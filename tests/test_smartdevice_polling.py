"""Smartdevice polling shares one fresh response within each station refresh."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import ConfigEntryAuthFailed
import pytest

from custom_components.smappee_ev import coordinator as coordinator_module
from custom_components.smappee_ev.api.dashboard_client import SmappeeDashboardClient
from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.api.errors import SmappeeConnectionError
from custom_components.smappee_ev.coordinator import SmappeeCoordinator


def make_handle(device_id, location=123):
    return SmappeeDeviceHandle(
        serial="station",
        smart_device_uuid=device_id,
        smart_device_id=device_id,
        service_location_id=location,
        connector_number=1,
    )


@pytest.fixture
def polling_clock(monkeypatch):
    clock = [1000.0]
    started = datetime.now(UTC)
    wall_clock = MagicMock(wraps=datetime)
    wall_clock.now.side_effect = lambda _tz: started + timedelta(seconds=clock[0] - 1000)
    monkeypatch.setattr("custom_components.smappee_ev.coordinator._monotonic", lambda: clock[0])
    monkeypatch.setattr("custom_components.smappee_ev.coordinator.datetime", wall_clock)
    return clock


@pytest.fixture
def polling_coordinator(hass, polling_clock):
    dashboard = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token="test-refresh",  # noqa: S106 - synthetic credential
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    dashboard._request = AsyncMock()
    coordinator = SmappeeCoordinator(
        hass,
        station_client=make_handle("led"),
        connector_clients={},
        update_interval=30,
        dashboard_client=dashboard,
    )
    coordinator._ensure_power_index_map = AsyncMock()
    coordinator._maybe_refresh_dashboard_data = AsyncMock(return_value=False)
    return coordinator


def add_connectors(coordinator, count):
    for index in range(count):
        device_id = f"connector-{index}"
        client = make_handle(device_id)
        client.dashboard_client = coordinator.dashboard_client
        coordinator.connector_clients[device_id] = client


def device_list(count, percentage=40):
    return [
        {
            "id": "led",
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.led.config.brightness"},
                    "value": 75,
                }
            ],
        },
        *[
            {
                ("id", "uuid", "smartDeviceId", "smartDeviceUuid")[index % 4]: f"connector-{index}",
                "properties": [{"spec": {"name": "percentageLimit"}, "value": percentage}],
            }
            for index in range(count)
        ],
    ]


@pytest.mark.parametrize("count", [1, 2, 4])
async def test_one_list_per_refresh_for_station_and_all_connectors(
    polling_coordinator, polling_clock, count
):
    coordinator = polling_coordinator
    add_connectors(coordinator, count)
    request = coordinator.dashboard_client._request
    request.side_effect = [device_list(count), device_list(count, percentage=60)]

    first = await coordinator._async_update_data()

    request.assert_awaited_once_with(
        "GET",
        "v10/servicelocation/123/homecontrol/smart/devices",
        params={"excludedCategories": ""},
        return_json=True,
    )
    assert first.station.led_brightness == 75
    assert all(conn.selected_percentage_limit == 40 for conn in first.connectors.values())
    assert all(conn.api_available for conn in first.connectors.values())

    coordinator.data = first
    polling_clock[0] += 300
    second = await coordinator._async_update_data()

    assert request.await_count == 2
    assert all(conn.selected_percentage_limit == 60 for conn in second.connectors.values())
    assert coordinator.update_interval.total_seconds() == 30


@pytest.mark.parametrize("failure", [None, [], SmappeeConnectionError("offline")])
async def test_failed_list_is_shared_and_next_refresh_recovers(
    polling_coordinator, polling_clock, failure
):
    coordinator = polling_coordinator
    add_connectors(coordinator, 2)
    request = coordinator.dashboard_client._request
    request.side_effect = [device_list(2), failure, device_list(2, percentage=60)]
    coordinator.data = await coordinator._async_update_data()

    polling_clock[0] += 300
    failed = await coordinator._async_update_data()

    assert request.await_count == 2
    assert all(not conn.api_available for conn in failed.connectors.values())
    assert all(conn.selected_percentage_limit == 40 for conn in failed.connectors.values())
    coordinator.data = failed

    polling_clock[0] += 300
    recovered = await coordinator._async_update_data()

    assert request.await_count == 3
    assert recovered.station.api_available
    assert all(conn.api_available for conn in recovered.connectors.values())
    assert all(conn.selected_percentage_limit == 60 for conn in recovered.connectors.values())


async def test_missing_connector_does_not_trigger_another_request(polling_coordinator):
    coordinator = polling_coordinator
    add_connectors(coordinator, 2)
    coordinator.dashboard_client._request.return_value = device_list(1)

    result = await coordinator._async_update_data()

    coordinator.dashboard_client._request.assert_awaited_once()
    assert result.connectors["connector-0"].api_available
    assert not result.connectors["connector-1"].api_available


@pytest.mark.parametrize("error", [ConfigEntryAuthFailed("reauth"), asyncio.CancelledError()])
@pytest.mark.parametrize("failed_location", [123, 456])
async def test_authentication_and_cancellation_propagate(
    polling_coordinator, error, failed_location
):
    coordinator = polling_coordinator
    add_connectors(coordinator, 2)
    for client in coordinator.connector_clients.values():
        client.service_location_id = failed_location
    request = coordinator.dashboard_client._request
    request.side_effect = error if failed_location == 123 else [device_list(0), error]

    with pytest.raises(type(error)):
        await coordinator._async_update_data()

    assert request.await_count == (1 if failed_location == 123 else 2)


async def test_concurrent_lookups_share_only_the_same_location(polling_coordinator):
    dashboard = polling_coordinator.dashboard_client
    dashboard._request.return_value = device_list(1)
    clients = [make_handle("connector-0", location) for location in (123, "123", 456)]
    for client in clients:
        client.dashboard_client = dashboard
    requests = {}

    results = await asyncio.gather(
        *(client.async_get_smartdevice("connector-0", requests=requests) for client in clients)
    )

    assert all(result["id"] == "connector-0" for result in results)
    assert dashboard._request.await_count == 2
    assert {call.args[1] for call in dashboard._request.await_args_list} == {
        "v10/servicelocation/123/homecontrol/smart/devices",
        "v10/servicelocation/456/homecontrol/smart/devices",
    }


@pytest.mark.parametrize("mqtt_state", ["healthy", "disconnected", "silent", "heartbeat", "power"])
async def test_poll_deadlines_ignore_local_ticks_and_unrelated_traffic(
    polling_coordinator, polling_clock, mqtt_state
):
    coordinator = polling_coordinator
    add_connectors(coordinator, 2)
    request = coordinator.dashboard_client._request
    request.return_value = device_list(2)
    coordinator.data = await coordinator._async_update_data()
    coordinator.mqtt_transport_connected = mqtt_state != "disconnected"
    interval = 1800 if mqtt_state == "healthy" else 300
    deadline = polling_clock[0] + interval
    snapshot = coordinator.data
    # A skipped poll must preserve the newer MQTT state, not replay a cached REST list.
    snapshot.connectors["connector-0"].selected_percentage_limit = 73

    while polling_clock[0] < deadline:
        now = coordinator_module.datetime.now(UTC)
        if mqtt_state == "healthy":
            coordinator.last_valid_charger_telemetry_rx = now
        elif mqtt_state == "heartbeat":
            coordinator.last_heartbeat_rx = now
        elif mqtt_state == "power":
            coordinator.last_real_power_rx = now
        assert await coordinator._async_update_data() is snapshot
        assert snapshot.connectors["connector-0"].selected_percentage_limit == 73
        request.assert_awaited_once()
        polling_clock[0] += 30

    coordinator.data = await coordinator._async_update_data()
    assert request.await_count == 2


async def test_silent_mqtt_switches_to_fallback_and_fresh_traffic_restores_slow_polling(
    polling_coordinator, polling_clock
):
    coordinator = polling_coordinator
    add_connectors(coordinator, 1)
    request = coordinator.dashboard_client._request
    request.return_value = device_list(1)
    coordinator.mqtt_transport_connected = True
    coordinator.last_valid_charger_telemetry_rx = coordinator_module.datetime.now(UTC)
    coordinator.data = await coordinator._async_update_data()

    polling_clock[0] += 299
    await coordinator._async_update_data()
    request.assert_awaited_once()
    polling_clock[0] += 1
    coordinator.data = await coordinator._async_update_data()
    assert request.await_count == 2

    polling_clock[0] += 300
    coordinator.last_valid_charger_telemetry_rx = coordinator_module.datetime.now(UTC)
    await coordinator._async_update_data()
    assert request.await_count == 2

    coordinator.mqtt_transport_connected = False
    coordinator.data = await coordinator._async_update_data()
    assert request.await_count == 3
    await coordinator._async_update_data()
    assert request.await_count == 3


async def test_rest_failure_retries_after_five_minutes_even_with_healthy_mqtt(
    polling_coordinator, polling_clock
):
    coordinator = polling_coordinator
    add_connectors(coordinator, 1)
    request = coordinator.dashboard_client._request
    request.side_effect = [SmappeeConnectionError("offline"), device_list(1)]
    coordinator.mqtt_transport_connected = True
    coordinator.last_valid_charger_telemetry_rx = coordinator_module.datetime.now(UTC)
    coordinator.data = await coordinator._async_update_data()
    assert not coordinator.data.station.api_available

    polling_clock[0] += 299
    await coordinator._async_update_data()
    request.assert_awaited_once()
    polling_clock[0] += 1
    coordinator.last_valid_charger_telemetry_rx = coordinator_module.datetime.now(UTC)
    coordinator.data = await coordinator._async_update_data()
    assert request.await_count == 2
    assert coordinator.data.station.api_available
    assert coordinator._smartdevice_poll_interval() == timedelta(minutes=30)


async def test_failed_refresh_is_not_reported_recovered_by_a_local_tick(
    polling_coordinator, polling_clock
):
    coordinator = polling_coordinator
    coordinator._async_fetch_data = AsyncMock(side_effect=ConfigEntryAuthFailed("reauth"))
    for _ in range(2):
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        polling_clock[0] += 30
    coordinator._async_fetch_data.assert_awaited_once()


async def test_cancelled_poll_does_not_postpone_the_next_attempt(polling_coordinator):
    coordinator = polling_coordinator
    add_connectors(coordinator, 1)
    request = coordinator.dashboard_client._request
    request.side_effect = [asyncio.CancelledError(), device_list(1)]
    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_update_data()

    coordinator.data = await coordinator._async_update_data()
    assert request.await_count == 2
    assert coordinator.data.connectors["connector-0"].api_available


async def test_write_refresh_is_debounced_and_bypasses_periodic_deadline(
    polling_coordinator, polling_clock, monkeypatch
):
    coordinator = polling_coordinator
    add_connectors(coordinator, 1)
    request = coordinator.dashboard_client._request
    request.side_effect = [device_list(1), device_list(1, percentage=60)]
    coordinator.data = await coordinator._async_update_data()
    coordinator.mqtt_transport_connected = True
    coordinator.last_valid_charger_telemetry_rx = coordinator_module.datetime.now(UTC)
    schedule = MagicMock(side_effect=[MagicMock(), MagicMock()])
    monkeypatch.setattr(
        "custom_components.smappee_ev.coordinators.dashboard_merge.async_call_later", schedule
    )

    coordinator.async_schedule_dashboard_refresh()
    cancel_first = coordinator._dashboard_refresh_unsub
    coordinator.async_schedule_dashboard_refresh()
    cancel_first.assert_called_once()
    assert [call.args[1] for call in schedule.call_args_list] == [120, 120]

    polling_clock[0] += 120
    await schedule.call_args.args[2](coordinator_module.datetime.now(UTC))
    await coordinator._dashboard_refresh_task

    assert request.await_count == 2
    assert coordinator.data.connectors["connector-0"].selected_percentage_limit == 60
    assert not coordinator._force_smartdevice_refresh
    assert coordinator._dashboard_refresh_task is None
    await coordinator.async_shutdown()
