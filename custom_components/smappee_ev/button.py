from homeassistant.components.button import ButtonEntity
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
        SmappeeSetChargingModeButton(api_client, hass, config_entry.entry_id)
    ])

class SmappeeSetChargingModeButton(ButtonEntity):
    def __init__(self, api_client, hass, entry_id):
        self.api_client = api_client
        self.hass = hass
        self.entry_id = entry_id
        self._attr_name = "Set Charging Mode"
        self._attr_unique_id = f"{api_client.serial_id}_set_charging_mode"

    async def async_press(self) -> None:
        # Entity IDs
        mode_entity_id = f"select.smappee_charging_mode_{self.api_client.serial_id}"
        current_entity_id = f"number.smappee_current_limit_{self.api_client.serial_id}"
        percent_entity_id = f"number.smappee_percentage_limit_{self.api_client.serial_id}"

        mode_state = self.hass.states.get(mode_entity_id)
        current_state = self.hass.states.get(current_entity_id)
        percent_state = self.hass.states.get(percent_entity_id)

        if mode_state is None:
            # Log error if not found
            return

        mode = mode_state.state

        # Default values
        current = 6
        percent = 10

        if current_state is not None:
            try:
                current = float(current_state.state)
            except (ValueError, TypeError):
                current = 0
        if percent_state is not None:
            try:
                percent = float(percent_state.state)
            except (ValueError, TypeError):
                percent = 0

        # Select the correct limit based on mode
        if mode == "NORMAL":
            limit = current
        elif mode == "NORMAL_PERCENTAGE":
            limit = percent
        else:
            limit = current

        await self.api_client.set_charging_mode(mode, limit)
