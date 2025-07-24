from homeassistant.components.select import SelectEntity
from .const import DOMAIN

MODES = ["SMART", "SOLAR", "NORMAL", "NORMAL_PERCENTAGE"]  # include what is supported

async def async_setup_entry(hass, config_entry, async_add_entities):
    api_client = hass.data[DOMAIN][config_entry.entry_id]
    select_entity = SmappeeModeSelect(api_client)
    async_add_entities([SmappeeModeSelect(api_client)], update_before_add=True)

    # Register the callback so the API can update the select
    api_client.set_mode_select_callback(select_entity.set_selected_mode)

class SmappeeModeSelect(SelectEntity):
    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = f"Smappee Charging Mode {api_client.serial_id}"
        self._attr_options = MODES
        self._selected_mode = MODES[0] 
        self._attr_unique_id = f"{api_client.serial_id or 'unknown'}_mode_select"

    @property
    def current_option(self):
        return self._selected_mode

    async def async_select_option(self, option):
        self._selected_mode = option
        self.api_client.selected_mode = option
        self.async_write_ha_state()

    async def set_selected_mode(self, option: str):
        """Externally set the selected mode from the API client."""
        self._selected_mode = option
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }    
