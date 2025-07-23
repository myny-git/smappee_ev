from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    api_client = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([
        SmappeeCurrentLimitNumber(api_client),
        SmappeePercentageLimitNumber(api_client),
    ])

class SmappeeCurrentLimitNumber(NumberEntity):
    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = "Smappee Current Limit"
        self._attr_unique_id = f"{api_client.serial_id}_current_limit"
        self._attr_native_unit_of_measurement = "A"
        self._attr_native_min_value = 6  # Adjust as needed
        self._attr_native_max_value = 32  # Adjust as needed

    @property
    def native_value(self):
        # Replace with actual retrieval from api_client
        return self.api_client.current_limit

    async def async_set_native_value(self, value):
        await self.api_client.set_charging_mode("NORMAL", int(value))
        self.async_write_ha_state()

class SmappeePercentageLimitNumber(NumberEntity):
    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = "Smappee Percentage Limit"
        self._attr_unique_id = f"{api_client.serial_id}_percentage_limit"
        self._attr_native_unit_of_measurement = "%"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100

    @property
    def native_value(self):
        # Replace with actual retrieval from api_client
        return self.api_client.percentage_limit

    async def async_set_native_value(self, value):
        await self.api_client.set_charging_mode("SOLAR", int(value))
        self.async_write_ha_state()
