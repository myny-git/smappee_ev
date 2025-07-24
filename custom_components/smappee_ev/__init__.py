import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv

from .oauth import OAuth2Client
from .api_client import SmappeeApiClient
from .const import (DOMAIN, CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_USERNAME, CONF_PASSWORD, CONF_SERIAL)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT, Platform.BUTTON]
            
async def async_setup_entry(hass: HomeAssistant, entry):
    """Set up Smappee Charging Profiles from a config entry."""
    
    _LOGGER.debug("Setting up entry for Smappee EV. Serial: ")
    _LOGGER.debug(entry.data.get(CONF_SERIAL))
    
    # Initialize the API/OAuth2 client
    _LOGGER.debug("Init OAuth...")
    oauth_client = OAuth2Client(entry.data)
    _LOGGER.debug("Init OAuth...done")
            
   # retreive all required data
    serial = entry.data.get(CONF_SERIAL)
    service_location_id = entry.data.get("service_location_id")
    smart_device_uuid = entry.data.get("smart_device_uuid")

    # Evaluate if everything is present
    if not serial or not service_location_id or not smart_device_uuid:
        _LOGGER.error("Missing required entry data: serial (%s), service_location_id (%s), smart_device_uuid (%s)",
                      serial, service_location_id, smart_device_uuid)
        return False
         
    _LOGGER.debug("Init API client...")    
    api_client = SmappeeApiClient(
        oauth_client, 
        serial,
        smart_device_uuid,
        service_location_id)
    _LOGGER.debug("API client initialized.")    
   
    _LOGGER.debug("Store API client in hass.data...") 
    # Store the API client in hass.data
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    hass.data[DOMAIN][entry.entry_id] = api_client
    _LOGGER.debug("Store API client in hass.data...done") 

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

# Use empty_config_schema because the component does not have any config options
CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Smappee EV services - now actions in Home Assistant."""
    
    @callback
    def set_charging_mode_service(call: ServiceCall):
        """Handle the action to set the charging mode."""
        _LOGGER.debug('SET CHARGING MODE SERVICE: Received data %s', call.data)
        mode = call.data.get("mode")
        limit = call.data.get("limit", 0)
        api_client = list(hass.data[DOMAIN].values())[0]
        serial = api_client.serial_id

                
                    
        if mode in ["SMART", "SOLAR"]:
            _LOGGER.info(f"Setting charging mode for serial {serial} to {mode}.")
        else:
            _LOGGER.info(f"Setting charging mode for serial {serial} to {mode} with limit {limit}.")
        hass.async_create_task(api_client.set_charging_mode(mode,limit))

            
    _LOGGER.debug('Registering set_charging_mode service...')
    hass.services.async_register(DOMAIN, "set_charging_mode", set_charging_mode_service)
    _LOGGER.debug('set_charging_mode service registered.')


    @callback
    def pause_charging_service(call: ServiceCall):
        """Handle the action to pause charging."""
        _LOGGER.debug("PAUSE CHARGING SERVICE: Triggered")
        api_client = list(hass.data[DOMAIN].values())[0]
        hass.async_create_task(api_client.pause_charging())

    _LOGGER.debug("Registering pause_charging service...")
    hass.services.async_register(DOMAIN, "pause_charging", pause_charging_service)
    _LOGGER.debug("pause_charging service registered.")


    @callback
    def stop_charging_service(call: ServiceCall):
        """Handle the action to stop charging."""
        _LOGGER.debug("STOP CHARGING SERVICE: Triggered")
        api_client = list(hass.data[DOMAIN].values())[0]
        hass.async_create_task(api_client.stop_charging())

    _LOGGER.debug("Registering stop_charging service...")
    hass.services.async_register(DOMAIN, "stop_charging", stop_charging_service)
    _LOGGER.debug("stop_charging service registered.")

    return True
