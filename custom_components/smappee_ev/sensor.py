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

from .const import DOMAIN, CONF_SERIAL

_LOGGER = logging.getLogger(__name__)
#SCAN_INTERVAL = timedelta(seconds=20)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV sensors from a config entry.""" 
    api_client = hass.data[DOMAIN][config_entry.entry_id]

    async_add_entities([
        ChargingPointSessionState(api_client, config_entry),
        ChargingPointEvccState(api_client, config_entry),
        # --- ENERGIESENSOR REACTIVATE---
        # ChargingPointLatestCounter(api_client, config_entry),  
        # ------------------------
    ])

class SmappeeSensorBase(SensorEntity):
    """Base class for Smappee EV sensors."""

    _attr_should_poll = False  # Event-driven, no polling

    def __init__(self, api_client, config_entry):
        self.api_client = api_client
        self._config_entry = config_entry
        self._serial = config_entry.data.get(CONF_SERIAL)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._serial)},
            "name": f"Smappee EV Wallbox {self._serial}",
            "manufacturer": "Smappee",
        }

    async def async_added_to_hass(self):
        self.api_client.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        self.api_client.remove_callback(self.async_write_ha_state)

class ChargingPointSessionState(SmappeeSensorBase):
    """Sensor for the current session state."""

    _attr_icon = "mdi:ev-station"

    def __init__(self, api_client, config_entry):
        super().__init__(api_client, config_entry)
        self._attr_unique_id = f"{self._serial}_session_state"
        self._attr_name = f"Charging point {self._serial} session state"

    @property
    def available(self) -> bool:
        return True   

    @property
    def native_value(self):
        """Return the current session state."""
        return self.api_client.session_state

class ChargingPointEvccState(SmappeeSensorBase):
    """Sensor mapping session state to EVCC state."""

    _attr_icon = "mdi:ev-plug-type2"

    def __init__(self, api_client, config_entry):
        super().__init__(api_client, config_entry)
        self._attr_unique_id = f"{self._serial}_evcc_state"
        self._attr_name = f"Charging point {self._serial} EVCC state"

    @property
    def native_value(self):
        """Return the EVCC mapped state."""
        session_state = self.api_client.session_state
        if session_state in ["INITIAL", "STOPPED"]:
            return "A"
        elif session_state in ["STARTED", "STOPPING"]:
            return "B"
        elif session_state in ["SUSPENDED", "CHARGING"]:
            return "C"
        else:
            return "E"

    @property
    def available(self) -> bool:
        return True           

    @property
    def extra_state_attributes(self):
        return {
            "raw_session_state": self.api_client.session_state,
            "evcc_mapped_state": self.native_value,
        }

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