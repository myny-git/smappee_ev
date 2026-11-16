"""Unloading a config entry and shutting down its runtime."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smappee_ev import async_remove_entry, async_unload_entry
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.dashboard_discovery import (
    MAINTENANCE_STATES,
    create_dashboard_client,
)
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.runtime_lifecycle import async_shutdown_runtime_resources
from tests.factories import make_site_runtime, make_station_runtime


@pytest.mark.asyncio
async def test_shutdown_runtime_resources_stops_mqtt_and_cancels_background_tasks():
    events: list[str] = []

    async def site_shutdown():
        events.append("site-shutdown")
        raise RuntimeError("site shutdown warning")

    async def station_shutdown():
        events.append("station-shutdown")

    async def mqtt_stop():
        events.append("mqtt-stop")
        assert station_coord.async_shutdown.await_count == 1

    def failing_mqtt_stop():
        events.append("failing-mqtt-stop")
        raise OSError("already closed")

    site_coord = MagicMock()
    site_coord.async_shutdown = AsyncMock(side_effect=site_shutdown)
    station_coord = MagicMock()
    station_coord.async_shutdown = AsyncMock(side_effect=station_shutdown)
    async_mqtt = MagicMock()
    async_mqtt.stop = AsyncMock(side_effect=mqtt_stop)
    failing_mqtt = MagicMock()
    failing_mqtt.stop.side_effect = failing_mqtt_stop
    pending = asyncio.create_task(asyncio.sleep(60))
    runtime = RuntimeData(
        api=MagicMock(),
        mqtt={100: [async_mqtt, failing_mqtt]},
        sites={
            100: make_site_runtime(
                site_coordinator=site_coord,
                stations={"station": make_station_runtime(coordinator=station_coord)},
            )
        },
        background_tasks={pending},
    )

    await async_shutdown_runtime_resources(runtime)

    station_coord.async_shutdown.assert_awaited_once()
    async_mqtt.stop.assert_awaited_once()
    assert events == [
        "site-shutdown",
        "station-shutdown",
        "mqtt-stop",
        "failing-mqtt-stop",
    ]
    assert pending.cancelled()
    assert runtime.background_tasks == set()


@pytest.mark.asyncio
async def test_async_unload_entry_handles_missing_invalid_and_active_runtime(hass):
    class EntryWithoutRuntime:
        entry_id = "missing-runtime"

    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.services.async_register(DOMAIN, "start_charging", MagicMock())

    assert await async_unload_entry(hass, EntryWithoutRuntime()) is True
    assert hass.services.has_service(DOMAIN, "start_charging")

    invalid_entry = MagicMock()
    invalid_entry.entry_id = "invalid-runtime"
    invalid_entry.runtime_data = object()

    assert await async_unload_entry(hass, invalid_entry) is True
    assert hass.services.has_service(DOMAIN, "start_charging")

    hass.config_entries.async_unload_platforms.return_value = False
    assert await async_unload_entry(hass, invalid_entry) is False
    assert hass.services.has_service(DOMAIN, "start_charging")


async def test_remove_entry_forgets_dashboard_maintenance_state(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    create_dashboard_client(hass, entry, MagicMock())
    assert entry.entry_id in hass.data[MAINTENANCE_STATES]

    await async_remove_entry(hass, entry)

    assert entry.entry_id not in hass.data[MAINTENANCE_STATES]


async def test_cancelled_unload_does_not_cancel_runtime_shutdown(hass):
    """The shutdown task is shared; only its own completion may end it."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_shutdown():
        started.set()
        await release.wait()

    runtime = RuntimeData(api=MagicMock(), mqtt={}, sites={})
    runtime.status_coordinator = MagicMock(async_shutdown=slow_shutdown)
    entry = MagicMock(entry_id="entry", runtime_data=runtime)

    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        unload = asyncio.create_task(async_unload_entry(hass, entry))
        await started.wait()
        unload.cancel()
        with pytest.raises(asyncio.CancelledError):
            await unload

    assert not runtime.shutdown_task.done()
    release.set()
    await runtime.shutdown_task
    assert runtime.stopping is True
