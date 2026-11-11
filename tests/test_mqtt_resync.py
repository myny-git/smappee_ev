"""MQTT reconnects force a fresh, serialized REST read without replayed config."""

import asyncio
from time import monotonic
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev.const import MQTT_RESYNC_MAX_PASSES
from custom_components.smappee_ev.models.state import ConnectorState, StationState
from custom_components.smappee_ev.mqtt_setup import (
    _handle_mqtt_connection_change,
    _handle_mqtt_refresh_done,
    _setup_mqtt,
)
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


@pytest.fixture
def resync(hass, online):
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    coord._last_smartdevice_refresh = monotonic()
    coord._fetch_station_state = AsyncMock(return_value=StationState())
    coord._fetch_connector_state = AsyncMock(
        return_value=ConnectorState(connector_number=1, min_surpluspct=60)
    )
    coord._ensure_power_index_map = AsyncMock()
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=False)
    tasks = set()
    mqtt = _setup_mqtt(
        hass,
        suuid="site-uuid",
        serial_str="gateway",
        sid=1,
        stations={"station-1": bucket},
        client_id_prefix="resync-test",
        update_interval=30,
        background_tasks=tasks,
        start_clients=False,
    )
    mqtt._on_conn(True)
    assert not tasks  # The initial connection already has its bootstrap snapshot.
    return coord, mqtt, tasks


async def test_reconnect_forces_config_read_without_waiting_for_poll_deadline(hass, resync):
    coord, mqtt, tasks = resync
    mqtt._on_conn(False)
    await hass.async_block_till_done()
    coord._fetch_connector_state.assert_awaited_once()
    coord.data.connectors["connector-1"].min_surpluspct = 20

    mqtt._on_conn(True)
    await hass.async_block_till_done()

    assert coord._fetch_connector_state.await_count == 2
    assert coord.data.connectors["connector-1"].min_surpluspct == 60
    assert not tasks
    for _ in range(3):
        mqtt._on_conn(True)
        mqtt._on_properties("/homeassistant/heartbeat", {})
    await hass.async_block_till_done()
    assert coord._fetch_connector_state.await_count == 2


@pytest.mark.parametrize("source", ["periodic", "disconnect"])
async def test_reconnect_during_older_poll_coalesces_one_fresh_followup(hass, resync, source):
    coord, mqtt, tasks = resync
    started, finish = asyncio.Event(), asyncio.Event()

    async def station(*_args, **_kwargs):
        if coord._fetch_station_state.await_count == 1:
            started.set()
            await finish.wait()
        return StationState()

    coord._fetch_station_state.side_effect = station
    poll = None
    if source == "periodic":
        coord._force_smartdevice_refresh = True
        poll = asyncio.create_task(coord.async_refresh())
    else:
        mqtt._on_conn(False)
    try:
        await asyncio.wait_for(started.wait(), 1)
        # All these edges arrive while the older request is blocked. They need
        # one read started after the last reconnect, never parallel REST polls.
        for _ in range(3):
            mqtt._on_conn(False)
            mqtt._on_conn(True)
        await asyncio.sleep(0)
        assert coord._fetch_station_state.await_count == 1
        assert len(tasks) == 1
        # New MQTT confirmations still survive the older REST result.
        coord.apply_mqtt_properties(bootstrap.TOPIC, {"activePowerData": [0, 3200]})
        finish.set()
        if poll is not None:
            await asyncio.wait_for(poll, 1)
        await hass.async_block_till_done()
        assert coord._fetch_station_state.await_count == 2
        assert coord.data.connectors["connector-1"].min_surpluspct == 60
        assert coord.data.connectors["connector-1"].power_total == 3200
        assert not coord._force_smartdevice_refresh
        assert not tasks
    finally:
        finish.set()
        if poll is not None:
            await asyncio.gather(poll, return_exceptions=True)


async def test_shutdown_discards_a_resync_waiting_for_an_older_poll(hass, resync):
    coord, mqtt, tasks = resync
    started, finish = asyncio.Event(), asyncio.Event()

    async def station(*_args, **_kwargs):
        started.set()
        await finish.wait()
        return StationState()

    coord._fetch_station_state.side_effect = station
    coord._force_smartdevice_refresh = True
    poll = asyncio.create_task(coord.async_refresh())
    try:
        await asyncio.wait_for(started.wait(), 1)
        mqtt._on_conn(False)
        mqtt._on_conn(True)
        await asyncio.sleep(0)
        coord.cancel_delayed_refreshes()
        finish.set()
        await asyncio.wait_for(poll, 1)
        await hass.async_block_till_done()
        assert coord._fetch_station_state.await_count == 1
        assert not tasks
        mqtt._on_conn(False)
        mqtt._on_conn(True)
        assert not tasks
    finally:
        finish.set()
        await asyncio.gather(poll, return_exceptions=True)


def test_reconnect_uses_transport_edges_even_if_telemetry_already_marked_online(online):
    site = online.sites[1]
    schedule = MagicMock()
    for up in (True, False):
        _handle_mqtt_connection_change(up, site.site_coordinator, site.stations, 30, schedule)
    schedule.reset_mock()
    for bucket in site.stations.values():
        bucket.station_coordinator.data.station.mqtt_connected = True
    for _ in range(3):
        _handle_mqtt_connection_change(True, site.site_coordinator, site.stations, 30, schedule)
    assert schedule.call_count == len(site.stations)


def test_completed_refresh_callback_keeps_a_newer_pending_task():
    old, new = MagicMock(), MagicMock()
    old.cancelled.return_value = False
    old.exception.return_value = None
    tasks = {1: new}
    _handle_mqtt_refresh_done(tasks, old, 1)
    assert tasks == {1: new}


async def test_monitoring_only_connections_update_state_without_rest(hass, resync):
    coord, mqtt, tasks = resync
    coord.monitoring_only = True
    mqtt._on_conn(False)
    assert coord.data.station.mqtt_connected is False
    mqtt._on_conn(True)
    assert coord.data.station.mqtt_connected is True
    await hass.async_block_till_done()
    assert not tasks
    coord._fetch_station_state.assert_not_awaited()


async def test_flapping_connection_reads_once_per_direction_then_waits_for_the_tick(
    hass, resync, monkeypatch
):
    """A flapping broker must not turn into one REST request per flap.

    Intended outcome: the first loss and the first recovery each read REST at
    once. Further flaps inside the interval only leave the forced read pending,
    and the coordinator's next regular tick performs it. Nothing is skipped.
    """
    coord, mqtt, _tasks = resync
    for _ in range(4):
        mqtt._on_conn(False)
        await hass.async_block_till_done()
        mqtt._on_conn(True)
        await hass.async_block_till_done()
    assert coord._fetch_connector_state.await_count == 2
    assert coord._force_smartdevice_refresh

    await coord.async_refresh()  # the regular tick
    assert coord._fetch_connector_state.await_count == 3
    assert not coord._force_smartdevice_refresh

    monkeypatch.setattr("custom_components.smappee_ev.mqtt_setup.MQTT_RESYNC_MIN_INTERVAL", 0)
    mqtt._on_conn(False)
    await hass.async_block_till_done()
    assert coord._fetch_connector_state.await_count == 4


async def test_resync_task_ends_when_the_forced_read_is_never_consumed(hass, resync):
    """The resync loop is bounded, so a refresh that does nothing cannot spin."""
    coord, mqtt, tasks = resync
    coord.async_refresh = AsyncMock()

    mqtt._on_conn(False)
    await hass.async_block_till_done()

    assert coord.async_refresh.await_count == MQTT_RESYNC_MAX_PASSES
    assert not tasks
