"""Runtime shutdown and cleanup helpers for Smappee EV."""

from __future__ import annotations

import asyncio
from inspect import isawaitable
import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback

from .models.runtime_data import RuntimeData, SmappeeEvConfigEntry, SmappeeSiteRuntime
from .mqtt_setup import _iter_mqtt_clients

_LOGGER = logging.getLogger(__name__)


def _begin_runtime_shutdown(rd: RuntimeData) -> None:
    """Synchronously mark runtime resources as stopping."""
    for site in (rd.sites or {}).values():
        for bucket in site.stations.values():
            coord = bucket.station_coordinator
            cancel_delayed = getattr(coord, "cancel_delayed_refreshes", None)
            if callable(cancel_delayed):
                cancel_delayed()

    for mqtt in (rd.mqtt or {}).values():
        for mqtt_client in _iter_mqtt_clients(mqtt):
            begin_shutdown = getattr(mqtt_client, "begin_shutdown", None)
            if callable(begin_shutdown):
                begin_shutdown()


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


async def _shutdown_site_coordinator(site: SmappeeSiteRuntime) -> None:
    """Shutdown a site coordinator if present."""
    site_coord = site.site_coordinator
    shutdown = getattr(site_coord, "async_shutdown", None)
    if callable(shutdown):
        try:
            result = shutdown()
            if isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - shutdown must continue for other resources
            _LOGGER.debug("Site coordinator shutdown issue: %s", exc)


async def _async_shutdown_runtime_resources(rd: RuntimeData) -> None:
    """Stop MQTT clients and coordinator background tasks for runtime data."""
    # Mark coordinators as shutting down before stopping MQTT, because MQTT
    # disconnect callbacks can otherwise schedule fallback refreshes.
    _begin_runtime_shutdown(rd)
    for site in (rd.sites or {}).values():
        await _shutdown_site_coordinator(site)
        for bucket in site.stations.values():
            coord = bucket.station_coordinator
            shutdown = getattr(coord, "async_shutdown", None)
            if callable(shutdown):
                try:
                    result = shutdown()
                    if isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - shutdown must continue
                    _LOGGER.debug("Coordinator shutdown issue: %s", exc)

    for sid, mqtt in (rd.mqtt or {}).items():
        for mqtt_client in _iter_mqtt_clients(mqtt):
            stop_fn = getattr(mqtt_client, "stop", None)
            if not callable(stop_fn):  # pragma: no cover - defensive
                continue
            try:
                result = stop_fn()
                if asyncio.iscoroutine(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - shutdown must continue
                _LOGGER.warning("Failed to stop MQTT client for service location %s: %s", sid, err)

    pending_tasks = [task for task in rd.background_tasks if not task.done()]
    for task in pending_tasks:
        task.cancel()
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    rd.background_tasks.difference_update(pending_tasks)


def ensure_runtime_shutdown(
    hass: HomeAssistant,
    runtime: RuntimeData,
) -> asyncio.Task[None]:
    """Return the single shared shutdown task for a runtime."""
    task = runtime.shutdown_task
    if task is None:
        shutdown_coro = _async_shutdown_runtime_resources(runtime)
        created = hass.async_create_task(shutdown_coro)
        if not isinstance(created, asyncio.Task):  # pragma: no cover - lightweight HA test doubles
            shutdown_coro.close()
            created = asyncio.create_task(_async_shutdown_runtime_resources(runtime))
        task = created
        runtime.shutdown_task = task
    return task
