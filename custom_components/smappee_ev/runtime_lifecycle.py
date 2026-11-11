"""Runtime shutdown and cleanup helpers for Smappee EV."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .models.runtime_data import RuntimeData, SmappeeEvConfigEntry
from .mqtt_setup import _iter_mqtt_clients

_LOGGER = logging.getLogger(__name__)


def _begin_runtime_shutdown(rd: RuntimeData) -> None:
    """Synchronously mark runtime resources as stopping."""
    rd.stopping = True
    rd.recovery.next_attempt = None
    if rd.recovery.status in ("waiting", "probing"):
        rd.recovery.status = "stopped"
    for remove in rd.cleanup_callbacks:
        remove()
    rd.cleanup_callbacks.clear()
    for site in rd.sites.values():
        if site.site_coordinator is not None:
            site.site_coordinator.cancel_freshness_timer()
        for bucket in site.stations.values():
            if bucket.station_coordinator is not None:
                bucket.station_coordinator.cancel_delayed_refreshes()

    for mqtt in rd.mqtt.values():
        for mqtt_client in _iter_mqtt_clients(mqtt):
            mqtt_client.begin_shutdown()


def _register_runtime_stop_cleanup(
    hass: HomeAssistant,
    entry: SmappeeEvConfigEntry,
    runtime: RuntimeData,
) -> None:
    """Cancel runtime background work as soon as Home Assistant begins stopping."""

    @callback
    def _handle_homeassistant_stop(_event: Event) -> None:
        _begin_runtime_shutdown(runtime)
        ensure_runtime_shutdown(hass, runtime)

    remove_stop_listener = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP,
        _handle_homeassistant_stop,
    )
    entry.async_on_unload(remove_stop_listener)


async def _async_shutdown_coordinator(
    coordinator: DataUpdateCoordinator[Any] | None, label: str
) -> None:
    """Shut one coordinator down; a failure must not stop the remaining cleanup."""
    if coordinator is None:
        return
    try:
        await coordinator.async_shutdown()
    except Exception:
        _LOGGER.debug("%s coordinator shutdown failed", label, exc_info=True)


async def _async_shutdown_runtime_resources(rd: RuntimeData) -> None:
    """Stop MQTT clients and coordinator background tasks for runtime data."""
    # Mark coordinators as shutting down before stopping MQTT, because MQTT
    # disconnect callbacks can otherwise schedule fallback refreshes.
    _begin_runtime_shutdown(rd)
    await _async_shutdown_coordinator(rd.status_coordinator, "Status")
    for site in rd.sites.values():
        await _async_shutdown_coordinator(site.site_coordinator, "Site")
        for bucket in site.stations.values():
            await _async_shutdown_coordinator(bucket.station_coordinator, "Station")

    for sid, mqtt in rd.mqtt.items():
        for mqtt_client in _iter_mqtt_clients(mqtt):
            try:
                await mqtt_client.stop()
            except Exception as err:
                _LOGGER.warning("Failed to stop MQTT client for service location %s: %s", sid, err)

    pending_tasks = [task for task in rd.background_tasks if not task.done()]
    for task in pending_tasks:
        task.cancel()
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    rd.background_tasks.difference_update(pending_tasks)
    # A completed task's done callback may still be waiting for the next loop tick.
    rd.background_tasks.difference_update({task for task in rd.background_tasks if task.done()})


def ensure_runtime_shutdown(
    hass: HomeAssistant,
    runtime: RuntimeData,
) -> asyncio.Task[None]:
    """Return the single shared shutdown task for a runtime."""
    if runtime.shutdown_task is None:
        runtime.shutdown_task = hass.async_create_task(_async_shutdown_runtime_resources(runtime))
    return runtime.shutdown_task
