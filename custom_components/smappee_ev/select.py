from aiohttp import ClientError
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api.device_handle import SmappeeDeviceHandle
from .api.errors import SmappeeError
from .const import CHARGING_MODES, DOMAIN
from .coordinator import SmappeeCoordinator
from .entity import SmappeeConnectorEntity
from .models.runtime_data import SmappeeEvConfigEntry
from .models.state import ConnectorState, IntegrationData

PARALLEL_UPDATES = 1
MODES = [mode.lower() for mode in CHARGING_MODES]


def _connector_action_error(method_name: str, err: BaseException) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="connector_service_failed",
        translation_placeholders={"method_name": method_name, "error": str(err)},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = config_entry.runtime_data

    entities: list[SelectEntity] = []
    for sid, site in (runtime.sites or {}).items():
        sid_int = int(sid)
        for st_uuid, bucket in site.stations.items():
            coord = bucket.station_coordinator
            if coord is None:
                continue
            conns = {key: conn.connector_client for key, conn in bucket.connectors.items()}

            for cuuid, client in (conns or {}).items():
                entities.append(
                    SmappeeModeSelect(
                        coordinator=coord,
                        api_client=client,
                        sid=sid_int,
                        station_uuid=st_uuid,
                        connector_uuid=cuuid,
                    )
                )

    async_add_entities(entities, False)


class SmappeeModeSelect(SmappeeConnectorEntity, SelectEntity, RestoreEntity):
    """Home Assistant select entity for Smappee charging mode."""

    _attr_has_entity_name = True
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
    def current_option(self) -> str:
        st = self._state()

        return (
            getattr(st, "selected_mode", None) or getattr(st, "ui_mode_base", None) or "standard"
        ).lower()

    async def async_select_option(self, option: str) -> None:
        data = self.coordinator.data
        conn = (data.connectors or {}).get(self.connector_uuid) if data else None
        previous_mode = conn.selected_mode if conn else None
        if conn:
            conn.selected_mode = option
            self.coordinator.async_set_updated_data(data)
        try:
            await self.api_client.set_charging_mode(option.upper())
        except (SmappeeError, ClientError, TimeoutError, RuntimeError, ValueError) as err:
            if conn:
                conn.selected_mode = previous_mode
                if data:
                    self.coordinator.async_set_updated_data(data)
            raise _connector_action_error("set_charging_mode", err) from err
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
            if getattr(self, "platform", None) is not None:
                self.async_write_ha_state()
