"""The intended contract between MQTT, REST polls, Dashboard refreshes and commands.

Each test states the outcome the integration should produce and why.

Already covered elsewhere, and therefore not repeated here:

* A command that repeats the current value while a poll is in flight
  (``test_stale_rest_current_limit_preserves_confirmation`` with 20 A, and
  ``test_confirmed_cable_lock_survives_older_dashboard_response``).
* MQTT and commands arriving while a poll is in flight
  (``test_rest_refresh_preserves_mqtt_sessions_and_new_commands``).
* Live MQTT charger state against a lagging poll (``test_session_transitions``).
"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.smappee_ev import number
from custom_components.smappee_ev.models.state import ConnectorState, StationState
from custom_components.smappee_ev.mqtt_setup import _handle_mqtt_connection_change
from custom_components.smappee_ev.state_updates import update_connector, update_station
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online

UUID = "connector-1"


def _coordinator(online):
    coord = online.sites[1].stations["station-1"].station_coordinator
    coord._ensure_power_index_map = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    coord.async_schedule_dashboard_refresh = MagicMock()
    return coord


async def _finish(task):
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_poll_started_after_a_command_is_authoritative(online):
    """A confirmation protects against older requests only; it is not a lock.

    Intended outcome: a poll that *starts after* a command reports what the
    cloud holds at that moment, and that value wins, even when it differs from
    the value the command just set. Otherwise a change made outside Home
    Assistant (the Smappee app, a schedule) right after a command could never
    be adopted. The command and the poll talk to the same cloud API, so the
    poll is assumed to read the command's own write.

    If the cloud turns out to answer with pre-command values for a while, this
    is the contract to change: a time-limited hold, never a permanent one.
    """
    coord = _coordinator(online)
    initial = ConnectorState(
        connector_number=1,
        min_current=8,
        max_current=32,
        selected_current_limit=8,
        selected_percentage_limit=0,
        optimization_strategy="NONE",
    )
    coord.data.connectors[UUID] = initial
    coord._fetch_station_state = AsyncMock(return_value=StationState())
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=False)
    api = MagicMock()
    api.set_current = AsyncMock(return_value=(20.0, 50))
    slider = number.SmappeeCombinedCurrentSlider(
        coordinator=coord, api_client=api, sid=1, station_uuid="station-1", connector_uuid=UUID
    )

    await slider.async_set_native_value(20)
    assert coord.data.connectors[UUID].selected_current_limit == 20

    coord._fetch_connector_state = AsyncMock(
        return_value=replace(initial, selected_current_limit=None, selected_percentage_limit=25)
    )
    result = await coord._async_fetch_data()

    assert result.connectors[UUID].selected_percentage_limit == 25
    assert result.connectors[UUID].selected_current_limit == 14


async def test_dashboard_refresh_that_started_later_wins_over_an_older_poll(online):
    """Two cloud reads overlap on one field: the read that started last wins.

    The minimum surplus percentage is reported by the smart-device poll and by
    the Dashboard station details. A poll is waiting for an answer that still
    holds 20. A Dashboard refresh starts after it, finishes first and reports
    60. Intended outcome: 60 stays, because the poll describes an older moment.
    """
    coord = _coordinator(online)
    coord.data.connectors[UUID].min_surpluspct = 20
    coord._fetch_station_state = AsyncMock(return_value=StationState())
    started, finish = asyncio.Event(), asyncio.Event()

    async def older_rest_connector(*_args, **_kwargs):
        started.set()
        await finish.wait()
        return ConnectorState(connector_number=1, min_surpluspct=20)

    async def dashboard(staged, force=False):
        if force:
            staged.connectors[UUID].min_surpluspct = 60
        return force

    coord._fetch_connector_state = AsyncMock(side_effect=older_rest_connector)
    coord._maybe_refresh_dashboard_data = dashboard
    poll = asyncio.create_task(coord._async_fetch_data())
    try:
        await asyncio.wait_for(started.wait(), 1)
        await coord._async_dashboard_refresh_now()
        assert coord.data.connectors[UUID].min_surpluspct == 60
        finish.set()
        result = await asyncio.wait_for(poll, 1)
        assert result.connectors[UUID].min_surpluspct == 60
    finally:
        await _finish(poll)


async def test_poll_that_started_later_wins_over_an_older_dashboard_refresh(online):
    """The mirror image: the Dashboard read is the older one.

    A Dashboard refresh is waiting with an outdated surplus percentage of 40.
    A poll starts after it, finishes first and publishes 60. Intended outcome:
    60 stays when the older Dashboard answer finally arrives.
    """
    coord = _coordinator(online)
    coord.data.connectors[UUID].min_surpluspct = 20
    coord._fetch_station_state = AsyncMock(return_value=StationState())
    coord._fetch_connector_state = AsyncMock(
        return_value=ConnectorState(connector_number=1, min_surpluspct=60)
    )
    started, finish = asyncio.Event(), asyncio.Event()

    async def dashboard(staged, force=False):
        if force:
            staged.connectors[UUID].min_surpluspct = 40
            started.set()
            await finish.wait()
        return force

    coord._maybe_refresh_dashboard_data = dashboard
    refresh = asyncio.create_task(coord._async_dashboard_refresh_now())
    try:
        await asyncio.wait_for(started.wait(), 1)
        coord.async_set_updated_data(await coord._async_fetch_data())
        assert coord.data.connectors[UUID].min_surpluspct == 60
        finish.set()
        await asyncio.wait_for(refresh, 1)
        assert coord.data.connectors[UUID].min_surpluspct == 60
    finally:
        await _finish(refresh)


async def test_session_refresh_keeps_the_snapshot_a_poll_published_meanwhile(online):
    """A session query must add its result to the newest snapshot, not its own.

    The session query is waiting for the cloud. A poll publishes a new snapshot
    with replaced connector objects. Intended outcome: the published state has
    the poll's connector objects *and* the new sessions; neither is lost.
    """
    coord = _coordinator(online)
    started, finish = asyncio.Event(), asyncio.Event()

    async def sessions():
        started.set()
        await finish.wait()
        return [{"energy": 12.5}]

    coord._async_get_recent_sessions = sessions
    task = asyncio.create_task(
        coord._async_run_session_refresh("test", force=True, minimum_interval=None)
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        replaced = replace(coord.data.connectors[UUID], session_state="Started", power_total=3200)
        coord.async_set_updated_data(replace(coord.data, connectors={UUID: replaced}))
        finish.set()
        await asyncio.wait_for(task, 1)
        assert coord.data.connectors[UUID] is replaced
        assert coord.data.recent_sessions == [{"energy": 12.5}]
    finally:
        await _finish(task)


@pytest.mark.parametrize("publication", ["command", "station_command", "sessions", "dashboard"])
async def test_local_publication_does_not_report_a_failed_poll_as_recovered(online, publication):
    """Only a successful poll may report the REST connection as recovered.

    The last poll failed, so the coordinator reports failure and its REST-backed
    entities are unavailable. Intended outcome: publishing a confirmed command
    or a new session list updates the values but leaves that failure in place.
    Otherwise entities become available for one tick and unavailable again.
    """
    coord = _coordinator(online)
    coord._async_fetch_data = AsyncMock(side_effect=UpdateFailed("REST is down"))
    await coord.async_refresh()
    assert coord.last_update_success is False

    if publication == "command":
        update_connector(coord, UUID, min_surpluspct=40)
        assert coord.data.connectors[UUID].min_surpluspct == 40
    elif publication == "station_command":
        update_station(coord, led_brightness=75)
        assert coord.data.station.led_brightness == 75
    elif publication == "sessions":
        coord._async_get_recent_sessions = AsyncMock(return_value=[{"energy": 1.5}])
        await coord._async_run_session_refresh("test", force=True, minimum_interval=None)
        assert coord.data.recent_sessions == [{"energy": 1.5}]
    else:

        async def dashboard(staged, force=False):
            staged.station.led_brightness = 75
            return True

        coord._maybe_refresh_dashboard_data = dashboard
        await coord._async_dashboard_refresh_now()
        assert coord.data.station.led_brightness == 75

    assert coord.last_update_success is False


@pytest.mark.parametrize("publication", ["command", "sessions", "site_mqtt"])
async def test_local_publications_notify_without_moving_the_next_poll(online, publication):
    coord = online.sites[1].site_coordinator if publication == "site_mqtt" else _coordinator(online)
    listener = MagicMock()
    remove = coord.async_add_listener(listener)
    try:
        deadline = coord._unsub_refresh
        assert deadline is not None
        coord.last_update_success = False
        if publication == "command":
            update_connector(coord, UUID, min_surpluspct=40)
        elif publication == "sessions":
            coord._async_get_recent_sessions = AsyncMock(return_value=[{"energy": 1.5}])
            await coord._async_run_session_refresh("test", force=True, minimum_interval=None)
        else:
            coord.apply_mqtt_properties(bootstrap.TOPIC, {"activePowerData": [3200, 0]})
        listener.assert_called_once_with()
        assert coord._unsub_refresh is deadline
        assert coord.last_update_success is False
    finally:
        remove()


def test_mqtt_disconnect_schedules_a_rest_refresh(online):
    """Without MQTT, REST is the only source, so it is consulted at once."""
    site = online.sites[1]
    schedule_refresh = MagicMock()

    _handle_mqtt_connection_change(
        False, site.site_coordinator, site.stations, 30, schedule_refresh
    )

    assert schedule_refresh.call_count == len(site.stations)


def test_mqtt_reconnect_schedules_a_rest_resync(online):
    """After an MQTT outage the missed changes must be fetched, not waited for.

    MQTT only sends configuration (current limits, surplus, grid support, LED)
    when it changes. A change made during an outage is therefore never repeated
    after the reconnect, and with healthy MQTT the next poll is up to 30 minutes
    away. Intended outcome: every station coordinator gets one REST refresh as
    soon as the transport is back.
    """
    site = online.sites[1]
    schedule_refresh = MagicMock()
    _handle_mqtt_connection_change(
        False, site.site_coordinator, site.stations, 30, schedule_refresh
    )
    schedule_refresh.reset_mock()

    _handle_mqtt_connection_change(True, site.site_coordinator, site.stations, 30, schedule_refresh)

    assert schedule_refresh.call_count == len(site.stations)
