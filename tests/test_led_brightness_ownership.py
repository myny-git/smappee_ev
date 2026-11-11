"""LED brightness has one remote owner, so two cloud reads never race on it.

The smart-device list is scanned for the whole service location; its LED value
is not tied to one station. The Dashboard station details are. Once they have
identified this station's LED, the Dashboard owns the brightness together with
MQTT and commands, and the poll only supplies a value while none is known.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev.light import SmappeeLedLight
from custom_components.smappee_ev.models.state import StationState
from tests import test_reconciliation_audit as audit

entry = audit.entry
online = audit.online
coord = audit.coord

UUID = "connector-1"


@pytest.fixture
def owned(coord):
    """A station whose LED the Dashboard has identified; brightness is 10."""
    coord.data.station.dashboard_led_device_id = "led-1"
    return coord


async def _start_dashboard(coord, brightness=50):
    started, finish = asyncio.Event(), asyncio.Event()

    async def remote(staged, force=False):
        if not force:
            return False
        staged.station.led_brightness = brightness
        staged.station.capacity_maximum_power_kw = 4.0
        staged.connectors[UUID].min_surpluspct = 60
        started.set()
        await finish.wait()
        return True

    coord._maybe_refresh_dashboard_data = remote
    task = asyncio.create_task(coord._async_dashboard_refresh_now())
    await asyncio.wait_for(started.wait(), 1)
    return task, finish


async def _complete(task, finish):
    finish.set()
    await asyncio.wait_for(task, 1)


@pytest.mark.parametrize("observed", [75, 0])
async def test_poll_does_not_write_a_led_the_dashboard_owns(owned, observed):
    """The location-wide smart-device scan must not replace the station's own value."""
    owned._fetch_station_state.return_value = StationState(led_brightness=observed)

    await owned.async_refresh()

    assert owned.data.station.led_brightness == 10
    assert owned.last_update_success


@pytest.mark.parametrize("identified", [False, True])
async def test_poll_supplies_the_led_while_the_dashboard_cannot(coord, identified):
    """Without an identified LED, or without any value yet, the poll is the source."""
    if identified:
        coord.data.station.dashboard_led_device_id = "led-1"
        coord.data.station.led_brightness = None
    coord._fetch_station_state.return_value = StationState(led_brightness=75)

    await coord.async_refresh()

    assert coord.data.station.led_brightness == 75


@pytest.mark.parametrize("observed", [10, 75, 0])
async def test_dashboard_value_arrives_whatever_an_overlapping_poll_reports(owned, observed):
    """A poll that overlaps a Dashboard read cannot affect the Dashboard's LED value."""
    task, finish = await _start_dashboard(owned, 50)
    try:
        owned._fetch_station_state.return_value = StationState(led_brightness=observed)
        await owned.async_refresh()
        assert owned.data.station.led_brightness == 10
        await _complete(task, finish)
        assert owned.data.station.led_brightness == 50
        assert owned.data.station.capacity_maximum_power_kw == 4
    finally:
        await audit._finish(task)


@pytest.mark.parametrize("writer", ["mqtt", "command"])
@pytest.mark.parametrize("moment", ["before_rest_finishes", "after_rest_finishes"])
@pytest.mark.parametrize("repeat", [False, True])
async def test_live_led_writes_win_over_both_remote_reads(owned, writer, moment, repeat):
    """MQTT and commands are newer than any read that was already in flight."""
    task, finish = await _start_dashboard(owned)
    started, release = asyncio.Event(), asyncio.Event()
    response = owned._fetch_connector_state.return_value

    async def blocked(*_args, **_kwargs):
        started.set()
        await release.wait()
        return response

    owned._fetch_station_state.return_value = StationState(led_brightness=75)
    owned._fetch_connector_state.side_effect = blocked
    poll = asyncio.create_task(owned.async_refresh())
    try:
        await asyncio.wait_for(started.wait(), 1)
        if moment == "after_rest_finishes":
            release.set()
            await asyncio.wait_for(poll, 1)
        value = owned.data.station.led_brightness if repeat else 30
        if writer == "mqtt":
            owned.apply_mqtt_properties(
                "servicelocation/site-uuid/etc/chargingstation/acchargingstation/v1/"
                "station-1/properties",
                {"ledBrightness": value},
            )
        else:
            api = MagicMock(set_brightness=AsyncMock())
            light = SmappeeLedLight(
                coordinator=owned, api_client=api, sid=1, station_uuid="station-1"
            )
            await light._set_brightness(value)
            api.set_brightness.assert_awaited_once_with(value)
        release.set()
        await asyncio.wait_for(poll, 1)
        await _complete(task, finish)
        assert owned.data.station.led_brightness == value
    finally:
        release.set()
        await audit._finish(poll)
        await audit._finish(task)


async def test_newer_rest_connector_changes_still_need_dashboard_snapshot_replay(coord):
    """Connector fields keep two remote sources, so the older read must not win."""
    task, finish = await _start_dashboard(coord)
    try:
        await coord.async_refresh()
        assert coord.data.connectors[UUID].min_surpluspct == 20
        await _complete(task, finish)
        assert coord.data.connectors[UUID].min_surpluspct == 20
    finally:
        await audit._finish(task)
