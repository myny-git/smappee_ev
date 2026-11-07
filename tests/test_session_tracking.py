"""Final session deadlines stay stable and belong to their own connector."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.smappee_ev.coordinators.session_tracking import (
    SESSION_EXPLICIT_MIN_REFRESH_INTERVAL,
    SESSION_FINAL_REFRESH_DELAYS,
)
from custom_components.smappee_ev.sensor import ConnectorSessionEnergySensor
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


def _session_entity(online):
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    client = bucket.connectors["connector-1"].connector_client
    return coord, ConnectorSessionEnergySensor(coord, client, 1, "station-1", "connector-1")


@pytest.mark.parametrize("failure", [False, True])
async def test_explicit_session_refresh_limits_attempts_including_failures(
    online, monkeypatch, failure
):
    coord, entity = _session_entity(online)
    clock = MagicMock(return_value=100.0)
    monkeypatch.setattr("custom_components.smappee_ev.coordinators.session_tracking._now", clock)
    query = AsyncMock(return_value=[])
    if failure:
        query.side_effect = RuntimeError("offline")
    monkeypatch.setattr(coord, "_async_get_recent_sessions", query)
    await entity.async_update()
    clock.return_value = 100 + SESSION_EXPLICIT_MIN_REFRESH_INTERVAL - 1
    for _ in range(5):
        await entity.async_update()
    assert query.await_count == 1
    clock.return_value = 100 + SESSION_EXPLICIT_MIN_REFRESH_INTERVAL
    await entity.async_update()
    assert query.await_count == 2
    # Lifecycle-driven final refreshes still bypass the ordinary two-minute throttle.
    await coord._async_refresh_recent_sessions("finalizing", force=True)
    assert query.await_count == 3


async def test_concurrent_explicit_updates_wait_for_one_scheduled_query(online, monkeypatch):
    coord, entity = _session_entity(online)
    started, release = asyncio.Event(), asyncio.Event()

    async def query():
        started.set()
        await release.wait()
        return [{"connectorUuid": "connector-1", "energy": 2.5}]

    fetch = AsyncMock(side_effect=query)
    monkeypatch.setattr(coord, "_async_get_recent_sessions", fetch)
    scheduled = asyncio.create_task(coord._async_refresh_recent_sessions("active session interval"))
    await started.wait()
    callers = [asyncio.create_task(entity.async_update()) for _ in range(3)]
    await asyncio.sleep(0)
    assert all(not task.done() for task in callers)
    release.set()
    await asyncio.gather(scheduled, *callers)
    fetch.assert_awaited_once()
    assert entity.native_value == 2.5


async def test_cancelled_waiter_does_not_cancel_shared_session_query(online, monkeypatch):
    coord, entity = _session_entity(online)
    started, release = asyncio.Event(), asyncio.Event()

    async def query():
        started.set()
        await release.wait()
        return [{"connectorUuid": "connector-1", "energy": 3.5}]

    fetch = AsyncMock(side_effect=query)
    monkeypatch.setattr(coord, "_async_get_recent_sessions", fetch)
    first = asyncio.create_task(entity.async_update())
    await started.wait()
    second = asyncio.create_task(entity.async_update())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, timeout=1)
    assert not coord._session_refresh_task.cancelled()
    release.set()
    await asyncio.wait_for(second, timeout=1)
    fetch.assert_awaited_once()
    assert entity.native_value == 3.5


async def test_shutdown_cancels_shared_session_query_and_blocks_new_requests(online, monkeypatch):
    coord, entity = _session_entity(online)
    started = asyncio.Event()

    async def query():
        started.set()
        await asyncio.Event().wait()

    fetch = AsyncMock(side_effect=query)
    monkeypatch.setattr(coord, "_async_get_recent_sessions", fetch)
    caller = asyncio.create_task(entity.async_update())
    await started.wait()
    shared = coord._session_refresh_task
    await coord.async_shutdown()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(caller, timeout=1)
    assert shared.cancelled()
    await entity.async_update()
    fetch.assert_awaited_once()


async def test_monitoring_only_session_update_never_calls_dashboard(online, monkeypatch):
    coord, entity = _session_entity(online)
    coord.monitoring_only = True
    fetch = AsyncMock()
    monkeypatch.setattr(coord, "_async_get_recent_sessions", fetch)
    await entity.async_update()
    fetch.assert_not_awaited()


@pytest.fixture
def tracking(online, monkeypatch):
    coord = online.sites[1].stations["station-1"].station_coordinator
    coord._session_tracking_started = True
    coord.data.connectors["connector-1"].session_state = "Started"
    coord.data.connectors["connector-2"] = replace(
        coord.data.connectors["connector-1"], connector_number=2
    )
    coord._async_refresh_recent_sessions = AsyncMock()
    scheduled = []

    def schedule(_hass, delay, callback):
        timer = {"delay": delay, "callback": callback, "cancel": MagicMock()}
        scheduled.append(timer)
        return timer["cancel"]

    monkeypatch.setattr(
        "custom_components.smappee_ev.coordinators.session_tracking.async_call_later", schedule
    )
    monkeypatch.setattr(
        "custom_components.smappee_ev.coordinators.session_tracking.async_track_time_interval",
        MagicMock(return_value=MagicMock()),
    )

    def send(uuid, state):
        coord.apply_mqtt_properties(
            f"servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/{uuid}/property/chargingstate",
            {"chargingState": state},
        )

    return coord, scheduled, send


async def test_repeated_finished_messages_do_not_postpone_or_restart_final_refreshes(tracking):
    coord, timers, send = tracking
    send("connector-1", "Stopped")
    originals = list(timers)
    assert [timer["delay"] for timer in originals] == list(SESSION_FINAL_REFRESH_DELAYS)
    for _ in range(3):
        send("connector-1", "Stopped")
        send("connector-2", "Started")
    assert timers == originals
    assert all(not timer["cancel"].called for timer in originals)
    for timer in originals:
        await timer["callback"](None)
    assert coord._async_refresh_recent_sessions.await_count == len(SESSION_FINAL_REFRESH_DELAYS)
    assert all(
        call.kwargs == {"force": True}
        for call in coord._async_refresh_recent_sessions.await_args_list
    )
    assert coord._session_final_refresh_unsubs == {}
    with patch(
        "custom_components.smappee_ev.coordinators.session_tracking.async_call_later"
    ) as schedule:
        send("connector-1", "Stopped")
    schedule.assert_not_called()


async def test_new_session_cancels_only_its_own_old_deadlines(tracking):
    coord, timers, send = tracking
    send("connector-1", "Stopped")
    first = list(timers)
    send("connector-2", "Stopped")
    second = timers[len(first) :]
    assert len(second) == len(SESSION_FINAL_REFRESH_DELAYS)
    assert all(not timer["cancel"].called for timer in first + second)
    send("connector-1", "Started")
    assert all(timer["cancel"].called for timer in first)
    assert all(not timer["cancel"].called for timer in second)
    # A cancelled callback may already be queued; it must not refresh the new session.
    for timer in first:
        await timer["callback"](None)
    coord._async_refresh_recent_sessions.assert_not_awaited()
    offset = len(timers)
    send("connector-1", "Stopped")
    new_first = timers[offset:]
    assert [timer["delay"] for timer in new_first] == list(SESSION_FINAL_REFRESH_DELAYS)
    await new_first[0]["callback"](None)
    coord._async_refresh_recent_sessions.assert_awaited_once()
    assert all(not timer["cancel"].called for timer in second)


async def test_shutdown_cancels_all_connectors_and_queued_callbacks(tracking):
    coord, timers, send = tracking
    send("connector-1", "Stopped")
    send("connector-2", "Stopped")
    await coord.async_shutdown()
    assert all(timer["cancel"].called for timer in timers)
    for timer in timers:
        await timer["callback"](None)
    coord._async_refresh_recent_sessions.assert_not_awaited()
    assert coord._session_final_refresh_unsubs == {}
    assert not coord._session_final_refresh_scheduled
    send("connector-1", "Stopped")
    assert len(timers) == 2 * len(SESSION_FINAL_REFRESH_DELAYS)
