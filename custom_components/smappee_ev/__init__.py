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
    # === get the serviceLocationId ===
    if "service_location_id" not in entry.data:
        try:
            _LOGGER.info("No service_location_id found, attempting to auto-detect...")
            token = await oauth_client.async_get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            session = hass.helpers.aiohttp_client.async_get_clientsession(hass)
            resp = await session.get("https://app1pub.smappee.net/dev/v3/servicelocation", headers=headers)
            resp.raise_for_status()
            data = await resp.json()

            locations = data.get("serviceLocations", [])
            if not locations:
                raise RuntimeError("No service locations found.")

            first_location = locations[0]
            service_location_id = first_location.get("serviceLocationId")
            _LOGGER.info(f"Detected serviceLocationId: {service_location_id}")

            # Update entry with service_location_id
            new_data = {**entry.data, "service_location_id": service_location_id}
            hass.config_entries.async_update_entry(entry, data=new_data)

        except Exception as e:
            _LOGGER.error(f"Failed to auto-detect service_location_id: {e}")
            raise
                    
    if "smart_device_uuid" not in entry.data:
        try:
            token = await oauth_client.async_get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            service_location_id = entry.data["service_location_id"]
            session = hass.helpers.aiohttp_client.async_get_clientsession(hass)
            url = f"https://app1pub.smappee.net/dev/v3/servicelocation/{service_location_id}/metering"
            resp = await session.get(url, headers=headers)
            resp.raise_for_status()
            data = await resp.json()

            # retrieve UUID 
            smart_device_uuid = data["chargingStations"][0]["chargers"][0]["uuid"]
            _LOGGER.info(f"Detected smart device UUID: {smart_device_uuid}")

            # Update entry 
            new_data = {**entry.data, "smart_device_uuid": smart_device_uuid}
            hass.config_entries.async_update_entry(entry, data=new_data)

        except Exception as e:
            _LOGGER.error(f"Failed to auto-detect smart device UUID: {e}")
            raise
         
    _LOGGER.debug("Init API...")    
    api_client = SmappeeApiClient(oauth_client, entry.data.get(CONF_SERIAL))
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
