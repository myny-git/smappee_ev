from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .helpers import make_device_info, make_unique_id


def _station_serial(coord: SmappeeCoordinator) -> str:
    return getattr(coord.station_client, "serial_id", "unknown")


def _station_name(coord: SmappeeCoordinator, sid: int) -> str:
    st = coord.data.station if coord.data else None
    return getattr(st, "name", None) or f"Smappee EV {_station_serial(coord)}"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    store = hass.data[DOMAIN][config_entry.entry_id]
    sites = store.get(
        "sites", {}
    )  # { sid: { "stations": { st_uuid: {coordinator, station_client, ...} } } }

    entities: list[SmappeeMqttConnectivity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeApiClient = bucket["station_client"]
            entities.append(SmappeeMqttConnectivity(coord, st_client, sid, st_uuid))

    async_add_entities(entities, True)


class SmappeeMqttConnectivity(CoordinatorEntity[SmappeeCoordinator], BinarySensorEntity):
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
        super().__init__(coordinator)
        self.api_client = api_client

        self._sid = sid
        self._station_uuid = station_uuid
        self._serial = getattr(coordinator.station_client, "serial_id", "unknown")
        self._attr_unique_id = make_unique_id(
            sid, self._serial, station_uuid, None, "mqtt_connected"
        )

    @property
    def device_info(self):
        station_name = getattr(getattr(self.coordinator.data, "station", None), "name", None)
        return make_device_info(
            self._sid,
            self._serial,
            self._station_uuid,
            station_name,
        )

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
