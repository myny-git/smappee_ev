from __future__ import annotations

import logging


from datetime import timedelta
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .api_client import SmappeeApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
#SCAN_INTERVAL = timedelta(seconds=20)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV sensors from a config entry.""" 
    connector_clients: dict[str, SmappeeApiClient] = hass.data[DOMAIN][config_entry.entry_id]["connectors"]

    entities: list[SensorEntity] = []
    for client in connector_clients.values():
        entities.append(SmappeeChargingStateSensor(client))
        entities.append(SmappeeEVCCStateSensor(client))

    async_add_entities(entities, update_before_add=True)

class SmappeeSensorBase(SensorEntity):
    """Base class for Smappee EV sensors."""

    _attr_should_poll = False  # Event-driven, no polling

    def __init__(
        self,
        api_client: SmappeeApiClient,
        name: str,
        unique_id: str,
        icon: str | None = None,
    ) -> None:
        self.api_client = api_client
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = icon

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": f"Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.api_client.register_callback(self.schedule_update_ha_state)

    async def async_will_remove_from_hass(self):
        await super().async_will_remove_from_hass()
        self.api_client.remove_callback(self.schedule_update_ha_state)


class SmappeeChargingStateSensor(SmappeeSensorBase):
    """Sensor for the current session state."""
    def __init__(self, api_client: SmappeeApiClient):
        super().__init__(
            api_client,
            f"Charging state {api_client.connector_number}",
            f"{api_client.serial_id}_connector{api_client.connector_number}_charging_state",
            icon="mdi:ev-station",
        )

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self):
        """Return the current session state."""
        return self.api_client.session_state


class SmappeeEVCCStateSensor(SmappeeSensorBase):
    """Sensor mapping session state to EVCC state."""

    def __init__(self, api_client: SmappeeApiClient):
        super().__init__(
            api_client,
            f"EVCC state {api_client.connector_number}",
            f"{api_client.serial_id}_connector{api_client.connector_number}_evcc_state",
            icon="mdi:car-electric",
        )

    @property
    def native_value(self):
        """Return the EVCC mapped state."""
        session_state = self.api_client.session_state
        if session_state in ["INITIAL", "STOPPED"]:
            return "A"
        elif session_state in ["SUSPENDED", "STOPPING"]:
            return "B"
        elif session_state in ["STARTED", "CHARGING"]:
            return "C"
        else:
            return "E"

    @property
    def available(self) -> bool:
        return True



# --- SENSOR DISABLED, as MQTT or MODBUS is available ---
# class ChargingPointLatestCounter(SmappeeSensorBase):
#     """Sensor for the total energy delivered by the charging point."""
#
#     _attr_state_class = SensorStateClass.TOTAL_INCREASING
#     _attr_device_class = SensorDeviceClass.ENERGY
#     _attr_icon = "mdi:ev-station"
#     _attr_native_unit_of_measurement = "kWh"
#
#     def __init__(self, api_client, config_entry):
#         super().__init__(api_client, config_entry)
#         self._attr_unique_id = f"{self._serial}_counter"
#         self._attr_name = f"Charging point {self._serial} total counter"
#
#     @property
#     def available(self) -> bool:
#         return self.api_client.latest_session_counter != 0
#
#     @property
#     def native_value(self):
#         """Return the latest session counter."""
#         return self.api_client.latest_session_counter