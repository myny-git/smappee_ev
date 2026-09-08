"""Bounded Dashboard recovery while a cached MQTT runtime remains active."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
import logging
import random

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api.dashboard_client import SmappeeDashboardClient
from .api.errors import SmappeeError
from .dashboard_discovery import _create_dashboard_client
from .models.runtime_data import RuntimeData, SmappeeEvConfigEntry
from .mqtt_bootstrap import snapshot_from_runtime
from .runtime_lifecycle import _async_shutdown_runtime_resources

_LOGGER = logging.getLogger(__name__)
type PrepareRuntime = Callable[
    [HomeAssistant, SmappeeEvConfigEntry, SmappeeDashboardClient], Awaitable[RuntimeData]
]


def start_recovery(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry, runtime: RuntimeData, prepare: PrepareRuntime
) -> None:
    """Start one owned recovery task and a local freshness clock (no REST polling)."""

    @callback
    def update_freshness(_now: datetime) -> None:
        for site in runtime.sites.values():
            if site.site_coordinator is not None:
                site.site_coordinator.async_update_listeners()
            for bucket in site.stations.values():
                if bucket.station_coordinator is not None:
                    bucket.station_coordinator.async_update_listeners()

    runtime.cleanup_callbacks.append(
        async_track_time_interval(hass, update_freshness, timedelta(seconds=30))
    )
    task = hass.async_create_background_task(
        _async_recover(hass, entry, runtime, prepare), f"{entry.entry_id} Dashboard recovery"
    )
    runtime.background_tasks.add(task)
    task.add_done_callback(runtime.background_tasks.discard)


async def _async_recover(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry, runtime: RuntimeData, prepare: PrepareRuntime
) -> None:
    """Probe full discovery without starting a second MQTT connection."""
    delay = 30.0
    while True:
        await asyncio.sleep(delay)
        if runtime.stopping or hass.is_stopping:
            return
        probe: RuntimeData | None = None
        recovered = False
        try:
            client = _create_dashboard_client(hass, entry, async_get_clientsession(hass))
            probe = await prepare(hass, entry, client)
            snapshot_from_runtime(probe, entry)
            _validate_rest_recovery(probe)
        except ConfigEntryAuthFailed:
            entry.async_start_reauth_if_available(hass)
            _LOGGER.warning(
                "Dashboard recovery requires reauthentication; MQTT monitoring continues"
            )
            return
        except SmappeeError, ConfigEntryNotReady, ValueError, TypeError, KeyError:
            _LOGGER.debug("Dashboard recovery not yet complete; MQTT monitoring continues")
        except Exception:
            _LOGGER.exception("Unexpected Dashboard recovery failure")
        else:
            recovered = True
        finally:
            if probe is not None:
                await _async_shutdown_runtime_resources(probe)
        if recovered:
            if not runtime.stopping and not hass.is_stopping:
                _LOGGER.info("Dashboard discovery recovered; reloading to restore controls")
                # Schedule only after probe cleanup, with no subsequent await.
                handle = hass.loop.call_soon(_schedule_reload, hass, entry, runtime)
                runtime.cleanup_callbacks.append(handle.cancel)
            return
        delay = min(600.0, delay * 2 * random.uniform(0.9, 1.1))  # noqa: S311 - retry jitter


@callback
def _schedule_reload(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry, runtime: RuntimeData
) -> None:
    if not runtime.stopping and not hass.is_stopping and entry.runtime_data is runtime:
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))


def _validate_rest_recovery(runtime: RuntimeData) -> None:
    """Require reachable REST state before leaving the monitoring runtime."""
    for site in runtime.sites.values():
        for bucket in site.stations.values():
            coord = bucket.station_coordinator
            if (
                coord is None
                or coord.data is None
                or not coord.data.station.api_available
                or not set(bucket.connectors).issubset(coord.data.connectors)
                or any(not coord.data.connectors[key].api_available for key in bucket.connectors)
            ):
                raise ValueError("Dashboard REST recovery is incomplete")
