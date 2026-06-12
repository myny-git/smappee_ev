from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api_client import SmappeeApiClient
from .base_entities import SmappeeConnectorEntity, SmappeeStationEntity
from .coordinator import SmappeeCoordinator
from .data import SmappeeEvConfigEntry
from .helpers import build_connector_label, safe_sum, update_total_increasing

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    # Access runtime data directly (preferred over hass.data lookups)
    runtime = config_entry.runtime_data
    sites = (
        runtime.sites
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
            entities.append(StationGridCurrentL1(coord, st_client, sid, st_uuid))
            entities.append(StationGridCurrentL2(coord, st_client, sid, st_uuid))
            entities.append(StationGridCurrentL3(coord, st_client, sid, st_uuid))
            entities.append(StationPvCurrents(coord, st_client, sid, st_uuid))
            entities.append(StationPvCurrentL1(coord, st_client, sid, st_uuid))
            entities.append(StationPvCurrentL2(coord, st_client, sid, st_uuid))
            entities.append(StationPvCurrentL3(coord, st_client, sid, st_uuid))
            entities.append(StationGridVoltageL1(coord, st_client, sid, st_uuid))
            entities.append(StationGridVoltageL2(coord, st_client, sid, st_uuid))
            entities.append(StationGridVoltageL3(coord, st_client, sid, st_uuid))

            # ---- Connector sensors ----
            for cuuid, client in (conns or {}).items():
                entities.append(ConnectorPowerSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnectorCurrentASensor(coord, client, sid, st_uuid, cuuid))
                entities.append(SmappeeSupportGridSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnEnergyImport(coord, client, sid, st_uuid, cuuid))
                entities.append(SmappeeChargingStateSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(SmappeeEVCCStateSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(SmappeeEvseStatusSensor(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnCurrentL1(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnCurrentL2(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnCurrentL3(coord, client, sid, st_uuid, cuuid))
                entities.append(ConnectorSessionEnergySensor(coord, client, sid, st_uuid, cuuid))

    async_add_entities(entities, False)


# --------------- Bases ---------------


############################################################
# Station sensors
############################################################


# --------------- Station sensors ---------------


class StationGridPower(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_power",
            name="Grid power",
        )

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_power_total", None)
        return float(v) if isinstance(v, int | float) else None


class StationHouseConsumptionPower(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:house_consumption_power",
            name="House consumption power",
        )

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "house_consumption_power", None)
        return float(v) if isinstance(v, int | float) else None


class StationPvPower(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:pv_power",
            name="PV power",
        )

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "pv_power_total", None)
        return float(v) if isinstance(v, int | float) else None


def _total_increasing_value(entity: object, candidate: object) -> float | None:
    """Protect total-increasing sensors from decreasing across restarts."""
    last = getattr(entity, "_last_value", None)
    raw_value: float | None = None
    if not isinstance(candidate, bool) and candidate is not None:
        with contextlib.suppress(TypeError, ValueError):
            raw_value = float(candidate)  # type: ignore[arg-type]
    value = update_total_increasing(last, raw_value)
    entity._last_value = value  # type: ignore[attr-defined]
    return value


async def _async_restore_last_total_value(sensor: RestoreSensor) -> None:
    last = await sensor.async_get_last_sensor_data()
    if last is None or last.native_value is None:
        return
    if isinstance(last.native_value, int | float | str):
        with contextlib.suppress(TypeError, ValueError):
            sensor._last_value = float(last.native_value)  # type: ignore[attr-defined]


class RestoredEnergyStationSensor(SmappeeStationEntity, RestoreSensor):
    """Station energy sensor with restore support and coordinator lifecycle."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def _total_increasing_value(self, candidate: object) -> float | None:
        return _total_increasing_value(self, candidate)

    async def async_added_to_hass(self) -> None:
        await SmappeeStationEntity.async_added_to_hass(self)
        await _async_restore_last_total_value(self)


class RestoredEnergyConnectorSensor(SmappeeConnectorEntity, RestoreSensor):
    """Connector energy sensor with restore support and coordinator lifecycle."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def _total_increasing_value(self, candidate: object) -> float | None:
        return _total_increasing_value(self, candidate)

    async def async_added_to_hass(self) -> None:
        await SmappeeConnectorEntity.async_added_to_hass(self)
        await _async_restore_last_total_value(self)


class StationGridEnergyImport(RestoredEnergyStationSensor):
    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_energy_import_kwh",
            name="Grid energy import",
        )

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        return self._total_increasing_value(getattr(st, "grid_energy_import_kwh", None))


class StationGridEnergyExport(RestoredEnergyStationSensor):
    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_energy_export_kwh",
            name="Grid energy export",
        )

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        return self._total_increasing_value(getattr(st, "grid_energy_export_kwh", None))


class StationPvEnergyImport(RestoredEnergyStationSensor):
    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:pv_energy_import_kwh",
            name="PV energy import",
        )

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        return self._total_increasing_value(getattr(st, "pv_energy_import_kwh", None))


# --------------- Connector sensors ---------------


############################################################
# Connector sensors
############################################################


class ConnCurrentL1(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Current L1"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:current_l1", name=name
        )
        self.api_client = api

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class ConnCurrentL2(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Current L2"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:current_l2", name=name
        )
        self.api_client = api

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class ConnCurrentL3(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Current L3"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:current_l3", name=name
        )
        self.api_client = api

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class ConnectorPowerSensor(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Power"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:power_total", name=name
        )
        self.api_client = api

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "power_total", None) if st else None
        return float(v) if isinstance(v, int | float) else None


class ConnectorCurrentASensor(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Current"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:current_total", name=name
        )
        self.api_client = api

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


class SmappeeSupportGridSensor(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Support Grid"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:support_grid", name=name
        )
        self.api_client = api

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "support_grid", None) if st else None
        return float(v) if isinstance(v, int | float) else None


class ConnEnergyImport(RestoredEnergyConnectorSensor):
    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Energy import"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:energy_import_kwh", name=name
        )
        self.api_client = api

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        return self._total_increasing_value(getattr(st, "energy_import_kwh", None) if st else None)


class StationGridCurrents(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_currents",
            name="Grid current (L1–L3)",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_current_phases", None) if st else None
        total = safe_sum(vals)
        return round(total, 3) if isinstance(total, float) else None

    @property
    def extra_state_attributes(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_current_phases", None) if st else None
        return (
            {"L1": vals[0], "L2": vals[1], "L3": vals[2]}
            if isinstance(vals, list) and len(vals) >= 3
            else {}
        )


class StationPvCurrents(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:pv_currents",
            name="PV current (L1–L3)",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "pv_current_phases", None) if st else None
        total = safe_sum(vals)
        return round(total, 3) if isinstance(total, float) else None

    @property
    def extra_state_attributes(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "pv_current_phases", None) if st else None
        return (
            {"L1": vals[0], "L2": vals[1], "L3": vals[2]}
            if isinstance(vals, list) and len(vals) >= 3
            else {}
        )


class StationGridCurrentL1(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_current_l1",
            name="Grid current L1",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_current_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class StationGridCurrentL2(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_current_l2",
            name="Grid current L2",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_current_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class StationGridCurrentL3(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_current_l3",
            name="Grid current L3",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_current_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class StationPvCurrentL1(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:pv_current_l1",
            name="PV current L1",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "pv_current_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class StationPvCurrentL2(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:pv_current_l2",
            name="PV current L2",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "pv_current_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class StationPvCurrentL3(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:pv_current_l3",
            name="PV current L3",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "pv_current_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class StationGridVoltageL1(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_voltage_l1",
            name="Grid voltage L1",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_voltage_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class StationGridVoltageL2(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_voltage_l2",
            name="Grid voltage L2",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_voltage_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class StationGridVoltageL3(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(
        self, coordinator: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:grid_voltage_l3",
            name="Grid voltage L3",
        )

    @property
    def native_value(self):
        st = self.coordinator.data.station if self.coordinator.data else None
        vals = getattr(st, "grid_voltage_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class SmappeeChargingStateSensor(SmappeeConnectorEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Charging state"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:charging_state", name=name
        )
        self.api_client = api

    @property
    def native_value(self) -> str | None:
        st = self._conn_state
        value = getattr(st, "session_state", None) if st else None
        return str(value) if value is not None else None


class SmappeeEVCCStateSensor(SmappeeConnectorEntity, RestoreSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} EVCC state"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:evcc_state", name=name
        )
        self.api_client = api
        self._restored_value: str | None = None
        self._restored_attributes: dict[str, object] = {}

    @property
    def native_value(self):
        st = self._conn_state
        value = getattr(st, "evcc_state", None) if st else None
        if value is not None:
            return str(value)
        return self._restored_value

    @property
    def extra_state_attributes(self):
        st = self._conn_state
        if st:
            return {
                "iec_status": getattr(st, "iec_status", None),
                "session_state": getattr(st, "session_state", None),
                "charging_mode": getattr(st, "raw_charging_mode", None),
                "optimization_strategy": getattr(st, "optimization_strategy", None),
                "paused": getattr(st, "paused", None),
                "status_current": getattr(st, "status_current", None),
            }
        # Return restored attributes if we have them and no current state
        if self._restored_attributes:
            return self._restored_attributes
        return None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await SmappeeConnectorEntity.async_added_to_hass(self)

        # Restore previous state if available
        last_data = await self.async_get_last_sensor_data()
        restored_value = last_data.native_value if last_data else None
        if isinstance(restored_value, str) and restored_value in ("unknown", "unavailable"):
            restored_value = None
        if restored_value is not None:
            self._restored_value = str(restored_value)

        # Restore attributes (not part of RestoreSensor data)
        last_state = await self.async_get_last_state()
        if last_state and last_state.attributes:
            self._restored_attributes = {
                "iec_status": last_state.attributes.get("iec_status"),
                "session_state": last_state.attributes.get("session_state"),
                "charging_mode": last_state.attributes.get("charging_mode"),
                "optimization_strategy": last_state.attributes.get("optimization_strategy"),
                "paused": last_state.attributes.get("paused"),
                "status_current": last_state.attributes.get("status_current"),
            }

        if self._restored_value is not None or self._restored_attributes:
            self.async_write_ha_state()


class SmappeeEvseStatusSensor(SmappeeConnectorEntity, RestoreSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} EVSE status"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:status_current", name=name
        )
        self.api_client = api
        self._restored_value: str | None = None

    @property
    def native_value(self):
        st = self._conn_state
        value = getattr(st, "status_current", None) if st else None
        if value is not None:
            return str(value)
        return self._restored_value

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await SmappeeConnectorEntity.async_added_to_hass(self)

        # Restore previous state if available
        last_data = await self.async_get_last_sensor_data()
        restored_value = last_data.native_value if last_data else None
        if isinstance(restored_value, str) and restored_value in ("unknown", "unavailable"):
            restored_value = None
        if restored_value is not None:
            self._restored_value = str(restored_value)
            self.async_write_ha_state()


class SmappeeMqttLastSeenSensor(SmappeeStationEntity, SensorEntity):
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
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="sensor:mqtt_last_seen",
            name="MQTT last seen",
        )
        self.api_client = api_client

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


def _session_ts_to_datetime(value: object) -> datetime | None:
    """Convert session timestamps that may be seconds or milliseconds."""
    try:
        ts = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if abs(ts) > 10_000_000_000:
        ts /= 1000
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _nested_value(data: dict[str, Any], *paths: tuple[str, ...]) -> object:
    for path in paths:
        cur: object = data
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                cur = None
                break
            cur = cur[key]
        if cur not in (None, ""):
            return cur
    return None


class ConnectorSessionEnergySensor(SmappeeConnectorEntity, SensorEntity):
    """Energy reported for the current or most recent cloud charging session."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Session energy"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:session_energy", name=name
        )
        self.api_client = api

    def _session_matches_connector(self, session: dict[str, Any]) -> bool:
        connector_uuid = str(self.connector_uuid)
        smart_device_id = str(self.api_client.smart_device_id)
        connector_number = str(self.api_client.connector_number or "")
        raw_station_serial = self.api_client.charging_station_serial or self.api_client.serial
        station_serial = str(raw_station_serial) if raw_station_serial else ""

        uuid_value = _nested_value(
            session,
            ("connectorUuid",),
            ("connectorUUID",),
            ("smartDeviceUuid",),
            ("smartDeviceUUID",),
            ("deviceUuid",),
            ("deviceUUID",),
            ("connector", "uuid"),
            ("smartDevice", "uuid"),
            ("device", "uuid"),
        )
        if uuid_value is not None and str(uuid_value) == connector_uuid:
            return True

        id_value = _nested_value(
            session,
            ("connectorId",),
            ("connectorID",),
            ("smartDeviceId",),
            ("smartDeviceID",),
            ("deviceId",),
            ("deviceID",),
            ("connector", "id"),
            ("smartDevice", "id"),
            ("device", "id"),
        )
        if id_value is not None and str(id_value) == smart_device_id:
            return True

        number_value = _nested_value(
            session,
            ("connectorNumber",),
            ("connector", "number"),
            ("connector", "position"),
            ("position",),
        )
        serial_value = _nested_value(
            session,
            ("chargingStationSerial",),
            ("stationSerial",),
            ("serialNumber",),
            ("chargingStation", "serialNumber"),
            ("chargingStation", "serial"),
        )
        return (
            number_value is not None
            and bool(connector_number)
            and str(number_value) == connector_number
            and (
                serial_value is None
                or (bool(station_serial) and str(serial_value) == station_serial)
            )
        )

    @property
    def _active_session_data(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        sessions = self.coordinator.data.recent_sessions
        if not isinstance(sessions, list):
            return {}
        for session in sessions:
            if isinstance(session, dict) and self._session_matches_connector(session):
                return session
        if (
            len(self.coordinator.connector_clients) == 1
            and sessions
            and isinstance(sessions[0], dict)
            and "energy" in sessions[0]
        ):
            return sessions[0]
        return {}

    @property
    def native_value(self) -> float | None:
        energy_kwh = self._active_session_data.get("energy")
        if energy_kwh is None:
            return None
        try:
            return round(float(energy_kwh), 2)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        session = self._active_session_data
        if not session:
            return {}

        attrs = dict(session)
        attrs.pop("energy", None)

        start_time = _session_ts_to_datetime(session.get("from"))
        if start_time is not None:
            attrs["from"] = start_time.isoformat()

        end_time = _session_ts_to_datetime(session.get("to"))
        if end_time is not None:
            attrs["to"] = end_time.isoformat()

        if start_time is not None:
            if end_time is None:
                end_time = datetime.now(UTC)
            duration = end_time - start_time
            attrs["duration_minutes"] = round(duration.total_seconds() / 60.0, 1)
        return attrs
