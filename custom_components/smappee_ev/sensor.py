from __future__ import annotations

import time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator


def _station_serial(coord: SmappeeCoordinator) -> str:
    return getattr(coord.station_client, "serial_id", "unknown")


def _station_name(coord: SmappeeCoordinator, sid: int) -> str:
    st = coord.data.station if getattr(coord, "data", None) else None
    return getattr(st, "name", None) or f"Smappee EV {_station_serial(coord)}"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    store = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[int, SmappeeCoordinator] = store["coordinators"]
    station_clients: dict[int, SmappeeApiClient] = store["station_clients"]
    connector_clients_by_sid: dict[int, dict[str, SmappeeApiClient]] = store["connector_clients"]

    entities: list[SensorEntity] = []

    for sid, coordinator in coordinators.items():
        station_client = station_clients.get(sid)
        if not station_client:
            continue

        # ---- Station sensors ----
        entities.append(SmappeeMqttLastSeenSensor(coordinator, station_client))
        entities.append(StationGridPower(coordinator, station_client, sid))
        entities.append(StationPvPower(coordinator, station_client, sid))
        entities.append(StationGridEnergyImport(coordinator, station_client, sid))
        entities.append(StationGridEnergyExport(coordinator, station_client, sid))
        entities.append(StationPvEnergyImport(coordinator, station_client, sid))
        entities.append(StationGridCurrents(coordinator, station_client, sid))
        entities.append(StationPvCurrents(coordinator, station_client, sid))

        # ---- Connector sensors ----
        for uuid, client in (connector_clients_by_sid.get(sid) or {}).items():
            entities.append(ConnectorPowerSensor(coordinator, client, sid, uuid))
            entities.append(ConnectorCurrentASensor(coordinator, client, sid, uuid))
            entities.append(ConnEnergyImport(coordinator, client, sid, uuid))
            entities.append(SmappeeChargingStateSensor(coordinator, client, sid, uuid))
            entities.append(SmappeeEVCCStateSensor(coordinator, client, sid, uuid))
            entities.append(SmappeeEvseStatusSensor(coordinator, client, sid, uuid))
            entities.append(ConnCurrentL1(coordinator, client, sid, uuid))
            entities.append(ConnCurrentL2(coordinator, client, sid, uuid))
            entities.append(ConnCurrentL3(coordinator, client, sid, uuid))

    async_add_entities(entities)


# --------------- Bases ---------------


class _Base(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Common base for station sensors."""

    def __init__(
        self, coordinator: SmappeeCoordinator, sid: int, name_suffix: str, unique_suffix: str
    ) -> None:
        super().__init__(coordinator)
        self._sid = sid
        self._name_suffix = name_suffix
        serial = _station_serial(coordinator)
        self._attr_unique_id = f"{sid}:{serial}:{unique_suffix}"
        self._attr_name = name_suffix

    @property
    def device_info(self) -> DeviceInfo:
        serial = _station_serial(self.coordinator)
        return {
            "identifiers": {(DOMAIN, f"{self._sid}:{serial}")},
            "name": _station_name(self.coordinator, self._sid),
            "manufacturer": "Smappee",
        }


class _ConnBase(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Common base for connector sensors."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api: SmappeeApiClient,
        sid: int,
        uuid: str,
        name_suffix: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api
        self._sid = sid
        self._uuid = uuid
        serial = _station_serial(coordinator)
        self._attr_unique_id = f"{sid}:{serial}:{uuid}:{unique_suffix}"
        cnum = getattr(api, "connector_number", None)
        num_lbl = f"{cnum}" if cnum is not None else uuid[-4:]
        self._attr_name = f"Connector {num_lbl} {name_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        serial = _station_serial(self.coordinator)
        return {
            "identifiers": {(DOMAIN, f"{self._sid}:{serial}")},
            "name": _station_name(self.coordinator, self._sid),
            "manufacturer": "Smappee",
        }

    @property
    def _conn_state(self) -> Any | None:
        data = self.coordinator.data
        if not data:
            return None
        return (data.connectors or {}).get(self._uuid)


# --------------- Station sensors ---------------


class StationGridPower(_Base):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int) -> None:
        super().__init__(coordinator, sid, "Grid power", "grid_power")

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_power_total", None)
        return float(v) if isinstance(v, int | float) else None


class StationPvPower(_Base):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int) -> None:
        super().__init__(coordinator, sid, "PV power", "pv_power")

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "pv_power_total", None)
        return float(v) if isinstance(v, int | float) else None


class StationGridEnergyImport(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int) -> None:
        super().__init__(coordinator, sid, "Grid energy import", "grid_energy_import")

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_energy_import_kwh", None)
        return float(v) if isinstance(v, int | float) else None


class StationGridEnergyExport(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int) -> None:
        super().__init__(coordinator, sid, "Grid energy export", "grid_energy_export")

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_energy_export_kwh", None)
        return float(v) if isinstance(v, int | float) else None


class StationPvEnergyImport(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int) -> None:
        super().__init__(coordinator, sid, "PV energy import", "pv_energy_import")

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "pv_energy_import_kwh", None)
        return float(v) if isinstance(v, int | float) else None


# --------------- Connector sensors ---------------


class ConnCurrentL1(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, sid, uuid):
        super().__init__(c, api, sid, uuid, "Current L1", "current_l1")

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class ConnCurrentL2(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, sid, uuid):
        super().__init__(c, api, sid, uuid, "Current L2", "current_l2")

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class ConnCurrentL3(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, sid, uuid):
        super().__init__(c, api, sid, uuid, "Current L3", "current_l3")

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class ConnectorPowerSensor(_ConnBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, uuid: str) -> None:
        super().__init__(c, api, sid, uuid, "Power", "power_total")

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "power_total", None) if st else None
        return float(v) if isinstance(v, int | float) else None


class ConnectorCurrentASensor(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, uuid: str) -> None:
        super().__init__(c, api, sid, uuid, "Current", "current_total")

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        if isinstance(vals, list) and vals:
            try:
                return float(sum(float(x) for x in vals))
            except (TypeError, ValueError):
                return None
        return None


class ConnEnergyImport(_ConnBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, uuid: str) -> None:
        super().__init__(c, api, sid, uuid, "Energy import", "energy_import_kwh")

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "energy_import_kwh", None) if st else None
        return float(v) if isinstance(v, int | float) else None


class StationGridCurrents(_Base):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, api, sid):
        super().__init__(coordinator, sid, "Grid current (L1–L3)", "grid_currents")

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_current_phases", None) if st else None
        if isinstance(vals, list) and vals:
            try:
                return round(sum(float(x) for x in vals), 3)
            except (TypeError, ValueError):
                return None
        return None

    @property
    def extra_state_attributes(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_current_phases", None) if st else None
        return (
            {"L1": vals[0], "L2": vals[1], "L3": vals[2]}
            if isinstance(vals, list) and len(vals) >= 3
            else {}
        )


class StationPvCurrents(_Base):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, api, sid):
        super().__init__(coordinator, sid, "PV current (L1–L3)", "pv_currents")

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "pv_current_phases", None) if st else None
        if isinstance(vals, list) and vals:
            try:
                return round(sum(float(x) for x in vals), 3)
            except (TypeError, ValueError):
                return None
        return None

    @property
    def extra_state_attributes(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "pv_current_phases", None) if st else None
        return (
            {"L1": vals[0], "L2": vals[1], "L3": vals[2]}
            if isinstance(vals, list) and len(vals) >= 3
            else {}
        )


class SmappeeChargingStateSensor(_ConnBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, uuid: str) -> None:
        super().__init__(c, api, sid, uuid, "Charging state", "charging_state")

    @property
    def native_value(self) -> str | None:
        st = self._conn_state
        return str(getattr(st, "session_state", None)) if st else None


class SmappeeEVCCStateSensor(_ConnBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, api, sid, uuid):
        super().__init__(c, api, sid, uuid, "EVCC state", "evcc_state")

    @property
    def native_value(self):
        st = self._conn_state
        return str(getattr(st, "evcc_state", None)) if st else None

    @property
    def extra_state_attributes(self):
        st = self._conn_state
        if not st:
            return None
        return {
            "iec_status": getattr(st, "iec_status", None),
            "session_state": getattr(st, "session_state", None),
            "charging_mode": getattr(st, "raw_charging_mode", None),
            "optimization_strategy": getattr(st, "optimization_strategy", None),
            "paused": getattr(st, "paused", None),
            "status_current": getattr(st, "session_cause", None),
        }


class SmappeeEvseStatusSensor(_ConnBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, api, sid, uuid):
        super().__init__(c, api, sid, uuid, "EVSE status", "status_current")

    @property
    def native_value(self):
        st = self._conn_state
        return str(getattr(st, "status_current", None)) if st else None


class SmappeeMqttLastSeenSensor(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Station-scope 'last MQTT RX' as age in seconds."""

    _attr_has_entity_name = True
    _attr_name = "MQTT last seen"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"

    def __init__(self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._attr_unique_id = f"{api_client.serial_id}_mqtt_last_seen"

    @property
    def device_info(self) -> DeviceInfo:
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        st = data.station if data else None
        ts = getattr(st, "last_mqtt_rx", None)
        if not ts:
            return None
        try:
            return max(0.0, time.time() - float(ts))
        except (TypeError, ValueError):
            return None
