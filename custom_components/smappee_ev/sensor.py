import logging

from . import SmappeeChargerCoordinator
from .const import DOMAIN

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

def async_setup_entry(
    hass: HomeAssistant, 
    config_entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback,
) -> None:    

    new_devices = []
    new_devices.append(ChargingPointSensor(config_entry.runtime_data.smappee))
    if new_devices:
        async_add_entities(new_devices)    
    return True

class SensorBase(Entity):
    should_poll = True

    def __init__(self, smappee):
        self._smappee = smappee

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self._smappee.serial)}}

    @property
    def available(self) -> bool:
        return self._smappee.online

    async def async_added_to_hass(self):
        # Sensors should also register callbacks to HA when their state changes
        self._smappee.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        # The opposite of async_added_to_hass. Remove any registered call backs here.
        self._smappee.remove_callback(self.async_write_ha_state)

class ChargingPointSensor(SensorBase):
    device_class = SensorDeviceClass.ENERGY
    _attr_unit_of_measurement = "kWh"

    def __init__(self, smappee):
        """Initialize the sensor."""
        _LOGGER.debug("ChargingPointSensor init...")
        super().__init__(smappee)
        self._attr_unique_id = f"{self._smappee.serial}_counter"
        self._attr_name = f"{self._smappee.name} Charging point counter"
        self._state = random.randint(0, 100)
        _LOGGER.debug("ChargingPointSensor init...done")

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._smappee.cp_counter
