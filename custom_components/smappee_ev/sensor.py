from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .helpers import make_device_info, make_unique_id


def _station_serial(coord: SmappeeCoordinator) -> str:
    return getattr(coord.station_client, "serial_id", "unknown")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    store = hass.data[DOMAIN][config_entry.entry_id]
    sites = store.get(
        "sites", {}
    )  # { sid: { "stations": { st_uuid: {coordinator, station_client, connector_clients} } } }

    entities: list[SensorEntity] = []

    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeApiClient = bucket["station_client"]
            conns: dict[str, SmappeeApiClient] = bucket.get("connector_clients", {})

            # ---- Station sensors ----
            entities.append(SmappeeMqttLastSeenSensor(coord, st_client, sid, st_uuid))
            entities.append(StationGridPower(coord, st_client, sid, st_uuid))
            entities.append(StationPvPower(coord, st_client, sid, st_uuid))
            entities.append(StationHouseConsumptionPower(coord, st_client, sid, st_uuid))
            entities.append(StationGridEnergyImport(coord, st_client, sid, st_uuid))
            entities.append(StationGridEnergyExport(coord, st_client, sid, st_uuid))
            entities.append(StationPvEnergyImport(coord, st_client, sid, st_uuid))
            entities.append(StationGridCurrents(coord, st_client, sid, st_uuid))
            entities.append(StationPvCurrents(coord, st_client, sid, st_uuid))

            # ---- Connector sensors ----
            for cuuid, client in (conns or {}).items():
                entities.append(ConnectorPowerSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnectorCurrentASensor(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnEnergyImport(coord, client, sid, st_uuid, cuuid))
                entities.append(SmappeeChargingStateSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(SmappeeEVCCStateSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(SmappeeEvseStatusSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnCurrentL1(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnCurrentL2(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnCurrentL3(coord, client, sid, st_uuid, cuuid))

    async_add_entities(entities, True)


# --------------- Bases ---------------


class _Base(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Common base for station sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str,
        name_suffix: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._sid = sid
        self._station_uuid = station_uuid
        self._name_suffix = name_suffix
        self._serial = _station_serial(coordinator)
        # Globally unique station-level entity id
        self._attr_unique_id = make_unique_id(sid, self._serial, station_uuid, None, unique_suffix)
        self._attr_name = name_suffix

    @property
    def device_info(self):
        station_name = getattr(getattr(self.coordinator.data, "station", None), "name", None)
        return make_device_info(
            self._sid,
            self._serial,
            self._station_uuid,
            station_name,
        )


class _ConnBase(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Common base for connector sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api: SmappeeApiClient,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
        name_suffix: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api
        self._sid = sid
        self._station_uuid = station_uuid
        self._uuid = connector_uuid
        self._serial = _station_serial(coordinator)
        # Globally unique connector-level entity id
        self._attr_unique_id = make_unique_id(
            sid, self._serial, station_uuid, connector_uuid, unique_suffix
        )
        cnum = getattr(api, "connector_number", None)
        num_lbl = f"{cnum}" if cnum is not None else connector_uuid[-4:]
        self._attr_name = f"Connector {num_lbl} {name_suffix}"

    @property
    def device_info(self):
        station_name = getattr(getattr(self.coordinator.data, "station", None), "name", None)
        return make_device_info(self._sid, self._serial, self._station_uuid, station_name)

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

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        super().__init__(coordinator, sid, station_uuid, "Grid power", "sensor:grid_power")

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_power_total", None)
        return float(v) if isinstance(v, int | float) else None


class StationHouseConsumptionPower(_Base):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        super().__init__(
            coordinator,
            sid,
            station_uuid,
            "House consumption power",
            "sensor:house_consumption_power",
        )

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "house_consumption_power", None)
        return float(v) if isinstance(v, int | float) else None


class StationPvPower(_Base):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        super().__init__(coordinator, sid, station_uuid, "PV power", "sensor:pv_power")

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "pv_power_total", None)
        return float(v) if isinstance(v, int | float) else None


class StationGridEnergyImport(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        super().__init__(
            coordinator, sid, station_uuid, "Grid energy import", "sensor:grid_energy_import_kwh"
        )
        self._last_value: float | None = None

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_energy_import_kwh", None)
        value = float(v) if isinstance(v, int | float) else None

        if value is None:
            return self._last_value
        if self._last_value is not None:
            if value < self._last_value or value == 0:
                return self._last_value
        self._last_value = value
        return value


class StationGridEnergyExport(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        super().__init__(
            coordinator, sid, station_uuid, "Grid energy export", "sensor:grid_energy_export_kwh"
        )
        self._last_value: float | None = None

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_energy_export_kwh", None)
        value = float(v) if isinstance(v, int | float) else None

        if value is None:
            return self._last_value
        if self._last_value is not None:
            if value < self._last_value or value == 0:
                return self._last_value
        self._last_value = value
        return value


class StationPvEnergyImport(_Base):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        super().__init__(
            coordinator, sid, station_uuid, "PV energy import", "sensor:pv_energy_import_kwh"
        )
        self._last_value: float | None = None

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "pv_energy_import_kwh", None)
        value = float(v) if isinstance(v, int | float) else None

        if value is None:
            return self._last_value
        if self._last_value is not None:
            if value < self._last_value or value == 0:
                return self._last_value
        self._last_value = value
        return value


# --------------- Connector sensors ---------------


class ConnCurrentL1(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, sid, station_uuid, uuid):
        super().__init__(c, api, sid, station_uuid, uuid, "Current L1", "sensor:current_l1")

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class ConnCurrentL2(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, sid, station_uuid, uuid):
        super().__init__(c, api, sid, station_uuid, uuid, "Current L2", "sensor:current_l2")

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class ConnCurrentL3(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, sid, station_uuid, uuid):
        super().__init__(c, api, sid, station_uuid, uuid, "Current L3", "sensor:current_l3")

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class ConnectorPowerSensor(_ConnBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        super().__init__(c, api, sid, station_uuid, uuid, "Power", "sensor:power_total")

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "power_total", None) if st else None
        return float(v) if isinstance(v, int | float) else None


class ConnectorCurrentASensor(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        super().__init__(c, api, sid, station_uuid, uuid, "Current", "sensor:current_total")

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

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        super().__init__(
            c, api, sid, station_uuid, uuid, "Energy import", "sensor:energy_import_kwh"
        )
        self._last_value: float | None = None

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "energy_import_kwh", None) if st else None
        value = float(v) if isinstance(v, int | float) else None

        # Only update if value is valid and not less than previous
        if value is None:
            return self._last_value
        if self._last_value is not None:
            if value < self._last_value or value == 0:
                return self._last_value
        self._last_value = value
        return value


class StationGridCurrents(_Base):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, api, sid, station_uuid):
        super().__init__(
            coordinator, sid, station_uuid, "Grid current (L1–L3)", "sensor:grid_currents"
        )

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

    def __init__(self, coordinator, api, sid, station_uuid):
        super().__init__(coordinator, sid, station_uuid, "PV current (L1–L3)", "sensor:pv_currents")

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

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        super().__init__(c, api, sid, station_uuid, uuid, "Charging state", "sensor:charging_state")

    @property
    def native_value(self) -> str | None:
        st = self._conn_state
        return str(getattr(st, "session_state", None)) if st else None


class SmappeeEVCCStateSensor(_ConnBase):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, api, sid, station_uuid, uuid):
        super().__init__(c, api, sid, station_uuid, uuid, "EVCC state", "sensor:evcc_state")

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

    def __init__(self, c, api, sid, station_uuid, uuid):
        super().__init__(c, api, sid, station_uuid, uuid, "EVSE status", "sensor:status_current")

    @property
    def native_value(self):
        st = self._conn_state
        return str(getattr(st, "status_current", None)) if st else None


class SmappeeMqttLastSeenSensor(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Station-scope 'last MQTT RX' as timestamp sensor."""

    _attr_has_entity_name = True
    _attr_name = "MQTT last seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # Disable by default in UI
    _attr_icon = "mdi:clock-check"

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
        self._serial = _station_serial(coordinator)
        self._attr_unique_id = make_unique_id(
            sid, self._serial, station_uuid, None, "sensor:mqtt_last_seen"
        )

    @property
    def device_info(self):
        station_name = getattr(getattr(self.coordinator.data, "station", None), "name", None)
        return make_device_info(self._sid, self._serial, self._station_uuid, station_name)

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data
        st = data.station if data else None
        ts = getattr(st, "last_mqtt_rx", None)
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(float(ts), tz=UTC)
        except (TypeError, ValueError):
            return None
