import logging
import random

from datetime import datetime, timedelta

from .oauth import OAuth2Client
from .api_client import SmappeeApiClient
from .const import (DOMAIN, CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_USERNAME, CONF_PASSWORD, CONF_SERIAL)

from homeassistant.components.sensor import (SensorEntity, SensorDeviceClass, SensorStateClass)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=20)

async def async_setup_entry(
    hass: HomeAssistant, 
    config_entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback,
) -> None:    
    _LOGGER.debug("Sensor async_setup_entry init...")
    _LOGGER.debug(config_entry.data.get(CONF_SERIAL))

    #Initialize the API client
    
    new_devices = []
    new_devices.append(ChargingPointSessionState(config_entry))
    if new_devices:
        async_add_entities(new_devices)    

    new_devices = []
    new_devices.append(ChargingPointLatestCounter(config_entry))
    if new_devices:
        async_add_entities(new_devices)    
    _LOGGER.debug("Sensor async_setup_entry init...done")
    return True


class SensorBase(Entity):
    should_poll = True
    _attr_should_poll = True
    
    def __init__(self, config_entry):
        _LOGGER.info("Sensor init...")
        self._config_entry = config_entry
        self.username = config_entry.data.get(CONF_USERNAME)
        self.pasw = config_entry.data.get(CONF_PASSWORD)
        self.oauth_client = OAuth2Client(config_entry.data)
        self.api_client = SmappeeApiClient(
            self.oauth_client, 
            config_entry.data.get(CONF_SERIAL);
            config_entry.data.get("smart_device_uuid"),
            config_entry.data.get("service_location_id")
        )
        self.api_client.enable
        _LOGGER.info("Sensor init...done")
        
    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self._config_entry.data.get(CONF_SERIAL))}}

    @property
    def available(self) -> bool:
        return True
     
    async def async_added_to_hass(self):
        # Sensors should also register callbacks to HA when their state changes
        self.api_client.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        # The opposite of async_added_to_hass. Remove any registered call backs here.
        self.api_client.remove_callback(self.async_write_ha_state)


class ChargingPointLatestCounter(SensorBase):
    def __init__(self, config_entry):
        """Initialize the sensor."""
        _LOGGER.debug("ChargingPointLatestCounter init...")
        super().__init__(config_entry)
        self._attr_unique_id = f"{config_entry.data.get(CONF_SERIAL)}_counter"
        self._attr_name = f"Charging point {config_entry.data.get(CONF_SERIAL)} total counter"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_icon = "mdi:ev-station"
        self._attr_unit_of_measurement = "kWh"
        _LOGGER.debug("ChargingPointLatestCounter init...done")

    @property
    def available(self) -> bool:
        if self.api_client.fetchLatestSessionCounter == 0: 
            return False
        return True

    @property
    def device_class(self) -> str | None:
        return self._attr_device_class
        
    @property
    def state(self):
        """Return the state of the sensor."""
        _LOGGER.debug("Get ChargingPointLatestCounter.state...")
        return self.api_client.fetchLatestSessionCounter

    @property
    def state_class(self) -> str:
        return self._attr_state_class

class ChargingPointSessionState(SensorBase):
    _native_value = "str"
    _native_unit_of_measurement = "str"

    def __init__(self, config_entry):
        """Initialize the sensor."""
        _LOGGER.debug("ChargingPointSessionState init...")
        super().__init__(config_entry)
        self._attr_unique_id = f"{config_entry.data.get(CONF_SERIAL)}_session_state"
        self._attr_name = f"Charging point {config_entry.data.get(CONF_SERIAL)} session state"
        _LOGGER.debug("ChargingPointSessionState init...done")

    @property
    def available(self) -> bool:
        if self.api_client.fetchLatestSessionCounter == 0: 
            return False
        return True
    
    @property
    def state(self):
        """Return the state of the sensor."""
        _LOGGER.debug("Get ChargingPointSessionState.state...")
        return self.api_client.getSessionState
