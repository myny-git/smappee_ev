from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    station_client: SmappeeApiClient = data["station_client"]

    async_add_entities([SmappeeMqttConnectivity(coordinator, station_client)])


class SmappeeMqttConnectivity(CoordinatorEntity[SmappeeCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "MQTT Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._attr_unique_id = f"{api_client.serial_id}_mqtt_connected"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    @property
    def is_on(self) -> bool:
        st = self.coordinator.data.station if self.coordinator.data else None
        return bool(getattr(st, "mqtt_connected", False))

    @property
    def extra_state_attributes(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        return {
            "last_mqtt_rx": getattr(st, "last_mqtt_rx", None),
        }
