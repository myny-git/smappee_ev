import logging
from homeassistant.core import HomeAssistant

from .oauth import OAuth2Client
from .api_client import SmappeeApiClient
from .const import (DOMAIN, CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_USERNAME, CONF_PASSWORD, CONF_SERIAL)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry):
    """Set up Smappee Charging Profiles from a config entry."""
    
    _LOGGER.debug("Setting up entry for Smappee EV. Serial: ")
    _LOGGER.debug(entry.data.get(CONF_SERIAL))
    # Initialize the API client
    _LOGGER.debug("Init OAuth...")
    oauth_client = OAuth2Client(entry.data)
    _LOGGER.debug("Init OAuth...done")
    _LOGGER.debug("Init API...")    
    api_client = SmappeeApiClient(oauth_client)
    _LOGGER.debug("Init API...done")    

    _LOGGER.debug("Store API client in hass.data...") 
    # Store the API client in hass.data
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    hass.data[DOMAIN][entry.entry_id] = api_client
    _LOGGER.debug("Store API client in hass.data...done") 
    
    # Register the service/action in Home Assistant
    _LOGGER.debug("Set charging mode in HA...")
    
    # Register the set_charging_mode service (now called actions in Home Assistant)
    async def set_charging_mode_service(call):
        """Handle the action to set the charging mode."""
        serial = call.data.get(CONF_SERIAL)
        mode = call.data.get("mode")
        limit = call.data.get("limit", 0)

        _LOGGER.info(f"Setting charging mode for serial {serial} to {mode} with limit {limit}.")
        
        api_client = hass.data[DOMAIN][entry.entry_id]
    
        try:
            await api_client.set_charging_mode(serial, mode, limit)
            _LOGGER.info(f"Charging mode set successfully for {serial}")
        except Exception as e:
            _LOGGER.error(f"Failed to set charging mode for {serial}: {e}")
            raise  # Ensures that the exception is re-raised and properly logged
    
    _LOGGER.debug("Set charging mode in HA2...")
    hass.states.async_set('smappee_ev.Hello_World', 'Works!')
    hass.services.async_register(DOMAIN, "set_charging_mode", set_charging_mode_service)
    _LOGGER.debug("Set charging mode in HA...done")    

    return True
