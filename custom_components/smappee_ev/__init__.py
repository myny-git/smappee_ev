import logging
import asyncio 

from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry 
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv

from .oauth import OAuth2Client
from .api_client import SmappeeApiClient
from .const import (
    DOMAIN, 
    CONF_CLIENT_ID, 
    CONF_CLIENT_SECRET, 
    CONF_USERNAME, 
    CONF_PASSWORD, 
    CONF_SERIAL,
    CONF_SERVICE_LOCATION_ID,
    CONF_SMART_DEVICE_UUID,
    CONF_SMART_DEVICE_ID,    
    SERVICE_SET_CHARGING_MODE,
    SERVICE_PAUSE_CHARGING,
    SERVICE_STOP_CHARGING,
    SERVICE_START_CHARGING,
    SERVICE_SET_BRIGHTNESS,
    SERVICE_SET_AVAILABLE,
    SERVICE_SET_UNAVAILABLE,
    SERVICE_RELOAD,
    CONF_UPDATE_INTERVAL,
    UPDATE_INTERVAL_DEFAULT,    
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.BUTTON]
CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Smappee EV component"""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Smappee EV config entry."""
    _LOGGER.debug("Setting up entry for Smappee EV. Serial: %s", entry.data.get(CONF_SERIAL))

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
        if not hass.data[DOMAIN]:
            unregister_services(hass)
    return unload_ok

async def async_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle updates to config entry options."""
    _LOGGER.debug("Config entry updated: %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)    

def get_api_client(hass: HomeAssistant, entry_id: str | None = None) -> SmappeeApiClient | None:
    """Return the api_client for the entry, or the first if not specified."""
    data = hass.data.get(DOMAIN, {})
    if entry_id:
        return data.get(entry_id)
    return next(iter(data.values()), None)    

def register_services(hass: HomeAssistant) -> None:
    """Register all Smappee EV services."""

    async def async_set_charging_mode_service(call: ServiceCall):
        api_client = get_api_client(hass)
        if not api_client:
            _LOGGER.error("No Smappee API client found for 'set_charging_mode' service.")
            return
        mode = call.data.get("mode")
        if mode in ["SMART", "SOLAR"]:
            _LOGGER.debug("Service: set_charging_mode (mode=%s)", mode)
            await api_client.set_charging_mode(mode)
        else:
            limit = call.data.get("limit", 0)
            _LOGGER.debug("Service: set_charging_mode (mode=%s, limit=%s)", mode, limit)
            await api_client.set_charging_mode(mode, limit)

    async def async_pause_charging_service(call: ServiceCall):
        api_client = get_api_client(hass)
        if not api_client:
            _LOGGER.error("No Smappee API client found for 'pause_charging' service.")
            return
        _LOGGER.debug("Service: pause_charging")
        await api_client.pause_charging()

    async def async_stop_charging_service(call: ServiceCall):
        api_client = get_api_client(hass)
        if not api_client:
            _LOGGER.error("No Smappee API client found for 'stop_charging' service.")
            return
        _LOGGER.debug("Service: stop_charging")
        await api_client.stop_charging()

    async def async_start_charging_service(call: ServiceCall):
        api_client = get_api_client(hass)
        if not api_client:
            _LOGGER.error("No Smappee API client found for 'start_charging' service.")
            return
        percentage = call.data.get("percentageLimit", 100)
        _LOGGER.debug("Service: start_charging (percentage=%s)", percentage)
        await api_client.start_charging(percentage)

    async def async_set_brightness_service(call: ServiceCall):
        api_client = get_api_client(hass)
        if not api_client:
            _LOGGER.error("No Smappee API client found for 'set_brightness' service.")
            return
        brightness = call.data.get("brightness", 10)
        _LOGGER.debug("Service: set_brightness (brightness=%s)", brightness)
        await api_client.set_brightness(brightness)

    async def async_set_available_service(call: ServiceCall):
        api_client = get_api_client(hass)
        if not api_client:
            _LOGGER.error("No Smappee API client found for 'set_available' service.")
            return
        _LOGGER.debug("Service: set_available")
        await api_client.set_available()

    async def async_set_unavailable_service(call: ServiceCall):
        api_client = get_api_client(hass)
        if not api_client:
            _LOGGER.error("No Smappee API client found for 'set_unavailable' service.")
            return
        _LOGGER.debug("Service: set_unavailable")
        await api_client.set_unavailable()

    async def async_reload_service(call: ServiceCall):
        _LOGGER.info("Service: reload called - reloading all Smappee EV entries.")
        current_entries = hass.config_entries.async_entries(DOMAIN)
        reload_tasks = [
            hass.config_entries.async_reload(entry.entry_id)
            for entry in current_entries
        ]
        await asyncio.gather(*reload_tasks)

    # Register each service with its own handler
    hass.services.async_register(DOMAIN, SERVICE_SET_CHARGING_MODE, async_set_charging_mode_service)
    hass.services.async_register(DOMAIN, SERVICE_PAUSE_CHARGING, async_pause_charging_service)
    hass.services.async_register(DOMAIN, SERVICE_STOP_CHARGING, async_stop_charging_service)
    hass.services.async_register(DOMAIN, SERVICE_START_CHARGING, async_start_charging_service)
    hass.services.async_register(DOMAIN, SERVICE_SET_BRIGHTNESS, async_set_brightness_service)
    hass.services.async_register(DOMAIN, SERVICE_SET_AVAILABLE, async_set_available_service)
    hass.services.async_register(DOMAIN, SERVICE_SET_UNAVAILABLE, async_set_unavailable_service)
    hass.services.async_register(DOMAIN, SERVICE_RELOAD, async_reload_service)
    _LOGGER.debug("All Smappee EV services registered.")

def unregister_services(hass: HomeAssistant) -> None:
    """Unregister all Smappee EV services."""
    for service in [
        SERVICE_SET_CHARGING_MODE,
        SERVICE_PAUSE_CHARGING,
        SERVICE_STOP_CHARGING,
        SERVICE_START_CHARGING,
        SERVICE_SET_BRIGHTNESS,
        SERVICE_SET_AVAILABLE,
        SERVICE_SET_UNAVAILABLE,
        SERVICE_RELOAD,
    ]:
        hass.services.async_remove(DOMAIN, service)
    _LOGGER.debug("All Smappee EV services unregistered.")    