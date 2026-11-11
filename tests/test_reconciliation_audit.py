"""Concurrency boundaries for writers that are not interchangeable confirmations."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev import number
from custom_components.smappee_ev.models.state import ConnectorState, StationState
from custom_components.smappee_ev.state_updates import update_connector
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online
UUID = "connector-1"


@pytest.fixture
def coord(online):
    coordinator = online.sites[1].stations["station-1"].station_coordinator
    coordinator._ensure_power_index_map = AsyncMock()
    coordinator._fetch_station_state = AsyncMock(return_value=StationState(led_brightness=10))
    coordinator._fetch_connector_state = AsyncMock(
        return_value=ConnectorState(connector_number=1, max_current=16, min_surpluspct=20)
    )
    coordinator._maybe_refresh_dashboard_data = AsyncMock(return_value=False)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_schedule_dashboard_refresh = MagicMock()
    coordinator.data.station.led_brightness = 10
    return coordinator


async def _finish(task):
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_restoration_during_rest_poll_needs_snapshot_diff_without_confirmation(
    coord, monkeypatch
):
    """Restored defaults are real writers, but they are not cloud confirmations."""
    coord.data.connectors[UUID].min_surpluspct = None
    started, finish = asyncio.Event(), asyncio.Event()

    async def remote(*_args, **_kwargs):
        started.set()
        await finish.wait()
        return ConnectorState(connector_number=1, min_surpluspct=20)

    coord._fetch_connector_state.side_effect = remote
    entity = number.SmappeeMinSurplusPctNumber(
        coord, coord.connector_clients[UUID], 1, "station-1", UUID
    )
    monkeypatch.setattr(number.SmappeeConnectorEntity, "async_added_to_hass", AsyncMock())
    entity.async_get_last_number_data = AsyncMock(return_value=MagicMock(native_value=40))
    poll = asyncio.create_task(coord._async_fetch_data())
    try:
        await asyncio.wait_for(started.wait(), 1)
        version = coord._state_confirmations.version
        await entity.async_added_to_hass()
        assert coord.data.connectors[UUID].min_surpluspct == 40
        assert coord._state_confirmations.version == version
        finish.set()
        result = await asyncio.wait_for(poll, 1)
        assert result.connectors[UUID].min_surpluspct == 40
    finally:
        await _finish(poll)


async def test_two_connectors_keep_independent_confirmations_during_a_poll(coord, online):
    second = (
        online.sites[1].stations["station-2"].station_coordinator.connector_clients["connector-2"]
    )
    coord.connector_clients["connector-2"] = second
    coord.data.connectors[UUID] = ConnectorState(connector_number=1, max_current=32)
    coord.data.connectors["connector-2"] = ConnectorState(connector_number=2, max_current=32)
    started, finish = asyncio.Event(), asyncio.Event()

    async def remote(client, **_kwargs):
        started.set()
        await finish.wait()
        return ConnectorState(
            connector_number=client.connector_number, max_current=16, min_surpluspct=20
        )

    coord._fetch_connector_state.side_effect = remote
    poll = asyncio.create_task(coord._async_fetch_data())
    try:
        await asyncio.wait_for(started.wait(), 1)
        # Repeat an unchanged bound on connector 1, write a different field on 2.
        coord.apply_mqtt_properties(
            "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/updated",
            {"deviceUUID": UUID, "maximumCurrent": 32},
        )
        update_connector(coord, "connector-2", min_surpluspct=40)
        finish.set()
        result = await asyncio.wait_for(poll, 1)
        assert result.connectors[UUID].max_current == 32
        assert result.connectors[UUID].min_surpluspct == 20
        assert result.connectors["connector-2"].max_current == 16
        assert result.connectors["connector-2"].min_surpluspct == 40
        coord.async_set_updated_data(result)
        later = await coord._async_fetch_data()
        # A later read is authoritative: confirmations are not permanent locks.
        assert later.connectors[UUID].max_current == 16
        assert later.connectors["connector-2"].min_surpluspct == 20
    finally:
        await _finish(poll)


@pytest.mark.parametrize("boundary", ["cancel", "shutdown"])
async def test_cancelled_dashboard_staging_never_leaks_into_live_state(coord, boundary):
    started, finish = asyncio.Event(), asyncio.Event()

    async def remote(staged, force=False):
        staged.station.led_brightness = 99
        staged.connectors[UUID].max_current = 16
        started.set()
        await finish.wait()
        return True

    coord.data.connectors[UUID].max_current = 32
    current = coord.data
    coord._maybe_refresh_dashboard_data = remote
    notify = coord.async_update_listeners = MagicMock()
    coord._start_dashboard_refresh_task()
    task = coord._dashboard_refresh_task
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert current.station.led_brightness == 10
        assert current.connectors[UUID].max_current == 32
        update_connector(coord, UUID, min_surpluspct=40)
        if boundary == "shutdown":
            coord.cancel_delayed_refreshes()
        else:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert coord.data is current
        assert current.station.led_brightness == 10
        assert current.connectors[UUID].max_current == 32
        assert current.connectors[UUID].min_surpluspct == 40
        notify.assert_called_once_with()
        coord.async_request_refresh.assert_not_awaited()
        assert coord._dashboard_refresh_task is None
    finally:
        await _finish(task)


async def test_cancelled_rest_staging_does_not_publish_or_revert_new_mqtt(coord):
    started, finish = asyncio.Event(), asyncio.Event()

    async def remote(staged):
        staged.station.led_brightness = 99
        started.set()
        await finish.wait()
        return True

    coord._maybe_refresh_dashboard_data = remote
    current = coord.data
    notify = coord.async_update_listeners = MagicMock()
    poll = asyncio.create_task(coord._async_fetch_data())
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert current.station.led_brightness == 10
        coord.apply_mqtt_properties(bootstrap.TOPIC, {"activePowerData": [0, 3200]})
        poll.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(poll, 1)
        assert coord.data is current
        assert current.station.led_brightness == 10
        assert current.connectors[UUID].power_total == 3200
        notify.assert_called_once_with()
    finally:
        await _finish(poll)


async def test_session_query_updates_the_snapshot_a_real_poll_replaced(coord):
    started, finish = asyncio.Event(), asyncio.Event()

    async def sessions():
        started.set()
        await finish.wait()
        return [{"energy": 12.5}]

    coord._async_get_recent_sessions = sessions
    initial = coord.data
    task = asyncio.create_task(
        coord._async_run_session_refresh("audit", force=True, minimum_interval=None)
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        await coord.async_refresh()
        current = coord.data
        assert current is not initial
        assert current.connectors[UUID] is not initial.connectors[UUID]
        finish.set()
        await asyncio.wait_for(task, 1)
        assert coord.data is current
        assert current.recent_sessions == [{"energy": 12.5}]
        assert initial.recent_sessions == []
    finally:
        await _finish(task)


async def test_unchanged_later_poll_is_not_a_general_read_ordering_barrier(coord):
    """Audit limitation: diffs track changed values, not generations of cloud reads.

    The existing later-read-wins tests exercise changed values. Here a newer
    REST poll reads the same brightness as the baseline, so it supplies neither
    a diff nor a confirmation against an older Dashboard response.
    """
    started, finish = asyncio.Event(), asyncio.Event()

    async def dashboard(staged, force=False):
        if force:
            staged.station.led_brightness = 50
            started.set()
            await finish.wait()
        return force

    coord._maybe_refresh_dashboard_data = dashboard
    task = asyncio.create_task(coord._async_dashboard_refresh_now())
    try:
        await asyncio.wait_for(started.wait(), 1)
        version = coord._state_confirmations.version
        await coord.async_refresh()
        assert coord.data.station.led_brightness == 10
        assert coord._state_confirmations.version == version
        finish.set()
        await asyncio.wait_for(task, 1)
        assert coord.data.station.led_brightness == 50
    finally:
        await _finish(task)
