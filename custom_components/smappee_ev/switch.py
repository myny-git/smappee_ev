from __future__ import annotations

import logging

from typing import Dict
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .api_client import SmappeeApiClient
from .coordinator import SmappeeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV switch entity from config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]

    connector_clients: Dict[str, SmappeeApiClient] = data["connector_clients"]  # keyed by UUID

    entities: list[SwitchEntity] = []
    # Create one EVCC Charging Control switch per connector
    for client in connector_clients.values():
        entities.append(SmappeeChargingSwitch(client))

    async_add_entities(entities)


class SmappeeChargingSwitch(SwitchEntity):

    """Switch entity to control start/pause charging."""

    _attr_has_entity_name = True

    def __init__(self, api_client: SmappeeApiClient) -> None:
        self.api_client = api_client
        self._attr_name = f"EVCC Charging Control {api_client.connector_number}"
        # Keep unique_id based on serial_id (your preference)
        self._attr_unique_id = f"{api_client.serial_id}_evcc_charging_switch_{api_client.connector_number}"
        self._is_on = False  # local flag only


    @property
    def device_info(self):
        """Return device information for correct device grouping."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }        

   @property
    def is_on(self) -> bool:
        """Local ON/OFF flag, not derived from session_state."""
        return self._is_on

    async def _async_refresh(self) -> None:
        """Trigger a single coordinator refresh so UI updates immediately."""
        try:
            data = self.hass.data[DOMAIN][self.platform.config_entry.entry_id]
            coordinator: SmappeeCoordinator | None = data.get("coordinator")
            if coordinator:
                await coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.debug("Coordinator refresh failed after switch action: %s", exc)

    async def async_turn_on(self, **kwargs) -> None:
        """Start charging at current selected limit (fallback to min_current)."""
        current = self.api_client.selected_current_limit or self.api_client.min_current
        _LOGGER.info(
            "Switch ON: starting charging at %sA on connector %s",
            current,
            self.api_client.connector_number,
        )
        await self.api_client.start_charging(int(current))
        # Implicitly NORMAL after starting
        self.api_client.selected_mode = "NORMAL"
        self._is_on = True
        self.async_write_ha_state()
        await self._async_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Pause charging."""
        _LOGGER.info(
            "Switch OFF: pausing charging on connector %s",
            self.api_client.connector_number,
        )
        await self.api_client.pause_charging()
        # Implicitly NORMAL after pausing
        self.api_client.selected_mode = "NORMAL"
        self._is_on = False
        self.async_write_ha_state()
        await self._async_refresh()
