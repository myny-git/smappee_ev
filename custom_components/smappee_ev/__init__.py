import logging

from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv

from .oauth import OAuth2Client
from .api_client import SmappeeApiClient
from .services import register_services, unregister_services
from .const import (
    DOMAIN,
    CONF_SERIAL,
    CONF_SERVICE_LOCATION_ID,
    CONF_UPDATE_INTERVAL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_SMART_DEVICE_UUID,
    CONF_SMART_DEVICE_ID,
    UPDATE_INTERVAL_DEFAULT,
)

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
    """Set up the Smappee EV component"""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Smappee EV config entry with support for multiple connectors."""
    _LOGGER.debug("Setting up entry for Smappee EV. Serial: %s", entry.data.get(CONF_SERIAL))
    
    hass.data.setdefault(DOMAIN, {})

    # Ensure connectors list is present
    if "carchargers" not in entry.data:
        _LOGGER.error("Config entry is missing 'carchargers': %s", entry.data)
        return False
    
    serial = entry.data[CONF_SERIAL]
    service_location_id = entry.data[CONF_SERVICE_LOCATION_ID]
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_DEFAULT)
   
    oauth_client = OAuth2Client(entry.data)    

    # Station-level client (for LED, availability, etc.)
    st = entry.data["station"]
    station_client = SmappeeApiClient(
        oauth_client,
        serial,
        st["uuid"],             # real station smart_device_uuid
        st["id"],               # real station smart_device_id
        service_location_id,
        update_interval,
    )
    station_client.enable()    

    # Connector-level clients (keyed by UUID)
    connector_clients = {}
    for device in entry.data["carchargers"]:
        client = SmappeeApiClient(
            oauth_client,
            serial,
            device["uuid"],
            device["id"],
            service_location_id,
            update_interval,
            connector_number=device.get("connector_number")  # pass through
        )
        client.enable()
        connector_clients[device["uuid"]] = client
   
    hass.data[DOMAIN][entry.entry_id] = {
        "station": station_client,
        "connectors": connector_clients,
    }

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
        active_keys = [k for k in hass.data[DOMAIN].keys() if k != "services_registered"]
        if not active_keys:
            unregister_services(hass)
            hass.data.pop(DOMAIN)
    return unload_ok

async def async_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle updates to config entry options."""
    _LOGGER.debug("Config entry updated: %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)    