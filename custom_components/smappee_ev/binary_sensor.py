from __future__ import annotations

from typing import cast

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entities import SmappeeSiteEntity
from .coordinator import SmappeeCoordinator, SmappeeSiteCoordinator
from .data import SmappeeEvConfigEntry
from .helpers import station_serial

PARALLEL_UPDATES = 0


def _station_serial(coord: SmappeeCoordinator) -> str:
    return station_serial(coord)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = config_entry.runtime_data

    entities: list[SmappeeMqttConnectivity] = []
    for sid, site in (runtime.sites or {}).items():
        sid_int = int(sid)
        coord = cast(SmappeeSiteCoordinator | SmappeeCoordinator | None, site.site_coordinator)
        if coord is None:
            first_bucket = next(iter(site.stations.values()), None)
            coord = cast(
                SmappeeSiteCoordinator | SmappeeCoordinator | None,
                first_bucket.station_coordinator if first_bucket else None,
            )
        if coord is not None:
            entities.append(SmappeeMqttConnectivity(coord, sid_int))

    async_add_entities(entities, False)


class SmappeeMqttConnectivity(SmappeeSiteEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "mqtt_connected"

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
    def is_on(self) -> bool:
        data = self.coordinator.data if self.coordinator.data else None
        site = getattr(data, "site", None) or getattr(data, "station", None)
        return bool(getattr(site, "mqtt_connected", False))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "service_location_id": self._sid,
            "station_serial": self._serial,
            "station_uuid": self._station_uuid,
            "gateway_serial": getattr(self.coordinator, "gateway_serial", None),
            "site_uuid": getattr(self.coordinator, "site_uuid", None),
        }
