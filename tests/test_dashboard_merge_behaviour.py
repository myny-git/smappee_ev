"""Behaviour tests for the Dashboard refresh/merge mixin of the station coordinator."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed
import pytest

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.api.errors import SmappeeConnectionError, SmappeeMaintenanceError
from custom_components.smappee_ev.const import DASHBOARD_REFRESH_INTERVAL
from custom_components.smappee_ev.coordinator import SmappeeStationCoordinator
from custom_components.smappee_ev.helpers import anonymize_uuid
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState

_MODULE = "custom_components.smappee_ev.coordinators.dashboard_merge"
_INTERVAL = DASHBOARD_REFRESH_INTERVAL.total_seconds()

# Long enough that anonymize_uuid() keeps a distinguishing prefix/suffix.
CONN_1 = "11112222-aaaa-bbbb-cccc-00000000c001"
CONN_2 = "33334444-dddd-eeee-ffff-00000000c002"


def _coordinator(hass) -> SmappeeStationCoordinator:
    station_client = MagicMock(spec=SmappeeDeviceHandle)
    station_client.service_location_id = 200
    station_client.site_location_id = 100
    station_client.serial = "SERIAL"
    station_client.charging_station_serial = "STATION-1"
    clients = {}
    for number, uuid in ((1, CONN_1), (2, CONN_2)):
        client = MagicMock(spec=SmappeeDeviceHandle)
        client.connector_number = number
        clients[uuid] = client
    coord = SmappeeStationCoordinator(
        hass,
        station_client=station_client,
        connector_clients=clients,
        update_interval=60,
    )
    coord.data = IntegrationData(
        station=StationState(),
        connectors={
            CONN_1: ConnectorState(connector_number=1),
            CONN_2: ConnectorState(connector_number=2),
        },
    )
    return coord


def _dashboard(**overrides) -> MagicMock:
    """Dashboard client whose five parallel requests all succeed by default."""
    dashboard = MagicMock()
    dashboard.async_get_charging_station_details = AsyncMock(return_value={"available": True})
    dashboard.async_get_capacity_protection = AsyncMock(
        return_value={"active": True, "capacityMaximumPower": 9.0}
    )
    dashboard.async_get_overload_protection = AsyncMock(
        return_value={"active": True, "maximumLoad": 40}
    )
    dashboard.async_get_highlevel_configuration = AsyncMock(return_value=None)
    dashboard.async_get_appliances = AsyncMock(return_value=[])
    dashboard.async_get_load_management = AsyncMock(return_value=None)
    for name, side_effect in overrides.items():
        getattr(dashboard, name).side_effect = side_effect
    return dashboard


def _refresh_log_records(caplog) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == _MODULE and "Dashboard refresh" in record.getMessage()
    ]


# ---------------------------------------------------------------------------
# 1. Dashboard maintenance keeps cached values and is not a refresh error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_on_one_request_keeps_cached_value_and_logs_nothing(hass, caplog):
    """Prevents Dashboard maintenance from wiping cached values or raising a warning.

    During planned Smappee maintenance the client already logs the transition;
    the coordinator must keep the last known overload limit and stay quiet.
    """
    coord = _coordinator(hass)
    coord.data.station.overload_maximum_load_a = 25
    coord.data.station.overload_protection_active = True
    coord.dashboard_client = _dashboard(
        async_get_overload_protection=SmappeeMaintenanceError("maintenance")
    )

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await coord._maybe_refresh_dashboard_data(coord.data, force=True)

    assert coord.data.station.overload_maximum_load_a == 25
    assert coord.data.station.overload_protection_active is True
    # The other requests were still merged.
    assert coord.data.station.capacity_maximum_power_kw == 9.0
    assert _refresh_log_records(caplog) == []


@pytest.mark.asyncio
async def test_real_error_is_reported_but_maintenance_sibling_is_not(hass, caplog):
    """Prevents a real Dashboard failure from being hidden by, or mixed with, maintenance."""
    coord = _coordinator(hass)
    coord.data.station.overload_maximum_load_a = 25
    coord.data.station.capacity_maximum_power_kw = 7.5
    coord.dashboard_client = _dashboard(
        async_get_overload_protection=SmappeeMaintenanceError("maintenance"),
        async_get_capacity_protection=SmappeeConnectionError("capacity down"),
    )

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await coord._maybe_refresh_dashboard_data(coord.data, force=True)

    assert coord.data.station.overload_maximum_load_a == 25
    assert coord.data.station.capacity_maximum_power_kw == 7.5
    records = _refresh_log_records(caplog)
    assert [record.levelno for record in records] == [logging.WARNING]
    message = records[0].getMessage()
    assert "capacity protection: capacity down" in message
    assert "overload protection" not in message


@pytest.mark.asyncio
async def test_all_requests_in_maintenance_retry_on_next_poll(hass):
    """Prevents a fully-in-maintenance Dashboard from silencing refreshes for 30 minutes.

    Without a single usable response the refresh timestamp must not advance, so
    the next regular (non-forced) poll asks the Dashboard again.
    """
    coord = _coordinator(hass)
    maintenance = SmappeeMaintenanceError("maintenance")
    dashboard = _dashboard(
        async_get_charging_station_details=maintenance,
        async_get_capacity_protection=maintenance,
        async_get_overload_protection=maintenance,
        async_get_highlevel_configuration=maintenance,
        async_get_appliances=maintenance,
    )
    coord.dashboard_client = dashboard

    with patch(f"{_MODULE}._now", return_value=10_000.0):
        await coord._maybe_refresh_dashboard_data(coord.data)
    with patch(f"{_MODULE}._now", return_value=10_060.0):
        await coord._maybe_refresh_dashboard_data(coord.data)

    assert dashboard.async_get_charging_station_details.await_count == 2


@pytest.mark.asyncio
async def test_load_management_maintenance_keeps_strategy_and_reports_only_real_error(hass, caplog):
    """Prevents per-connector maintenance from being logged or resetting the known strategy.

    Connector 1 is in maintenance (keep its strategy, no log); connector 2 fails
    for real and must be reported with its anonymized UUID only.
    """
    coord = _coordinator(hass)
    for conn, device_id in ((CONN_1, "device-1"), (CONN_2, "device-2")):
        coord.data.connectors[conn].dashboard_device_id = device_id
        coord.data.connectors[conn].optimization_strategy = "EXCESS_ONLY"

    async def _load_management(_location, device_id):
        if device_id == "device-1":
            raise SmappeeMaintenanceError("maintenance")
        raise SmappeeConnectionError("lm down")

    dashboard = _dashboard()
    dashboard.async_get_load_management = _load_management
    coord.dashboard_client = dashboard

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await coord._maybe_refresh_dashboard_data(coord.data, force=True)

    assert coord.data.connectors[CONN_1].optimization_strategy == "EXCESS_ONLY"
    assert coord.data.connectors[CONN_2].optimization_strategy == "EXCESS_ONLY"
    records = _refresh_log_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert f"load management {anonymize_uuid(CONN_2)}: lm down" in message
    assert anonymize_uuid(CONN_1) not in message
    assert CONN_2 not in message


@pytest.mark.parametrize("exc_type", [ConfigEntryAuthFailed, asyncio.CancelledError])
@pytest.mark.parametrize("failing", ["details", "load_management"])
@pytest.mark.asyncio
async def test_auth_failure_and_cancellation_propagate_instead_of_being_logged(
    hass, caplog, exc_type, failing
):
    """Prevents reauth/cancellation from being swallowed as a 'Dashboard refresh failed' warning."""
    coord = _coordinator(hass)
    coord.data.connectors[CONN_1].dashboard_device_id = "device-1"
    if failing == "details":
        dashboard = _dashboard(async_get_charging_station_details=exc_type("stop"))
    else:
        dashboard = _dashboard(async_get_load_management=exc_type("stop"))
    coord.dashboard_client = dashboard

    with caplog.at_level(logging.DEBUG, logger=_MODULE), pytest.raises(exc_type):
        await coord._maybe_refresh_dashboard_data(coord.data, force=True)

    assert _refresh_log_records(caplog) == []


# ---------------------------------------------------------------------------
# 2. Log-when-unavailable-once for repeated failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_failures_warn_once_per_interval(hass, caplog):
    """Prevents log spam: repeated Dashboard failures warn once, then only DEBUG.

    After the refresh interval has passed a new WARNING is allowed again, so a
    long outage still surfaces periodically.
    """
    coord = _coordinator(hass)
    coord.dashboard_client = _dashboard(
        async_get_capacity_protection=SmappeeConnectionError("capacity down")
    )

    levels = []
    for now in (10_000.0, 10_000.0 + 60, 10_000.0 + _INTERVAL + 1):
        caplog.clear()
        with (
            patch(f"{_MODULE}._now", return_value=now),
            caplog.at_level(logging.DEBUG, logger=_MODULE),
        ):
            await coord._maybe_refresh_dashboard_data(coord.data, force=True)
        records = _refresh_log_records(caplog)
        assert len(records) == 1
        assert "capacity down" in records[0].getMessage()
        levels.append(records[0].levelno)

    assert levels == [logging.WARNING, logging.DEBUG, logging.WARNING]


# ---------------------------------------------------------------------------
# 3. Throttling and concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_refreshes_fetch_dashboard_once(hass):
    """Prevents two overlapping refreshes from doubling the Dashboard request load."""
    coord = _coordinator(hass)
    release = asyncio.Event()

    async def _slow_details(_serial):
        await release.wait()
        return {"available": True}

    dashboard = _dashboard()
    dashboard.async_get_charging_station_details = AsyncMock(side_effect=_slow_details)
    coord.dashboard_client = dashboard

    first = asyncio.ensure_future(coord._maybe_refresh_dashboard_data(coord.data, force=True))
    await asyncio.sleep(0)
    assert coord._dashboard_refresh_lock.locked()

    second = await coord._maybe_refresh_dashboard_data(coord.data, force=True)
    release.set()
    await first

    assert second is False
    assert first.result() is True
    assert dashboard.async_get_charging_station_details.await_count == 1


@pytest.mark.parametrize(
    ("elapsed", "force", "expected_fetches"),
    [
        pytest.param(60, False, 1, id="within-interval-throttled"),
        pytest.param(60, True, 2, id="within-interval-forced"),
        pytest.param(_INTERVAL + 1, False, 2, id="after-interval"),
    ],
)
@pytest.mark.asyncio
async def test_refresh_is_throttled_to_interval_unless_forced(
    hass, elapsed, force, expected_fetches
):
    """Prevents every 60 s poll from hitting the slow Dashboard API; writes may force it."""
    coord = _coordinator(hass)
    dashboard = _dashboard()
    coord.dashboard_client = dashboard

    with patch(f"{_MODULE}._now", return_value=10_000.0):
        await coord._maybe_refresh_dashboard_data(coord.data)
    with patch(f"{_MODULE}._now", return_value=10_000.0 + elapsed):
        await coord._maybe_refresh_dashboard_data(coord.data, force=force)

    assert dashboard.async_get_charging_station_details.await_count == expected_fetches


@pytest.mark.asyncio
async def test_no_dashboard_io_while_stopping(hass):
    """Prevents Dashboard requests from starting during Home Assistant shutdown."""
    coord = _coordinator(hass)
    dashboard = _dashboard()
    coord.dashboard_client = dashboard
    coord.cancel_delayed_refreshes()

    assert await coord._maybe_refresh_dashboard_data(coord.data, force=True) is False
    dashboard.async_get_charging_station_details.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. Matching Dashboard modules to connectors
# ---------------------------------------------------------------------------


def _carcharger_module(position, channel_name=None) -> dict:
    smart_device = {
        "id": "dash-device",
        "name": "Cable",
        "type": {"category": "CARCHARGER"},
    }
    if channel_name is not None:
        smart_device["carCharger"] = {"chargingStateUpdateChannel": {"name": channel_name}}
    module = {"smartDevice": smart_device}
    if position is not None:
        module["position"] = position
    return module


@pytest.mark.parametrize(
    ("module", "expected_conn"),
    [
        pytest.param(_carcharger_module(2), CONN_2, id="position-2"),
        pytest.param(_carcharger_module("1"), CONN_1, id="position-as-string"),
        pytest.param(
            _carcharger_module(2, channel_name="servicelocation/200/state"),
            CONN_2,
            id="channel-without-device-falls-back-to-position",
        ),
        pytest.param(
            _carcharger_module(9, channel_name=f"servicelocation/200/devices/{CONN_1}/state"),
            CONN_1,
            id="channel-device-uuid-wins-over-position",
        ),
        pytest.param(_carcharger_module(3), None, id="unknown-position"),
        pytest.param(_carcharger_module(None), None, id="no-position"),
    ],
)
def test_dashboard_module_is_matched_to_connector_by_uuid_then_position(
    hass, module, expected_conn
):
    """Prevents Dashboard settings landing on the wrong connector of a two-connector station.

    Without a UUID match the module's ``position`` must select the connector
    with that connector_number; an unknown position must change nothing.
    """
    coord = _coordinator(hass)

    changed = coord._merge_dashboard_module(coord.data, module)

    assert changed is (expected_conn is not None)
    for uuid, conn in coord.data.connectors.items():
        expected_id = "dash-device" if uuid == expected_conn else None
        assert conn.dashboard_device_id == expected_id


def test_malformed_dashboard_modules_do_not_block_valid_ones(hass):
    """Prevents one malformed Dashboard module/property from dropping the LED brightness."""
    coord = _coordinator(hass)
    details = {
        "modules": [
            "garbage",
            {"smartDevice": None},
            {
                "smartDevice": {
                    "id": "led-device",
                    "type": {"category": "LED"},
                    "configurationProperties": [
                        "garbage",
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.led.config.brightness"
                            },
                            "values": [{"Integer": 55}],
                        },
                    ],
                }
            },
        ]
    }

    coord._merge_dashboard_station_details(coord.data, details)

    assert coord.data.station.dashboard_led_device_id == "led-device"
    assert coord.data.station.led_brightness == 55


# ---------------------------------------------------------------------------
# 5. Scheduling refreshes after writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_with_zero_delay_refreshes_immediately(hass):
    """Prevents a zero-delay refresh request from silently waiting for a timer."""
    coord = _coordinator(hass)
    coord.async_request_refresh = AsyncMock()

    async def _fake_refresh(staged, force=False):
        assert force is True
        staged.station.led_brightness = 70
        return True

    coord._maybe_refresh_dashboard_data = _fake_refresh

    with patch(f"{_MODULE}.async_call_later") as call_later:
        coord.async_schedule_dashboard_refresh(delay=0)
    call_later.assert_not_called()
    task = coord._dashboard_refresh_task
    assert task is not None
    await task

    assert coord.data.station.led_brightness == 70
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_refresh_cancels_running_one_and_only_newest_applies(hass):
    """Prevents a slow, superseded Dashboard refresh from overwriting a newer one."""
    coord = _coordinator(hass)
    coord.async_request_refresh = AsyncMock()
    never = asyncio.Event()
    calls = 0

    async def _fake_refresh(staged, force=False):
        nonlocal calls
        calls += 1
        if calls == 1:
            staged.station.led_brightness = 10
            await never.wait()
        staged.station.led_brightness = 90
        return True

    coord._maybe_refresh_dashboard_data = _fake_refresh

    coord.async_schedule_dashboard_refresh(delay=0)
    first = coord._dashboard_refresh_task
    await asyncio.sleep(0)
    assert calls == 1

    coord.async_schedule_dashboard_refresh(delay=0)
    second = coord._dashboard_refresh_task
    assert second is not first
    await second
    with pytest.raises(asyncio.CancelledError):
        await first

    assert first.cancelled()
    assert coord.data.station.led_brightness == 90
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_delayed_refresh_timer_firing_during_shutdown_starts_nothing(hass):
    """Prevents a write-triggered timer that fires as HA stops from starting Dashboard I/O."""
    coord = _coordinator(hass)
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=True)

    with patch(f"{_MODULE}.async_call_later") as call_later:
        coord.async_schedule_dashboard_refresh(delay=30)
    callback = call_later.call_args.args[2]

    coord._shutting_down = True
    await callback(None)

    assert coord._dashboard_refresh_task is None
    assert coord._dashboard_refresh_unsub is None
    coord._maybe_refresh_dashboard_data.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_before_first_data_is_a_no_op(hass):
    """Prevents a write issued before the first coordinator update from crashing the refresh."""
    coord = _coordinator(hass)
    coord.data = None
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=True)
    coord.async_request_refresh = AsyncMock()

    coord.async_schedule_dashboard_refresh(delay=0)
    await coord._dashboard_refresh_task

    coord._maybe_refresh_dashboard_data.assert_not_called()
    coord.async_request_refresh.assert_not_awaited()
