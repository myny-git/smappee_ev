from homeassistant.components.select import SelectEntity
from .const import DOMAIN

MODES = ["SMART", "SOLAR", "NORMAL", "NORMAL_PERCENTAGE"]  # Voeg toe wat je ondersteunt!

async def async_setup_entry(hass, config_entry, async_add_entities):
    api_client = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([SmappeeModeSelect(api_client)], update_before_add=True)

class SmappeeModeSelect(SelectEntity):
    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = "Smappee Charging Mode"
        self._attr_options = MODES
        self._attr_unique_id = f"{api_client.serial_id}_mode_select"

    @property
    def current_option(self):
        return self.api_client.getSessionState  # Of hoe je huidige mode uit je api_client leest

    async def async_select_option(self, option):
        await self.api_client.set_charging_mode(option, 0)  # 0 als default limit, of kies passend
        self.async_write_ha_state()
