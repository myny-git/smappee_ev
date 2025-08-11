from __future__ import annotations
import logging

from typing import Dict
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import IntegrationData, ConnectorState

_LOGGER = logging.getLogger(__name__)

MODES = ["SMART", "SOLAR", "NORMAL"]  

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV mode select entity from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: Dict[str, SmappeeApiClient] = data["connector_clients"] 

    entities: list[SelectEntity] = []
    for uuid, client in connector_clients.items():
        entities.append(SmappeeModeSelect(coordinator, client, uuid))

    async_add_entities(entities)


class SmappeeModeSelect(CoordinatorEntity[SmappeeCoordinator], SelectEntity):
    """Home Assistant select entity for Smappee charging mode."""

    _attr_has_entity_name = True
    _attr_options = MODES

    def __init__(self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, connector_uuid: str) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._connector_uuid = connector_uuid
        self._attr_name = f"Charging Mode {api_client.connector_number}"
        self._attr_unique_id = f"{api_client.serial_id}_charging_mode_{api_client.connector_number}"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        if not data:
            return None
        return data.connectors.get(self._connector_uuid)

    async def _async_refresh(self) -> None:
        """Small shared helper: fetch coordinator and refresh once."""
        try:
            await self.coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.debug("Coordinator refresh failed after select change: %s", exc)


    @property
    def current_option(self) -> str:
        st = self._state()
        if st and st.selected_mode in MODES:
            return st.selected_mode
        # fallback to client
        return getattr(self.api_client, "selected_mode", "NORMAL")

    async def async_select_option(self, option: str) -> None:
        """Change the selected mode and update the API client."""
        if option not in MODES:
            _LOGGER.warning("Unsupported mode selected: %s", option)
            return            
        self.api_client.selected_mode = option
        self.async_write_ha_state()
        #await self.coordinator.async_request_refresh()
        await self._async_refresh()
