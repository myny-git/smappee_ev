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

    async def call_service(call: ServiceCall, action: str):
        """Central dispatcher for all services."""
        api_client = get_api_client(hass)
        if not api_client:
            _LOGGER.error("No Smappee API client found for '%s' service.", action)
            return

        if action == SERVICE_SET_CHARGING_MODE:
            mode = call.data.get("mode")
            if mode in ["SMART", "SOLAR"]:
                _LOGGER.debug("Service: set_charging_mode (mode=%s)", mode)
                hass.async_create_task(api_client.set_charging_mode(mode))  # limit not necessary
            else:
                limit = call.data.get("limit", 0)
                _LOGGER.debug("Service: set_charging_mode (mode=%s, limit=%s)", mode, limit)
                hass.async_create_task(api_client.set_charging_mode(mode, limit))
        elif action == SERVICE_PAUSE_CHARGING:
            _LOGGER.debug("Service: pause_charging")
            hass.async_create_task(api_client.pause_charging())
        elif action == SERVICE_STOP_CHARGING:
            _LOGGER.debug("Service: stop_charging")
            hass.async_create_task(api_client.stop_charging())
        elif action == SERVICE_START_CHARGING:
            percentage = call.data.get("percentageLimit", 100)
            _LOGGER.debug("Service: start_charging (percentage=%s)", percentage)
            hass.async_create_task(api_client.start_charging(percentage))
        elif action == SERVICE_SET_BRIGHTNESS:
            brightness = call.data.get("brightness", 10)
            _LOGGER.debug("Service: set_brightness (brightness=%s)", brightness)
            hass.async_create_task(api_client.set_brightness(brightness))
        elif action == SERVICE_SET_AVAILABLE:
            _LOGGER.debug("Service: set_available")
            hass.async_create_task(api_client.set_available())
        elif action == SERVICE_SET_UNAVAILABLE:
            _LOGGER.debug("Service: set_unavailable")
            hass.async_create_task(api_client.set_unavailable())
        elif action == SERVICE_RELOAD:
            _LOGGER.info("Service: reload called - reloading all Smappee EV entries.")
            current_entries = hass.config_entries.async_entries(DOMAIN)
            reload_tasks = [
                hass.config_entries.async_reload(entry.entry_id)
                for entry in current_entries
            ]
            await asyncio.gather(*reload_tasks)
        else:
            _LOGGER.warning("Unknown service action: %s", action)
  
    # Registreer de services via de constants
    hass.services.async_register(DOMAIN, SERVICE_SET_CHARGING_MODE, lambda call: call_service(call, SERVICE_SET_CHARGING_MODE))
    hass.services.async_register(DOMAIN, SERVICE_PAUSE_CHARGING, lambda call: call_service(call, SERVICE_PAUSE_CHARGING))
    hass.services.async_register(DOMAIN, SERVICE_STOP_CHARGING, lambda call: call_service(call, SERVICE_STOP_CHARGING))
    hass.services.async_register(DOMAIN, SERVICE_START_CHARGING, lambda call: call_service(call, SERVICE_START_CHARGING))
    hass.services.async_register(DOMAIN, SERVICE_SET_BRIGHTNESS, lambda call: call_service(call, SERVICE_SET_BRIGHTNESS))
    hass.services.async_register(DOMAIN, SERVICE_SET_AVAILABLE, lambda call: call_service(call, SERVICE_SET_AVAILABLE))
    hass.services.async_register(DOMAIN, SERVICE_SET_UNAVAILABLE, lambda call: call_service(call, SERVICE_SET_UNAVAILABLE))
    hass.services.async_register(DOMAIN, SERVICE_RELOAD, lambda call: call_service(call, SERVICE_RELOAD))
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