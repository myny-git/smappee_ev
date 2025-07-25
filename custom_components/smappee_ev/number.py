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
        SmappeeBrightnessNumber(NumberEntity)        
    ])

class SmappeeCurrentLimitNumber(NumberEntity):
    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = f"Smappee Current Limit {api_client.serial_id}"
        self._attr_unique_id = f"{api_client.serial_id}_current_limit"
        self._attr_native_unit_of_measurement = "A"
        self._attr_native_min_value = 6  # Pas aan indien nodig
        self._attr_native_max_value = 32  # Pas aan indien nodig
        # Startwaarde: laad uit api_client of default
        self._current_value = getattr(api_client, "current_limit", 6)

    @property
    def native_value(self):
        return self._current_value

    async def async_set_native_value(self, value):
        # Alleen waarde onthouden, niet direct de API aanroepen!
        self._current_value = value
        # Optioneel: ook opslaan in api_client voor ophalen door button
        self.api_client.selected_current_limit = value
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

class SmappeePercentageLimitNumber(NumberEntity):
    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = f"Smappee Percentage Limit {api_client.serial_id}"
        self._attr_unique_id = f"{api_client.serial_id}_percentage_limit"
        self._attr_native_unit_of_measurement = "%"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        # Startwaarde: laad uit api_client of default
        self._current_value = getattr(api_client, "percentage_limit", 0)

    @property
    def native_value(self):
        return self._current_value

    async def async_set_native_value(self, value):
        # Alleen waarde onthouden, niet direct de API aanroepen!
        self._current_value = value
        # Optioneel: ook opslaan in api_client voor ophalen door button
        self.api_client.selected_percentage_limit = value
        self.async_write_ha_state()
        
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

class SmappeeBrightnessNumber(NumberEntity):
    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = f"Smappee LED Brightness {api_client.serial_id}"
        self._attr_unique_id = f"{api_client.serial_id}_led_brightness"
        self._attr_native_unit_of_measurement = "%"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        # Startwaarde: laad uit api_client of default
        self._current_value = getattr(api_client, "led_brightness", 70)

    @property
    def native_value(self):
        return self._current_value

    async def async_set_native_value(self, value):
        self._current_value = int(value)
        await self.api_client.set_brightness(self._current_value)
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

