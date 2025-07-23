import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import config_validation as cv

from .oauth import OAuth2Client
from .api_client import SmappeeApiClient
from .const import (DOMAIN, CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_USERNAME, CONF_PASSWORD, CONF_SERIAL)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
            
async def async_setup_entry(hass: HomeAssistant, entry):
    """Set up Smappee Charging Profiles from a config entry."""
    
    _LOGGER.debug("Setting up entry for Smappee EV. Serial: ")
    _LOGGER.debug(entry.data.get(CONF_SERIAL))
    # Initialize the API client
    _LOGGER.debug("Init OAuth...")
    oauth_client = OAuth2Client(entry.data)
    _LOGGER.debug("Init OAuth...done")
            
   # retreive all required data
    serial = entry.data.get(CONF_SERIAL)
    service_location_id = entry.data.get("service_location_id")
    smart_device_uuid = entry.data.get("smart_device_uuid")

    # Evaluate if everything is present
    if not serial or not service_location_id or not smart_device_uuid:
        _LOGGER.error("Vereiste entry data ontbreekt: serial (%s), service_location_id (%s), smart_device_uuid (%s)",
                      serial, service_location_id, smart_device_uuid)
        return False
         
    _LOGGER.debug("Init API...")    
    api_client = SmappeeApiClient(
        oauth_client, 
        serial,
        smart_device_uuid,
        service_location_id)
    _LOGGER.debug("Init API...done")    

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
    # Register the set_charging_mode service (now called actions in Home Assistant)
    @callback
    def set_charging_mode_service(call):
        """Handle the action to set the charging mode."""
        _LOGGER.debug('SET CHARGING MODE SERVICE: Received data', call.data)
        serial = call.data.get("serial")
        mode = call.data.get("mode")
        limit = call.data.get("limit", 0)

        _LOGGER.info(f"Setting charging mode for serial {serial} to {mode} with limit {limit}.")
       
        #api_client = hass.data[DOMAIN][entry.entry_id]
    
        #try:
        #    await api_client.set_charging_mode(serial, mode, limit)
        #    _LOGGER.info(f"Charging mode set successfully for {serial}")
        #except Exception as e:
        #    _LOGGER.error(f"Failed to set charging mode for {serial}: {e}")
        #    raise  # Ensures that the exception is re-raised and properly logged
            
    _LOGGER.debug('Set charging mode service in HA...')
    hass.services.async_register(DOMAIN, "set_charging_mode", set_charging_mode_service)
    _LOGGER.debug('Set charging mode service in HA...done')
    return True
