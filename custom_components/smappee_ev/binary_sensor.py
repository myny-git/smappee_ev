from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import SmappeeCoordinator, SmappeeSiteCoordinator
from .coordinators.status import SmappeeStatusCoordinator
from .entity import SmappeeSiteEntity
from .helpers import station_serial
from .models.runtime_data import SmappeeEvConfigEntry

PARALLEL_UPDATES = 0


def _station_serial(coord: SmappeeCoordinator) -> str:
    return station_serial(coord)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = config_entry.runtime_data

    entities: list[BinarySensorEntity] = []

    if runtime.status_coordinator is not None and runtime.status_coordinator.data is not None:
        entities.extend(
            SmappeeServiceStatusSensor(runtime.status_coordinator, subsystem)
            for subsystem in runtime.status_coordinator.data.subsystems
        )

    for sid, site in (runtime.sites or {}).items():
        sid_int = int(sid)
        coord: SmappeeSiteCoordinator | SmappeeCoordinator | None = site.site_coordinator
        if coord is None:
            first_bucket = next(iter(site.stations.values()), None)
            coord = first_bucket.station_coordinator if first_bucket else None
        if coord is not None:
            entities.append(SmappeeMqttConnectivity(coord, sid_int))

    async_add_entities(entities, False)


_SUBSYSTEM_LABELS: dict[str, str] = {
    "website": "Website",
    "dashboard": "Dashboard",
    "android": "Android app",
    "ios": "iOS app",
    "api": "API",
    "cloud": "Cloud",
    "livedata": "Live data",
    "historical": "Historical data",
    "evline": "Smappee EV Line",
    "evline-ocpp": "Smappee EV Line OCPP",
    "evline-csms": "Smappee EV Line CSMS",
    "global": "Global",
}


class SmappeeServiceStatusSensor(CoordinatorEntity[SmappeeStatusCoordinator], BinarySensorEntity):
    """Binary sensor per Smappee service subsystem: off=ok, on=problem."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "service_status"

    def __init__(self, coordinator: SmappeeStatusCoordinator, subsystem: str) -> None:
        super().__init__(coordinator)
        self._subsystem = subsystem
        self._attr_unique_id = f"smappee_service_status:{subsystem}"
        label = _SUBSYSTEM_LABELS.get(subsystem, subsystem)
        self._attr_translation_placeholders = {"subsystem": label}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "smappee_service_status")},
            name="Smappee Service Status",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://status.smappee.net",
        )

    @property
    @override
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        status = data.subsystems.get(self._subsystem) if data else None
        return None if status is None else status != "ok"


class SmappeeMqttConnectivity(SmappeeSiteEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "mqtt_connected"

    @property
    @override
    def available(self) -> bool:
        return self._coordinator_available

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        sid: int,
        api_client: object | None = None,
        station_uuid: str | None = None,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="mqtt_connected",
        )
        if station_uuid is not None:
            self._station_uuid = station_uuid
        self.api_client = api_client
        self._attr_name = "MQTT Connected"

    @property
    @override
    def is_on(self) -> bool:
        data = self.coordinator.data if self.coordinator.data else None
        site = getattr(data, "site", None) or getattr(data, "station", None)
        return bool(getattr(site, "mqtt_connected", False))

    @property
    @override
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "service_location_id": self._sid,
            "station_serial": self._serial,
            "station_uuid": self._station_uuid,
            "gateway_serial": getattr(self.coordinator, "gateway_serial", None),
            "site_uuid": getattr(self.coordinator, "site_uuid", None),
        }
