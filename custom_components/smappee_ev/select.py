from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .base_entities import SmappeeConnectorEntity
from .const import CHARGING_MODES
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData, SmappeeEvConfigEntry
from .device_handle import SmappeeDeviceHandle

MODES = list(CHARGING_MODES)
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = config_entry.runtime_data
    sites = runtime.sites

    entities: list[SelectEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            conns: dict[str, SmappeeDeviceHandle] = bucket.get("connector_clients", {})

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

    async_add_entities(entities, False)


class SmappeeModeSelect(SmappeeConnectorEntity, SelectEntity, RestoreEntity):
    """Home Assistant select entity for Smappee charging mode."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:tune-variant"
    _attr_options = MODES
    _attr_translation_key = "charging_mode"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ):
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            api_client,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="select:charging_mode",
        )
        self.api_client = api_client

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._connector_uuid) if data else None

    @property
    def current_option(self) -> str | None:
        st = self._state()
        if st:
            mode = st.selected_mode or st.ui_mode_base or "standard"
            return mode.lower() if mode else "standard"
        return "standard"

    async def async_select_option(self, option: str) -> None:
        data = self.coordinator.data
        conn = (data.connectors or {}).get(self.connector_uuid) if data else None
        previous_mode = conn.selected_mode if conn else None
        if conn:
            conn.selected_mode = option
            self.coordinator.async_set_updated_data(data)
        try:
            await self.api_client.set_charging_mode(option.upper())
        except Exception:
            if conn:
                conn.selected_mode = previous_mode
                if data:
                    self.coordinator.async_set_updated_data(data)
            raise
        self.coordinator.async_schedule_dashboard_refresh()
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
        updated_data = False
        if st and st.selected_mode is None:
            st.selected_mode = restored
            data = self.coordinator.data
            if data:
                self.coordinator.async_set_updated_data(data)
                updated_data = True
        if not updated_data:
            self.async_write_ha_state()
