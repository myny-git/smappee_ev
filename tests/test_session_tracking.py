"""Final session deadlines stay stable and belong to their own connector."""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev.coordinators.session_tracking import SESSION_FINAL_REFRESH_DELAYS
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


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
    send("connector-1", "Stopped")
    assert timers == originals


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
