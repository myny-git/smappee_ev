from __future__ import annotations

import logging
from typing import Dict


from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .api_client import SmappeeApiClient
from .coordinator import SmappeeCoordinator
from .data import IntegrationData, ConnectorState

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV sensors from a config entry.""" 
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: Dict[str, SmappeeApiClient] = data["connector_clients"]

    entities: list[SensorEntity] = []
    for uuid, client in connector_clients.items():
        entities.append(SmappeeChargingStateSensor(coordinator, client, uuid))
        entities.append(SmappeeEVCCStateSensor(coordinator, client, uuid))

    async_add_entities(entities, update_before_add=True)

class _Base(CoordinatorEntity[SmappeeCoordinator], SensorEntity):

    """Base class for Smappee EV sensors."""

    _attr_should_poll = False  # Event-driven, no polling

    def __init__(self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str) -> None:
        super().__init__(coordinator)
        self.api_client = api_client          # kept only for device_info
        self._uuid = uuid

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": f"Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._uuid) if data else None



class SmappeeChargingStateSensor(_Base):
    """Raw charging/session state reported by the connector."""

    def __init__(self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str) -> None:
        super().__init__(coordinator, api_client, uuid)
        self._attr_name = f"Charging state {api_client.connector_number}"
        self._attr_unique_id = f"{api_client.serial_id}_connector{api_client.connector_number}_charging_state"
        self._attr_icon = "mdi:ev-station"

    @property
    def native_value(self):
        st = self._state()
        return st.session_state if st else None


class SmappeeEVCCStateSensor(_Base):
    """EVCC A/B/C/E mapping derived from the session state."""

    def __init__(self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str) -> None:
        super().__init__(coordinator, api_client, uuid)
        self._attr_name = f"EVCC state {api_client.connector_number}"
        self._attr_unique_id = f"{api_client.serial_id}_connector{api_client.connector_number}_evcc_state"
        self._attr_icon = "mdi:car-electric"

    @property
    def native_value(self):
        st = self._state()
        session_state = st.session_state if st else None

        if session_state in ("INITIAL", "STOPPED"):
            return "A"
        if session_state in ("SUSPENDED", "STOPPING"):
            return "B"
        if session_state in ("STARTED", "CHARGING"):
            return "C"
        return "E"




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