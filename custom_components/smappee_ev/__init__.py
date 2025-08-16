"""Smappee EV Home Assistant integration package."""

import logging

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api_client import SmappeeApiClient
from .const import (
    CONF_SERIAL,
    CONF_SERVICE_LOCATION_ID,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    UPDATE_INTERVAL_DEFAULT,
)
from .coordinator import SmappeeCoordinator
from .oauth import OAuth2Client
from .services import register_services, unregister_services

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Smappee EV component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Smappee EV config entry with support for multiple connectors."""
    _LOGGER.debug("Setting up entry for Smappee EV. Serial: %s", entry.data.get(CONF_SERIAL))

    hass.data.setdefault(DOMAIN, {})

    # Ensure connectors list is present
    if "carchargers" not in entry.data or "station" not in entry.data:
        _LOGGER.error("Config entry missing connectors or station: %s", entry.data)
        return False

    # Use HA's aiohttp session
    session: ClientSession = async_get_clientsession(hass)

    serial = entry.data[CONF_SERIAL]
    service_location_id = entry.data[CONF_SERVICE_LOCATION_ID]
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_DEFAULT)

    # oauth_client = OAuth2Client(entry.data)
    oauth_client = OAuth2Client(entry.data, session=session)

    # Station-level client (for LED, availability, etc.)
    st = entry.data["station"]
    station_client = SmappeeApiClient(
        oauth_client,
        serial,
        st["uuid"],  # real station smart_device_uuid
        st["id"],  # real station smart_device_id
        service_location_id,
        session=session,
        is_station=True,
    )

    # await station_client.enable()

    # Connector-level clients (keyed by UUID)
    connector_clients = {}
    for device in entry.data["carchargers"]:
        client = SmappeeApiClient(
            oauth_client,
            serial,
            device["uuid"],
            device["id"],
            service_location_id,
            session=session,
            connector_number=device.get("connector_number"),  # pass through
        )

        # await client.enable()
        connector_clients[device["uuid"]] = client

    # --- Create and refresh coordinator ---
    coordinator = SmappeeCoordinator(
        hass,
        station_client=station_client,
        connector_clients=connector_clients,
        update_interval=update_interval,
    )
    await coordinator.async_config_entry_first_refresh()

    # --- Store in hass.data for platforms to use ---
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "station_client": station_client,
        "connector_clients": connector_clients,
    }

    # hass.data[DOMAIN][entry.entry_id] = {
    #    "station": station_client,
    #    "connectors": connector_clients,
    # }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.data[DOMAIN].get("services_registered", False):
        register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    entry.async_on_unload(entry.add_update_listener(async_entry_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Smappee EV config entry."""
    _LOGGER.debug("Unloading Smappee EV config entry: %s", entry.entry_id)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        active_keys = [k for k in hass.data[DOMAIN] if k != "services_registered"]
        if not active_keys:
            unregister_services(hass)
            hass.data.pop(DOMAIN, None)
    return unload_ok


async def async_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle updates to config entry options."""
    _LOGGER.debug("Config entry updated: %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
