from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api_client import SmappeeApiClient
from .base_entities import SmappeeStationEntity
from .coordinator import SmappeeCoordinator
from .data import RuntimeData
from .helpers import station_serial


def _station_serial(coord: SmappeeCoordinator) -> str:
    return station_serial(coord)

    # removed unused _station_name helper


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: RuntimeData = config_entry.runtime_data  # type: ignore[attr-defined]
    sites = runtime.sites

    entities: list[SmappeeMqttConnectivity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeApiClient = bucket["station_client"]
            entities.append(SmappeeMqttConnectivity(coord, st_client, sid, st_uuid))

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
        # st = self.coordinator.data.station if self.coordinator.data else None
        return {
            #            "last_mqtt_rx": getattr(st, "last_mqtt_rx", None),
            "service_location_id": self._sid,
            "station_serial": self._serial,
            "station_uuid": self._station_uuid,
        }
