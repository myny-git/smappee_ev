from __future__ import annotations
import logging

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api_client import SmappeeApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV number entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    connector_clients: dict[str, SmappeeApiClient] = data["connectors"]
    station_client: SmappeeApiClient = data["station"]

    entities: list[NumberEntity] = []
    for client in connector_clients.values():
        entities.append(SmappeeCombinedCurrentSlider(client))
        entities.append(SmappeeMinSurplusPctNumber(client))

    entities.append(SmappeeBrightnessNumber(station_client))
    async_add_entities(entities)


class SmappeeBaseNumber(NumberEntity):
    """Base class for Smappee EV numbers."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        api_client: Any,
        name: str,
        unique_id: str,
        unit: str,
        min_value: int,
        max_value: int,
        step: int = 1,
        initial_value: int = None,
    ) -> None:
        self.api_client = api_client
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        self._current_value = int(initial_value) if initial_value is not None else min_value

    @property
    def device_info(self):
        """Return device info for the wallbox."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

class SmappeeCombinedCurrentSlider(SmappeeBaseNumber):
    """Combined slider showing current and percentage."""

    def __init__(self, api_client: Any):
        self._attr_unit_of_measurement = "A"
        self.min_current = api_client.min_current
        self.max_current = api_client.max_current        
        self.range = self.max_current - self.min_current

        initial_current = int(getattr(api_client, "current_limit", self.min_current))

        super().__init__(
            api_client,
            f"Max charging speed {api_client.connector_number}",
             f"{api_client.serial_id}_connector{api_client.connector_number}_combined_current",
            "A", 
            min_value=self.min_current,
            max_value=self.max_current,
            initial_value=initial_current,
        )



        api_client.register_value_callback("current_limit", self._handle_external_update)
        api_client.register_value_callback("percentage_limit", self._handle_percentage_update)

    @property
    def native_value(self) -> int:
        return int(self._current_value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = {}
        if self._current_value is not None:
            percentage = round((self._current_value - self.min_current) / self.range * 100)
            attributes["percentage"] = percentage
            attributes["percentage_formatted"] = f"{percentage}%"
        return attributes

    async def async_set_native_value(self, value: int) -> None:
        value = max(self.min_current, min(value, self.max_current))  # Clamp value to [min, max]

        self._current_value = int(value)
        self.api_client.selected_current_limit = self._current_value
        percentage = round((value - self.min_current) / self.range * 100)
        self.api_client.selected_percentage_limit = percentage

        # Make API call here, when users change the number or move the slider
        await self.api_client.set_percentage_limit(percentage)

        self.async_write_ha_state()

    def _handle_external_update(self, value: int) -> None:
        self._current_value = value
        self.async_write_ha_state()

    def _handle_percentage_update(self, value: int) -> None:
        """Update current (A) when we retrieve the percentage."""
        current = round((value / 100) * self.range + self.min_current)
        self._current_value = current
        self.async_write_ha_state()


class SmappeeBrightnessNumber(SmappeeBaseNumber):
    """LED brightness setting for Smappee EV."""

    def __init__(self, api_client: Any):
        super().__init__(
            api_client,
            "LED Brightness",
            f"{api_client.serial_id}_led_brightness",
            "%",
            min_value=0,
            max_value=100,
            step=1,
            initial_value=int(getattr(api_client, "led_brightness", 70)),
        )

    async def async_set_native_value(self, value: int) -> None:
        self._current_value = int(value)
        await self.api_client.set_brightness(self._current_value)  # push to the cloud
        self.api_client.led_brightness = self._current_value       # keep local in sync
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return int(self.api_client.led_brightness)     

class SmappeeMinSurplusPctNumber(SmappeeBaseNumber):
    """Min Surplus Percentage (slider) for Smappee EV."""

    def __init__(self, api_client):
        super().__init__(
            api_client,
            f"Min Surplus Percentage {api_client.connector_number}",
            f"{api_client.serial_id}_connector{api_client.connector_number}_min_surpluspct",
            "%",
            min_value=0,
            max_value=100,
            step=1,
            initial_value=int(getattr(api_client, "min_surpluspct", 100)),
        )
        api_client.register_value_callback("min_surpluspct", self._handle_external_update)

    async def async_set_native_value(self, value: int) -> None:
        self._current_value = int(value)
        await self.api_client.set_min_surpluspct(self._current_value)
        self.api_client.min_surpluspct = self._current_value
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return int(getattr(self.api_client, "min_surpluspct", 100))

    def _handle_external_update(self, value: int) -> None:
        self._current_value = value
        self.async_write_ha_state()