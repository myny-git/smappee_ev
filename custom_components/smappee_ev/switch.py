import logging

from typing import Any
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN
from .api_client import SmappeeApiClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV switch entity from config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    connector_clients: dict[str, SmappeeApiClient] = data["connectors"]

    entities: list[SwitchEntity] = []
    # Create one EVCC Charging Control switch per connector
    for client in connector_clients.values():
        entities.append(SmappeeChargingSwitch(client))

    async_add_entities(entities, update_before_add=True)


class SmappeeChargingSwitch(SwitchEntity):
    """Switch entity to control start/pause charging."""

    _attr_has_entity_name = True

    def __init__(self, api_client: SmappeeApiClient):
        self.api_client = api_client
        self._attr_name = f"EVCC Charging Control {api_client.connector_number}"
        self._attr_unique_id = f"{api_client.serial_id}_evcc_charging_switch_{api_client.connector_number}"
        self._is_on = False


    async def async_turn_on(self, **kwargs):
        """Start charging at 6A."""

        connector = self.api_client.connector_number
        _LOGGER.info("Switch ON: starting charging at 6A on connector %s", connector)
        await self.api_client.start_charging_current(6)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Pause charging."""
        connector = self.api_client.connector_number
        _LOGGER.info("Switch OFF: pausing charging on connector %s", connector)
        await self.api_client.pause_charging()
        self._is_on = False
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._is_on

    async def async_added_to_hass(self):
        """Restore previous state if needed."""
        self._is_on = False  # default on boot
        _LOGGER.debug("Initialized EVCCChargingSwitch %s with is_on = False", self._attr_unique_id) 

    @property
    def device_info(self):
        """Return device information for correct device grouping."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }        
