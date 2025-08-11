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
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: Dict[str, SmappeeApiClient] = data["connector_clients"]  # keyed by UUID

    entities: list[SmappeeChargingSwitch] = []
    # Create one EVCC Charging Control switch per connector
    for client in connector_clients.values():
        entities.append(SmappeeChargingSwitch(client, coordinator))

    async_add_entities(entities)


class SmappeeChargingSwitch(SwitchEntity):

    """Switch entity to control start/pause charging."""

    _attr_has_entity_name = True

    def __init__(self, api_client: SmappeeApiClient, coordinator: SmappeeCoordinator) -> None:
        self.api_client = api_client
        self._coordinator = coordinator
        self._attr_name = f"EVCC Charging Control {api_client.connector_number}"
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

    async def async_added_to_hass(self) -> None:
        """Initialize local state on add."""
        self._is_on = False
        _LOGGER.debug("Initialized %s with is_on = False", self._attr_unique_id)

    async def _async_refresh(self) -> None:
        """Trigger one coordinator refresh so UI updates immediately."""
        try:
            await self._coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.debug("Coordinator refresh failed after switch action: %s", exc)

    async def async_turn_on(self, **kwargs) -> None:
        """Start charging at current selected limit (fallback to min_current)."""
        current = None
        try:
            data = self.hass.data[DOMAIN][self.platform.config_entry.entry_id]
            coordinator: SmappeeCoordinator | None = data.get("coordinator")
        except Exception:
            coordinator = None

        if coordinator and coordinator.data:
            for uuid, st in coordinator.data.connectors.items():
                if st.connector_number == self.api_client.connector_number:
                    current = st.selected_current_limit if st.selected_current_limit is not None else st.min_current
                    break

        if current is None:
            current = max(getattr(self.api_client, "min_current", 6), 1)

        _LOGGER.info("Switch ON: starting charging at %sA on connector %s", current, self.api_client.connector_number)
        await self.api_client.start_charging(int(current))
        self._is_on = True
        self.async_write_ha_state()
        await self._async_refresh()


    async def async_turn_off(self, **kwargs) -> None:
        """Pause charging."""
        _LOGGER.info("Switch OFF: pausing charging on connector %s", self.api_client.connector_number)
        await self.api_client.pause_charging()
        self._is_on = False
        self.async_write_ha_state()
        await self._async_refresh()
