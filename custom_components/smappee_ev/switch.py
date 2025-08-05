import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV switch entity from config entry."""
    api_client = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([SmappeeChargingSwitch(api_client)], update_before_add=True)


class SmappeeChargingSwitch(SwitchEntity):
    """Switch entity to control start/pause charging."""

    _attr_has_entity_name = True

    def __init__(self, api_client):
        self.api_client = api_client
        self._attr_name = "Charging Control"
        self._attr_unique_id = f"{api_client.serial_id}_charging_switch"
        self._is_on = False


    async def async_turn_on(self, **kwargs):
        """Start charging at 6A."""
        _LOGGER.info("Switch turned ON: starting charging at 6A")
        await self.api_client.start_charging_current(6)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Pause charging."""
        _LOGGER.info("Switch turned OFF: pausing charging")
        await self.api_client.pause_charging()
        self._is_on = False
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_added_to_hass(self):
        """Restore previous state if needed."""
        self._is_on = False  # default on boot
        _LOGGER.debug("SmappeeChargingSwitch initialized with is_on = False")
        
    @property
    def device_info(self):
        """Return device information for correct device grouping."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }        
