"""Exercise real entity writes while polling replaces the coordinator snapshot."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
import pytest

from custom_components.smappee_ev import light, number, select, switch
from custom_components.smappee_ev.api.errors import SmappeeAuthenticationError
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


def make_data():
    return IntegrationData(
        station=StationState(
            capacity_maximum_power_kw=2.0,
            overload_maximum_load_a=16,
            offline_charging_enabled=False,
            offline_failsafe_current_a=3,
            available=False,
            led_brightness=10,
        ),
        connectors={
            "c": ConnectorState(
                connector_number=1,
                selected_current_limit=8,
                selected_percentage_limit=8,
                selected_mode="standard",
                max_current=32,
                min_surpluspct=20,
                power_total=100,
            )
        },
    )


def make_entity(kind, coord, api):
    station = {"coordinator": coord, "api_client": api, "sid": 1, "station_uuid": "station"}
    connector = {**station, "connector_uuid": "c"}
    cases = {
        "current": (
            number.SmappeeCombinedCurrentSlider,
            "set_current",
            "selected_current_limit",
            20,
        ),
        "maximum": (
            number.SmappeeConnectorMaxCurrentNumber,
            "set_connector_max_current",
            "max_current",
            16,
        ),
        "surplus": (number.SmappeeMinSurplusPctNumber, "set_min_surpluspct", "min_surpluspct", 40),
        "mode": (select.SmappeeModeSelect, "set_charging_mode", "selected_mode", "solar"),
        "capacity": (
            number.SmappeeCapacityMaximumPowerNumber,
            "async_set_capacity_protection",
            "capacity_maximum_power_kw",
            4,
        ),
        "overload": (
            number.SmappeeOverloadMaximumLoadNumber,
            "async_set_overload_protection",
            "overload_maximum_load_a",
            20,
        ),
        "failsafe": (
            number.SmappeeOfflineFailsafeCurrentNumber,
            "set_offline_charging_config",
            "offline_failsafe_current_a",
            6,
        ),
        "availability": (switch.SmappeeAvailabilitySwitch, "set_available", "available", True),
        "offline": (
            switch.SmappeeOfflineChargingSwitch,
            "set_offline_charging_config",
            "offline_charging_enabled",
            True,
        ),
        "light": (light.SmappeeLedLight, "set_brightness", "led_brightness", 75),
    }
    cls, method, field, value = cases[kind]
    if kind in {"capacity", "overload"}:
        entity = cls(coordinator=coord, sid=1)
    elif kind in {"current", "maximum", "surplus", "mode"}:
        entity = cls(**connector)
    else:
        entity = cls(**station)
    entity.async_write_ha_state = MagicMock()
    if kind == "mode":

        def action():
            return entity.async_select_option(value)
    elif kind in {"availability", "offline"}:
        action = entity.async_turn_on
    elif kind == "light":

        def action():
            return entity._set_brightness(value)
    else:

        def action():
            return entity.async_set_native_value(value)

    return action, method, field, value, kind in {"current", "maximum", "surplus", "mode"}


@pytest.mark.parametrize(
    "kind",
    [
        "current",
        "maximum",
        "surplus",
        "mode",
        "capacity",
        "overload",
        "failsafe",
        "availability",
        "offline",
        "light",
    ],
)
@pytest.mark.parametrize("outcome", ["success", "error", "auth", "cancel"])
async def test_pending_controls_preserve_current_snapshot(kind, outcome):
    coord = MagicMock(spec=SmappeeCoordinator)
    coord.data = initial = make_data()
    coord.async_set_updated_data.side_effect = lambda data: setattr(coord, "data", data)
    api = MagicMock()
    coord.dashboard_client = api
    action, method, field, target, connector = make_entity(kind, coord, api)
    started, finish = asyncio.Event(), asyncio.Event()

    async def remote_write(*args, **kwargs):
        started.set()
        await finish.wait()
        if outcome == "error":
            raise RuntimeError("uncertain response")
        if outcome == "auth":
            raise SmappeeAuthenticationError("reauth required")
        return (20.0, 54) if kind == "current" else None

    setattr(api, method, AsyncMock(side_effect=remote_write))
    task = asyncio.create_task(action())
    try:
        await asyncio.wait_for(started.wait(), 1)
        coord.async_set_updated_data.assert_not_called()
        current = replace(
            initial,
            station=replace(initial.station),
            connectors={"c": replace(initial.connectors["c"], power_total=3200)},
            recent_sessions=[{"energy": 12.5}],
        )
        previous = getattr(current.connectors["c"] if connector else current.station, field)
        coord.data = current
        if outcome == "cancel":
            task.cancel()
        else:
            finish.set()
        if outcome == "success":
            await task
        else:
            expected = {"auth": ConfigEntryAuthFailed, "cancel": asyncio.CancelledError}.get(
                outcome, (RuntimeError, HomeAssistantError)
            )
            with pytest.raises(expected):
                await task
        assert coord.data is current
        assert current.connectors["c"].power_total == 3200
        assert current.recent_sessions == [{"energy": 12.5}]
        assert getattr(current.connectors["c"] if connector else current.station, field) == (
            target if outcome == "success" else previous
        )
        assert getattr(initial.connectors["c"] if connector else initial.station, field) == previous
        assert coord.async_schedule_dashboard_refresh.call_count == (outcome != "auth")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_rest_refresh_preserves_mqtt_sessions_and_new_commands(online):
    coord = online.sites[1].stations["station-1"].station_coordinator
    uuid = "connector-1"
    coord.data.connectors[uuid].power_total = 100
    coord.data.connectors[uuid].selected_current_limit = 8
    coord._fetch_station_state = AsyncMock(return_value=StationState())
    coord._fetch_connector_state = AsyncMock(
        return_value=ConnectorState(
            connector_number=1,
            selected_percentage_limit=8,
        )
    )
    coord._ensure_power_index_map = AsyncMock()
    started, finish = asyncio.Event(), asyncio.Event()

    async def dashboard(staged):
        staged.station.capacity_maximum_power_kw = 4.0
        started.set()
        await finish.wait()
        return True

    coord._maybe_refresh_dashboard_data = dashboard
    task = asyncio.create_task(coord._async_fetch_data())
    try:
        await asyncio.wait_for(started.wait(), 1)
        coord.apply_mqtt_properties(bootstrap.TOPIC, {"activePowerData": [0, 3200]})
        current = replace(coord.data, recent_sessions=[{"energy": 12.5}])
        current.connectors[uuid].selected_current_limit = 20
        current.connectors[uuid].selected_percentage_limit = 54
        coord.async_set_updated_data(current)
        finish.set()
        result = await task
        assert result.connectors[uuid].power_total == 3200
        assert result.connectors[uuid].selected_current_limit == 20
        assert result.connectors[uuid].selected_percentage_limit == 54
        assert result.recent_sessions == [{"energy": 12.5}]
        assert result.station.capacity_maximum_power_kw == 4.0
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_sessions_share_one_station_request(online):
    coord = online.sites[1].stations["station-1"].station_coordinator
    first = coord.connector_clients["connector-1"]
    second = MagicMock()
    second.charging_station_serial = first.charging_station_serial
    first.async_get_recent_sessions = AsyncMock(return_value=[{"energy": 1.5}])
    second.async_get_recent_sessions = AsyncMock()
    coord.connector_clients["second"] = second
    assert await coord._async_get_recent_sessions() == [{"energy": 1.5}]
    first.async_get_recent_sessions.assert_awaited_once()
    second.async_get_recent_sessions.assert_not_awaited()
    assert coord._connector_session_available == {"connector-1": True, "second": True}


async def test_forced_dashboard_refresh_preserves_in_flight_commands(online):
    coord = online.sites[1].stations["station-1"].station_coordinator
    coord.data.station.capacity_maximum_power_kw = 2.0
    initial = coord.data
    coord.async_request_refresh = AsyncMock()
    started, finish = asyncio.Event(), asyncio.Event()

    async def dashboard(staged, force=False):
        assert force
        staged.station.capacity_maximum_power_kw = 3.0
        started.set()
        await finish.wait()
        return True

    coord._maybe_refresh_dashboard_data = dashboard
    task = asyncio.create_task(coord._async_dashboard_refresh_now())
    try:
        await asyncio.wait_for(started.wait(), 1)
        current = replace(
            initial,
            station=replace(initial.station, capacity_maximum_power_kw=4.0),
            connectors={
                uuid: replace(connector, power_total=3200)
                for uuid, connector in initial.connectors.items()
            },
            recent_sessions=[{"energy": 12.5}],
        )
        coord.async_set_updated_data(current)
        finish.set()
        await task
        assert coord.data is current
        assert current.station.capacity_maximum_power_kw == 4.0
        assert current.connectors["connector-1"].power_total == 3200
        assert current.recent_sessions == [{"energy": 12.5}]
        assert initial.station.capacity_maximum_power_kw == 2.0
        coord.async_request_refresh.assert_awaited_once()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
