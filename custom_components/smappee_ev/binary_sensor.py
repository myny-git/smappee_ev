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
    coordinators: dict[int, SmappeeCoordinator] = store["coordinators"]
    station_clients: dict[int, SmappeeApiClient] = store["station_clients"]

    entities: list[SmappeeMqttConnectivity] = []
    for sid, coord in coordinators.items():
        st_client = station_clients.get(sid)
        if not st_client:
            continue
        entities.append(SmappeeMqttConnectivity(coord, st_client, sid))

    async_add_entities(entities)


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
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        # self._attr_unique_id = f"{api_client.serial_id}_mqtt_connected"
        self._sid = sid
        # Unique ID for each site + station
        self._attr_unique_id = f"{sid}:{_station_serial(coordinator)}:mqtt_connected"

    @property
    def device_info(self):
        serial = _station_serial(self.coordinator)
        return {
            "identifiers": {(DOMAIN, f"{self._sid}:{serial}")},
            "name": _station_name(self.coordinator, self._sid),
            "manufacturer": "Smappee",
        }

    @property
    def is_on(self) -> bool:
        st = self.coordinator.data.station if self.coordinator.data else None
        return bool(getattr(st, "mqtt_connected", False))

    @property
    def extra_state_attributes(self):
        # st = self.coordinator.data.station if self.coordinator.data else None
        return {
            #            "last_mqtt_rx": getattr(st, "last_mqtt_rx", None),
            "service_location_id": self._sid,
            "station_serial": _station_serial(self.coordinator),
        }
