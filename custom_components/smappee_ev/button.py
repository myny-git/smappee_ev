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
        # Bepaal de entity_id's van je select en number
        mode_entity_id = f"select.smappee_charging_mode_{self.api_client.serial_id}"
        # Gebruik de juiste entity_id's die jouw entities in Home Assistant hebben!
        # Pas eventueel aan naar jouw naamgevingsconventie

        # Kies hier welke limit je wilt gebruiken (current_limit of percentage_limit)
        limit_entity_id = f"number.smappee_current_limit_{self.api_client.serial_id}"
        # of voor percentage:
        # limit_entity_id = f"number.smappee_percentage_limit_{self.api_client.serial_id}"

        mode_state = self.hass.states.get(mode_entity_id)
        limit_state = self.hass.states.get(limit_entity_id)

        if mode_state is None or limit_state is None:
            # Je kunt hier loggen of een fout afhandelen
            return

        mode = mode_state.state
        try:
            limit = float(limit_state.state)
        except (ValueError, TypeError):
            limit = 0

        await self.api_client.set_charging_mode(mode, limit)
