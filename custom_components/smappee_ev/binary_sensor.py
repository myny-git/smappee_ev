from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api_client import SmappeeApiClient
from .base_entities import SmappeeConnectorEntity, SmappeeStationEntity
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

    entities: list[SmappeeMqttConnectivity] = []
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
    def device_info(self):
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
        num_lbl = build_connector_label(api_client, connector_uuid).split(" ", 1)[1]
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="switch:charging",
            name=f"Connector {num_lbl} Car Connected",
        )
        self.api_client = api_client

    # ---------- Helpers ----------

    def _conn_state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        if not data:
            return None
        return (data.connectors or {}).get(self._uuid)

    # ---------- HA hooks ----------

    @property
    def device_info(self):
        return super().device_info

    @property
    def is_on(self) -> bool:
        conn_state = self._conn_state()
        if not conn_state:
            return False

        status = getattr(conn_state, "status_current", None)
        if status is not None:
            state_upper = str(status).upper()
            return state_upper in ["CABLE_CONNECTED", "CHARGING", "SUSPENDED_EV", "SUSPENDED_EVSE"]

        return False

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == "on"
