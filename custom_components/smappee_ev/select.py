from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api_client import SmappeeApiClient
from .base_entities import SmappeeConnectorEntity
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData, RuntimeData
from .helpers import build_connector_label

MODES = ["SMART", "SOLAR", "NORMAL"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: RuntimeData = config_entry.runtime_data  # type: ignore[attr-defined]
    sites = runtime.sites

    entities: list[SelectEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            conns: dict[str, SmappeeApiClient] = bucket.get("connector_clients", {})

            for cuuid, client in (conns or {}).items():
                entities.append(
                    SmappeeModeSelect(
                        coordinator=coord,
                        api_client=client,
                        sid=sid,
                        station_uuid=st_uuid,
                        connector_uuid=cuuid,
                    )
                )

    async_add_entities(entities, True)


class SmappeeModeSelect(SmappeeConnectorEntity, SelectEntity, RestoreEntity):
    """Home Assistant select entity for Smappee charging mode."""

    _attr_has_entity_name = True
    _attr_options = MODES

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ):
        label = build_connector_label(api_client, connector_uuid).split(" ", 1)[1]
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="select:charging_mode",
            name=f"Charging Mode {label}",
        )
        self.api_client = api_client

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._connector_uuid) if data else None

    @property
    def current_option(self) -> str | None:
        st = self._state()
        if st:
            # Prefer explicit selected_mode; fall back to ui_mode_base; default NORMAL
            return st.selected_mode or st.ui_mode_base or "NORMAL"
        return "NORMAL"

    async def async_select_option(self, option: str) -> None:
        data = self.coordinator.data
        if data and self.connector_uuid in (data.connectors or {}):
            data.connectors[self.connector_uuid].selected_mode = option
            self.coordinator.async_set_updated_data(data)
        await self.api_client.set_charging_mode(option)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:  # RestoreEntity
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if not last or last.state in (None, "unknown", "unavailable"):
            return
        restored = last.state
        if restored not in MODES:
            return
        st = self._state()
        if st and st.selected_mode is None:
            st.selected_mode = restored
            data = self.coordinator.data
            if data:
                self.coordinator.async_set_updated_data(data)
        self.async_write_ha_state()

    @property
    def device_info(self):
        return super().device_info
