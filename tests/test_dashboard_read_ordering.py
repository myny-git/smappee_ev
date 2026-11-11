"""The LED read barrier is local to older Dashboard operations and valid REST reads."""

import asyncio
from time import monotonic
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.smappee_ev.light import SmappeeLedLight
from custom_components.smappee_ev.models.state import StationState
from tests import test_reconciliation_audit as audit

entry = audit.entry
online = audit.online
coord = audit.coord


async def _start_dashboard(coord, brightness=50, *, owned=False):
    started, finish = asyncio.Event(), asyncio.Event()

    async def remote(staged, force=False):
        if not force:
            return False
        staged.station.led_brightness = brightness
        staged.station.capacity_maximum_power_kw = 4.0
        staged.connectors["connector-1"].min_surpluspct = 60
        started.set()
        await finish.wait()
        return True

    coord._maybe_refresh_dashboard_data = remote
    if owned:
        coord._start_dashboard_refresh_task()
        task = coord._dashboard_refresh_task
    else:
        task = asyncio.create_task(coord._async_dashboard_refresh_now())
    await asyncio.wait_for(started.wait(), 1)
    return task, finish


async def _complete(task, finish):
    finish.set()
    await asyncio.wait_for(task, 1)


@pytest.mark.parametrize("observed", [10, 75, 0])
async def test_successful_newer_rest_protects_only_led_brightness(coord, observed):
    coord.data.connectors["connector-1"].min_surpluspct = 20
    task, finish = await _start_dashboard(coord)
    try:
        coord._fetch_station_state.return_value = StationState(led_brightness=observed)
        version = coord._state_confirmations.version
        await coord.async_refresh()
        await _complete(task, finish)
        assert coord.data.station.led_brightness == observed
        assert coord.data.station.capacity_maximum_power_kw == 4
        assert coord.data.connectors["connector-1"].min_surpluspct == 60
        assert coord._state_confirmations.version == version
        assert not coord._dashboard_rest_read_guards
    finally:
        await audit._finish(task)


async def test_newer_rest_connector_changes_still_need_dashboard_snapshot_replay(coord):
    task, finish = await _start_dashboard(coord)
    try:
        await coord.async_refresh()
        assert coord.data.connectors["connector-1"].min_surpluspct == 20
        await _complete(task, finish)
        assert coord.data.connectors["connector-1"].min_surpluspct == 20
    finally:
        await audit._finish(task)


async def test_connector_failure_does_not_invalidate_a_successful_station_led_observation(coord):
    task, finish = await _start_dashboard(coord)
    try:
        coord._fetch_connector_state.side_effect = RuntimeError("connector unavailable")
        await coord.async_refresh()
        assert not coord.data.connectors["connector-1"].api_available
        await _complete(task, finish)
        assert coord.data.station.led_brightness == 10
    finally:
        await audit._finish(task)


@pytest.mark.parametrize("outcome", ["error", "late_error", "unavailable", "missing"])
async def test_failed_or_missing_newer_led_observation_does_not_discard_dashboard(coord, outcome):
    task, finish = await _start_dashboard(coord)
    try:
        if outcome == "error":
            coord._fetch_station_state.side_effect = UpdateFailed("REST failed")
        elif outcome == "late_error":
            coord._maybe_refresh_dashboard_data = AsyncMock(
                side_effect=UpdateFailed("merge failed")
            )
        else:
            coord._fetch_station_state.return_value = StationState(
                led_brightness=10 if outcome == "unavailable" else None,
                api_available=outcome != "unavailable",
            )
        await coord.async_refresh()
        await _complete(task, finish)
        assert coord.data.station.led_brightness == 50
        assert coord.data.station.capacity_maximum_power_kw == 4
        if outcome in {"error", "late_error"}:
            assert not coord.last_update_success
        assert not coord._dashboard_rest_read_guards
    finally:
        await audit._finish(task)


@pytest.mark.parametrize("phase", ["station", "connector"])
async def test_cancelled_newer_poll_does_not_discard_dashboard(coord, phase):
    task, finish = await _start_dashboard(coord)
    started = asyncio.Event()

    async def blocked(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    target = coord._fetch_station_state if phase == "station" else coord._fetch_connector_state
    target.side_effect = blocked
    poll = asyncio.create_task(coord.async_refresh())
    try:
        await asyncio.wait_for(started.wait(), 1)
        poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await poll
        await _complete(task, finish)
        assert coord.data.station.led_brightness == 50
        assert not coord.last_update_success
        assert not coord._dashboard_rest_read_guards
    finally:
        await audit._finish(poll)
        await audit._finish(task)


@pytest.mark.parametrize("writer", ["mqtt", "command"])
@pytest.mark.parametrize("moment", ["before_rest_finishes", "after_rest_finishes"])
@pytest.mark.parametrize("repeat", [False, True])
async def test_live_led_writes_still_win_during_both_reads(coord, writer, moment, repeat):
    task, finish = await _start_dashboard(coord)
    started, release = asyncio.Event(), asyncio.Event()
    response = coord._fetch_connector_state.return_value

    async def blocked(*_args, **_kwargs):
        started.set()
        await release.wait()
        return response

    coord._fetch_station_state.return_value = StationState(led_brightness=75)
    coord._fetch_connector_state.side_effect = blocked
    poll = asyncio.create_task(coord.async_refresh())
    try:
        await asyncio.wait_for(started.wait(), 1)
        if moment == "after_rest_finishes":
            release.set()
            await asyncio.wait_for(poll, 1)
        value = coord.data.station.led_brightness if repeat else 30
        if writer == "mqtt":
            coord.apply_mqtt_properties(
                "servicelocation/site-uuid/etc/chargingstation/acchargingstation/v1/"
                "station-1/properties",
                {"ledBrightness": value},
            )
        else:
            api = MagicMock(set_brightness=AsyncMock())
            light = SmappeeLedLight(
                coordinator=coord, api_client=api, sid=1, station_uuid="station-1"
            )
            await light._set_brightness(value)
            api.set_brightness.assert_awaited_once_with(value)
        release.set()
        await asyncio.wait_for(poll, 1)
        await _complete(task, finish)
        assert coord.data.station.led_brightness == value
        assert not coord._dashboard_rest_read_guards
    finally:
        release.set()
        await audit._finish(poll)
        await audit._finish(task)


@pytest.mark.parametrize("completion", ["dashboard_first", "rest_first"])
async def test_dashboard_started_after_an_older_rest_read_is_not_invalidated(coord, completion):
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(*_args, **_kwargs):
        started.set()
        await release.wait()
        return StationState(led_brightness=10)

    coord._fetch_station_state.side_effect = blocked
    poll = asyncio.create_task(coord.async_refresh())
    await asyncio.wait_for(started.wait(), 1)
    task, finish = await _start_dashboard(coord, 75)
    try:
        if completion == "rest_first":
            release.set()
            await asyncio.wait_for(poll, 1)
        await _complete(task, finish)
        release.set()
        await asyncio.wait_for(poll, 1)
        assert coord.data.station.led_brightness == 75
        assert not coord._dashboard_rest_read_guards
    finally:
        release.set()
        await audit._finish(poll)
        await audit._finish(task)


@pytest.mark.parametrize("completion", ["older_first", "newer_first"])
async def test_multiple_dashboard_reads_have_independent_overlap_guards(coord, completion):
    older, older_finish = await _start_dashboard(coord, 50)
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(*_args, **_kwargs):
        started.set()
        await release.wait()
        return StationState(led_brightness=10)

    coord._fetch_station_state.side_effect = blocked
    poll = asyncio.create_task(coord.async_refresh())
    await asyncio.wait_for(started.wait(), 1)
    newer, newer_finish = await _start_dashboard(coord, 75)
    try:
        release.set()
        await asyncio.wait_for(poll, 1)
        order = [(older, older_finish), (newer, newer_finish)]
        if completion == "newer_first":
            order.reverse()
        for task, finish in order:
            await _complete(task, finish)
        assert coord.data.station.led_brightness == 75
        assert not coord._dashboard_rest_read_guards
    finally:
        release.set()
        await audit._finish(poll)
        await audit._finish(older)
        await audit._finish(newer)


async def test_failed_second_poll_does_not_invalidate_a_dashboard_newer_than_the_first_poll(coord):
    older, older_finish = await _start_dashboard(coord, 50)
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(*_args, **_kwargs):
        started.set()
        await release.wait()
        return StationState(led_brightness=10)

    coord._fetch_station_state.side_effect = blocked
    poll = asyncio.create_task(coord.async_refresh())
    await asyncio.wait_for(started.wait(), 1)
    newer, newer_finish = await _start_dashboard(coord, 75)
    try:
        release.set()
        await asyncio.wait_for(poll, 1)
        coord._force_smartdevice_refresh = True
        coord._fetch_station_state.side_effect = UpdateFailed("second poll failed")
        await coord.async_refresh()
        await _complete(newer, newer_finish)
        await _complete(older, older_finish)
        assert coord.data.station.led_brightness == 75
        assert not coord.last_update_success
        assert not coord._dashboard_rest_read_guards
    finally:
        release.set()
        await audit._finish(poll)
        await audit._finish(older)
        await audit._finish(newer)


async def test_local_tick_is_not_an_observation_and_guards_are_not_sticky(coord):
    task, finish = await _start_dashboard(coord, 50)
    try:
        coord._last_smartdevice_refresh = monotonic()
        await coord.async_refresh()
        coord._fetch_station_state.assert_not_awaited()
        await _complete(task, finish)
        assert coord.data.station.led_brightness == 50
        coord._force_smartdevice_refresh = True
        await coord.async_refresh()
        assert coord.data.station.led_brightness == 10
        next_task, next_finish = await _start_dashboard(coord, 60)
        try:
            await _complete(next_task, next_finish)
            assert coord.data.station.led_brightness == 60
            assert not coord._dashboard_rest_read_guards
        finally:
            await audit._finish(next_task)
    finally:
        await audit._finish(task)


@pytest.mark.parametrize("boundary", ["cancel", "shutdown"])
async def test_dashboard_cancellation_cleans_the_guard_while_rest_is_in_flight(coord, boundary):
    task, finish = await _start_dashboard(coord, owned=True)
    started, release = asyncio.Event(), asyncio.Event()
    response = coord._fetch_connector_state.return_value

    async def blocked(*_args, **_kwargs):
        started.set()
        await release.wait()
        return response

    coord._fetch_connector_state.side_effect = blocked
    poll = asyncio.create_task(coord.async_refresh())
    try:
        await asyncio.wait_for(started.wait(), 1)
        if boundary == "shutdown":
            coord.cancel_delayed_refreshes()
        else:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not coord._dashboard_rest_read_guards
        release.set()
        await asyncio.wait_for(poll, 1)
        assert not coord._dashboard_rest_read_guards
        assert coord.data.station.capacity_maximum_power_kw is None
        coord.async_request_refresh.assert_not_awaited()
    finally:
        release.set()
        await audit._finish(poll)
        await audit._finish(task)
