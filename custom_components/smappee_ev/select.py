from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData
from .helpers import make_device_info, make_unique_id

MODES = ["SMART", "SOLAR", "NORMAL"]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    store = hass.data[DOMAIN][config_entry.entry_id]
    sites = store.get(
        "sites", {}
    )  # { sid: { "stations": { st_uuid: { coordinator, station_client, connector_clients } } } }

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


class SmappeeModeSelect(CoordinatorEntity[SmappeeCoordinator], SelectEntity):
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
        super().__init__(coordinator)
        self.api_client = api_client
        self._sid = sid
        self._station_uuid = station_uuid
        self._connector_uuid = connector_uuid
        self._serial = getattr(coordinator.station_client, "serial_id", "unknown")

        label = getattr(api_client, "connector_number", None) or connector_uuid[-4:]
        self._attr_name = f"Charging Mode {label}"
        # Globally stable unique_id
        self._attr_unique_id = make_unique_id(
            sid, self._serial, station_uuid, connector_uuid, "select:charging_mode"
        )

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._connector_uuid) if data else None

    @property
    def current_option(self) -> str | None:
        st = self._state()
        if st:
            # Prefer explicit selected_mode; fall back to ui_mode_base; default NORMAL
            return st.selected_mode or st.ui_mode_base or "NORMAL"
        return getattr(self.api_client, "selected_mode", "NORMAL")

    async def async_select_option(self, option: str) -> None:
        # Persist selection locally for instant UI feedback
        self.api_client.selected_mode = option
        data = self.coordinator.data
        if data and self._connector_uuid in (data.connectors or {}):
            data.connectors[self._connector_uuid].selected_mode = option
            self.coordinator.async_set_updated_data(data)
        # Send to backend
        await self.api_client.set_charging_mode(option)
        self.async_write_ha_state()

    @property
    def device_info(self):
        station_name = getattr(getattr(self.coordinator.data, "station", None), "name", None)
        return make_device_info(
            self._sid,
            self._serial,
            self._station_uuid,
            station_name,
        )
