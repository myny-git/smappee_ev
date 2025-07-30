import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MODES = ["SMART", "SOLAR", "NORMAL", "NORMAL_PERCENTAGE"]  

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV mode select entity from a config entry."""
    api_client = hass.data[DOMAIN][config_entry.entry_id]
    select_entity = SmappeeModeSelect(api_client)
    async_add_entities([select_entity], update_before_add=True)

    # Register the callback once HA is fully started
    def register_callback_later(_):
        api_client.set_mode_select_callback(select_entity.set_selected_mode)
    hass.bus.async_listen_once("homeassistant_started", register_callback_later)

class SmappeeModeSelect(SelectEntity):
    """Home Assistant select entity for Smappee charging mode."""

    _attr_has_entity_name = True

    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = f"Charging Mode"
        self._attr_options = MODES
        self._selected_mode = MODES[0]
        self._attr_unique_id = f"{api_client.serial_id}_mode_select"


    @property
    def current_option(self) -> str:
        """Return the current selected charging mode."""
        return self._selected_mode

    async def async_select_option(self, option: str) -> None:
        """Change the selected mode and update the API client."""
        self._selected_mode = option
        self.api_client.selected_mode = option
        self.async_write_ha_state()

    def set_selected_mode(self, option: str) -> None:
        """Externally set the selected mode from the API client."""
        self._selected_mode = option
        if self.hass:
            _LOGGER.debug("Updating HA state for mode select to %s", option)
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Tried to update mode select before entity was added.")

    @property
    def device_info(self):
        """Device info for the wallbox, ensures correct grouping in HA."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }
