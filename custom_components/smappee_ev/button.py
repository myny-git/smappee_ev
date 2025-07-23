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
        SmappeeSetChargingModeButton(api_client, hass)
    ])

class SmappeeSetChargingModeButton(ButtonEntity):
    def __init__(self, api_client, hass):
        self.api_client = api_client
        self.hass = hass
        self._attr_name = "Set Charging Mode"
        self._attr_unique_id = f"{api_client.serial_id}_set_charging_mode"

    async def async_press(self) -> None:
        serial = self.api_client.serial_id
        mode_entity_id = f"select.smappee_charging_mode_{serial}"
        current_entity_id = f"number.smappee_current_limit_{serial}"
        percent_entity_id = f"number.smappee_percentage_limit_{serial}"

        mode_state = self.hass.states.get(mode_entity_id)
        current_state = self.hass.states.get(current_entity_id)
        percent_state = self.hass.states.get(percent_entity_id)

        if mode_state is None:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": f"Kan mode entity '{mode_entity_id}' niet vinden.",
                    "title": "Smappee Button"
                },
                blocking=True,
            )
            return

        mode = mode_state.state

        current = None
        percent = None
        if current_state is not None:
            try:
                current = float(current_state.state)
            except (ValueError, TypeError):
                current = None
        if percent_state is not None:
            try:
                percent = float(percent_state.state)
            except (ValueError, TypeError):
                percent = None

        if mode == "NORMAL":
            limit = current if current is not None else 6
        elif mode == "NORMAL_PERCENTAGE":
            limit = percent if percent is not None else 10
        else:
            limit = 0

        await self.api_client.set_charging_mode(mode, limit)
