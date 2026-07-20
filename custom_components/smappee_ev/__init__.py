"""Set up and manage runtime data for the Smappee EV integration."""

import asyncio
import logging

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api.discovery import SmappeeLocationTopology
from .const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    CONF_NEEDS_DASHBOARD_REAUTH,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    UPDATE_INTERVAL_DEFAULT,
)
from .dashboard_discovery import (
    _create_dashboard_client,
    _dashboard_client_configured,
    _load_dashboard_topologies,
)
from .models.mqtt_diagnostics import MqttRoutingDiagnostics
from .models.runtime_data import (
    MqttRuntimeValue,
    RuntimeData,
    SmappeeEvConfigEntry,
    SmappeeSiteRuntime,
)
from .mqtt_setup import _mqtt_routing_diagnostics, _start_mqtt_clients
from .runtime_assembly import _log_stored_runtime_shape, _prepare_site_topologies
from .runtime_devices import _current_station_device_identifiers, _register_runtime_devices
from .runtime_lifecycle import (
    _async_shutdown_runtime_resources,
    _register_runtime_stop_cleanup,
    ensure_runtime_shutdown,
)
from .services import register_services

_LOGGER = logging.getLogger(__name__)
_SERVICE_REGISTRATION_SENTINEL = "start_charging"
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entry versions to the current format.

    Version history:
      - v5 removes user control of update interval and drops old OAuth/v3 fields.
      - v6 marks entries without Dashboard credentials for reauthentication.
    """
    version = entry.version
    data = dict(entry.data)
    options = dict(entry.options)

    updated = False

    # v5 cleanup: remove legacy update interval key if present.
    if version < 5:
        if "update_interval" in data:
            data.pop("update_interval")
            updated = True
        if "update_interval" in options:
            options.pop("update_interval")
            updated = True
        version = 5

    # Remove old v3/OAuth credentials.
    for old_key in (
        "client_id",
        "client_secret",
        "access_token",
        "refresh_token",
        "token_expires_at",
    ):
        if old_key in data:
            data.pop(old_key)
            updated = True

    # v6: Dashboard v10/v11 requires Dashboard credentials.
    if version < 6:
        has_dashboard_credentials = bool(
            data.get(CONF_DASHBOARD_REFRESH_TOKEN)
            or (data.get(CONF_USERNAME) and data.get(CONF_PASSWORD))
        )

        if not has_dashboard_credentials:
            data[CONF_NEEDS_DASHBOARD_REAUTH] = True
            updated = True

        version = 6

    if updated or version != entry.version:
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=version,
        )
        _LOGGER.info(
            "Config entry %s migrated to version %s",
            entry.entry_id,
            version,
        )
    else:
        _LOGGER.debug(
            "Config entry %s already at latest version %s",
            entry.entry_id,
            version,
        )

    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Smappee EV component."""
    # Register services once domain-wide (multi-entry safe)
    if not hass.services.has_service(DOMAIN, _SERVICE_REGISTRATION_SENTINEL):
        await register_services(hass)
    return True


def _start_runtime_background_work(
    hass: HomeAssistant,
    sites: dict[int, SmappeeSiteRuntime],
    mqtt_clients: dict[int, MqttRuntimeValue],
) -> None:
    """Start periodic work after the complete topology has committed."""
    for site in sites.values():
        for bucket in site.stations.values():
            coordinator = bucket.station_coordinator
            if coordinator is not None:
                coordinator.async_start_session_tracking()
    for mqtt in mqtt_clients.values():
        _start_mqtt_clients(hass, mqtt)


async def async_setup_entry(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> bool:
    """Set up a Smappee EV account entry that discovers all service locations with a charger."""
    _LOGGER.debug("Setting up Smappee EV account entry: %s", entry.title)

    # Use HA's aiohttp session
    session: ClientSession = async_get_clientsession(hass)

    update_interval = UPDATE_INTERVAL_DEFAULT

    dashboard_client = _create_dashboard_client(hass, entry, session)

    if entry.data.get(CONF_NEEDS_DASHBOARD_REAUTH) or not _dashboard_client_configured(
        dashboard_client
    ):
        raise ConfigEntryAuthFailed(
            "Smappee Dashboard credentials are required after migration to API v10/v11"
        )

    # 1) Discover site-first topologies
    topologies = await _load_dashboard_topologies(dashboard_client)

    sites: dict[int, SmappeeSiteRuntime] = {}
    mqtt_clients: dict[int, MqttRuntimeValue] = {}
    mqtt_diagnostics: dict[int, list[MqttRoutingDiagnostics]] = {}
    background_tasks: set[asyncio.Task] = set()

    # 2) Merge discovery records by physical site before creating runtime objects.
    topologies_by_site: dict[int, list[SmappeeLocationTopology]] = {}
    for topology in topologies:
        topologies_by_site.setdefault(topology.site_location_id, []).append(topology)

    # Prepare different physical sites in parallel. Controls belonging to one
    # site are prepared together so coordinators and MQTT capture the final map.
    client_id_prefix = f"ha-{entry.entry_id[-6:]}"
    prep_tasks = [
        _prepare_site_topologies(
            hass,
            site_topologies,
            update_interval,
            client_id_prefix,
            config_entry=entry,
            dashboard_client=dashboard_client,
            background_tasks=background_tasks,
            start_runtime=False,
        )
        for site_topologies in topologies_by_site.values()
    ]
    try:
        results = await asyncio.gather(*prep_tasks, return_exceptions=True)
        hard_error: BaseException | None = None
        for (sid, _site_topologies), res in zip(topologies_by_site.items(), results, strict=True):
            if isinstance(res, asyncio.CancelledError):
                hard_error = hard_error or res
                continue
            if isinstance(res, ConfigEntryAuthFailed):
                hard_error = hard_error or res
                continue
            if isinstance(res, BaseException):
                hard_error = hard_error or res
                _LOGGER.warning("Site %s preparation failed: %s", sid, res)
                continue
            site, mqtt = res
            mqtt_diagnostics.setdefault(sid, []).extend(_mqtt_routing_diagnostics(mqtt))
            if mqtt:
                mqtt_clients[sid] = mqtt
            if site is None:
                continue
            sites[sid] = site

        if hard_error is not None:
            if isinstance(hard_error, asyncio.CancelledError | ConfigEntryAuthFailed):
                raise hard_error
            raise ConfigEntryNotReady(
                f"Preparing Smappee EV topology failed: {hard_error}"
            ) from hard_error

        if not sites:
            _LOGGER.debug("Discovered service locations but no stations mapped yet (retry later)")
            raise ConfigEntryNotReady("No Smappee EV stations discovered (will retry)")

        # Commit the complete topology before starting periodic/background work.
        _start_runtime_background_work(hass, sites, mqtt_clients)
    except BaseException:
        await _async_shutdown_runtime_resources(
            RuntimeData(
                api=dashboard_client,
                sites=sites,
                mqtt=mqtt_clients,
                dashboard=dashboard_client,
                background_tasks=background_tasks,
                mqtt_diagnostics=mqtt_diagnostics,
            )
        )
        raise

    # Store runtime data only on the entry (preferred pattern); avoid duplicating in hass.data
    runtime = RuntimeData(
        api=dashboard_client,
        sites=sites,
        mqtt=mqtt_clients,
        dashboard=dashboard_client,
        background_tasks=background_tasks,
        mqtt_diagnostics=mqtt_diagnostics,
    )
    entry.runtime_data = runtime
    _register_runtime_stop_cleanup(hass, entry, runtime)
    _log_stored_runtime_shape(runtime)
    try:
        _register_runtime_devices(hass, entry)

        # Platforms start
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Services already registered domain-wide in async_setup
    except asyncio.CancelledError:
        await _async_shutdown_runtime_resources(runtime)
        raise
    except Exception:
        await _async_shutdown_runtime_resources(runtime)
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading config entry: %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    try:
        rd = entry.runtime_data
    except AttributeError:
        _LOGGER.debug(
            "Unload requested for %s but no runtime_data present (may have failed early)",
            entry.entry_id,
        )
    else:
        if isinstance(rd, RuntimeData):
            await ensure_runtime_shutdown(hass, rd)
        else:
            _LOGGER.debug(
                "Unload requested for %s but runtime_data is invalid (may have failed early)",
                entry.entry_id,
            )

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: SmappeeEvConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow users to remove stale Smappee EV devices from the registry."""
    domain_identifiers = {
        identifier for domain, identifier in device_entry.identifiers if domain == DOMAIN
    }
    if not domain_identifiers:
        return True

    current_identifiers = _current_station_device_identifiers(entry)
    if not current_identifiers:
        return False

    return domain_identifiers.isdisjoint(current_identifiers)
