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
    CONF_SMART_DEVICE_UUID,
    CONF_SMART_DEVICE_ID,
    CONF_UPDATE_INTERVAL,
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
    """Set up a Smappee EV config entry."""
    _LOGGER.debug("Setting up entry for Smappee EV. Serial: %s", entry.data.get(CONF_SERIAL))
    
    hass.data.setdefault(DOMAIN, {})
    
    required = [
        CONF_SERIAL, 
        CONF_SERVICE_LOCATION_ID,
        CONF_SMART_DEVICE_UUID,
        CONF_SMART_DEVICE_ID,
    ]
    if not all(k in entry.data for k in required):
        _LOGGER.error("Missing required entry data for Smappee EV: %s", entry.data)
        return False
   
    oauth_client = OAuth2Client(entry.data)    
    api_client = SmappeeApiClient(
        oauth_client, 
        entry.data[CONF_SERIAL],
        entry.data[CONF_SMART_DEVICE_UUID],
        entry.data[CONF_SMART_DEVICE_ID],
        entry.data[CONF_SERVICE_LOCATION_ID],
        entry.data.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_DEFAULT),        
    )

    api_client.enable()
   
    hass.data[DOMAIN][entry.entry_id] = api_client

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