from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData

MODES = ["SMART", "SOLAR", "NORMAL"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: dict[str, SmappeeApiClient] = data["connector_clients"]

    entities: list[SelectEntity] = [
        SmappeeModeSelect(coordinator, client, uuid) for uuid, client in connector_clients.items()
    ]
    async_add_entities(entities, update_before_add=True)


class SmappeeModeSelect(CoordinatorEntity[SmappeeCoordinator], SelectEntity):
    """Home Assistant select entity for Smappee charging mode."""

    _attr_has_entity_name = True
    _attr_options = MODES

    def __init__(
        self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, connector_uuid: str
    ):
        super().__init__(coordinator)
        self.api_client = api_client
        self._connector_uuid = connector_uuid
        self._attr_name = f"Charging Mode {api_client.connector_number}"
        self._attr_unique_id = f"{api_client.serial_id}_charging_mode_{api_client.connector_number}"

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._connector_uuid) if data else None

    @property
    def current_option(self) -> str | None:
        st = self._state()
        if st and st.selected_mode:
            return st.selected_mode
        # Fallback to client's local value (or NORMAL)
        return getattr(self.api_client, "selected_mode", "NORMAL")

    async def async_select_option(self, option: str) -> None:
        # Only stage the choice here; the button actually sends the API command
        self.api_client.selected_mode = option
        if self.coordinator.data and self._connector_uuid in self.coordinator.data.connectors:
            self.coordinator.data.connectors[self._connector_uuid].selected_mode = option
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }
