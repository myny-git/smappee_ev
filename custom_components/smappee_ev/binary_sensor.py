from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api_client import SmappeeApiClient
from .base_entities import SmappeeBaseEntity, SmappeeConnectorEntity, SmappeeStationEntity
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData, SmappeeEvConfigEntry
from .helpers import build_connector_label, station_serial


def _station_serial(coord: SmappeeCoordinator) -> str:
    return station_serial(coord)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = config_entry.runtime_data
    sites = runtime.sites

    entities: list[SmappeeBaseEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeApiClient = bucket["station_client"]
            conns: dict[str, SmappeeApiClient] = bucket.get("connector_clients", {})
            entities.append(SmappeeMqttConnectivity(coord, st_client, sid, st_uuid))
            for cuuid, client in (conns or {}).items():
                entities.append(ConnectorCarConnectedBinarySensor(coord, client, sid, st_uuid, cuuid))

    async_add_entities(entities, True)


class SmappeeMqttConnectivity(SmappeeStationEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "MQTT Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="mqtt_connected",
            name="MQTT Connected",
        )
        self.api_client = api_client

    @property
    def device_info(self) -> DeviceInfo:
        return super().device_info

    @property
    def is_on(self) -> bool:
        st = self.coordinator.data.station if self.coordinator.data else None
        return bool(getattr(st, "mqtt_connected", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "service_location_id": self._sid,
            "station_serial": self._serial,
            "station_uuid": self._station_uuid,
        }

class ConnectorCarConnectedBinarySensor(SmappeeConnectorEntity, BinarySensorEntity, RestoreEntity):
    """Binary sensor indicating whether the car is physically connected to the charging station."""

    # FIX: Corrected from .PLUG (non-existent) to .PLUGGED to match Home Assistant Core architecture
    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ) -> None:
        """Initialize the car connection binary sensor."""
        num_lbl = build_connector_label(api_client, connector_uuid).split(" ", 1)[1]

        # Kept your exact operational parent init method syntax that bypassed the typing error
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="binary_sensor:car_connected",
            name=f"Connector {num_lbl} Car Connected",
        )
        self.api_client = api_client
        # Added tracking variable to safely pass the restored state to the is_on property
        self._fallback_is_on: bool | None = None

    # ---------- Helpers ----------

    def _conn_state(self) -> ConnectorState | None:
        """Helper to extract current connector data attributes from the coordinator."""
        data: IntegrationData | None = self.coordinator.data
        if not data:
            return None
        return (data.connectors or {}).get(self._uuid)

    # ---------- Home Assistant Hooks ----------

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information linkage from base entity implementation."""
        return super().device_info

    @property
    def is_on(self) -> bool:
        """Return true if the physical vehicle loop state handles connection indicators."""
        conn_state = self._conn_state()

        # FIX: If coordinator data is missing on early startup, return the state restored from DB
        if not conn_state or getattr(conn_state, "status_current", None) is None:
            return bool(self._fallback_is_on)

        status = conn_state.status_current
        state_upper = str(status).upper()

        # Match against valid connected state signatures reported by Smappee endpoints
        return state_upper in ["CABLE_CONNECTED", "CHARGING", "SUSPENDED_EV", "SUSPENDED_EVSE"]

    async def async_added_to_hass(self) -> None:
        """Run registration logic and manage early system state restoration profiles."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            # FIX: Assigned to fallback tracker instead of _attr_is_on, as is_on completely overrides it
            self._fallback_is_on = last_state.state == "on"
