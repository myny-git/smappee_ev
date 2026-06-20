"""Sensor platform for the Smappee EV integration."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorExtraStoredData,
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
from homeassistant.util import dt as dt_util

from .base_entities import SmappeeConnectorEntity, SmappeeConnectorMqttEntity, SmappeeSiteEntity
from .coordinator import SmappeeCoordinator, SmappeeSiteCoordinator
from .data import SmappeeEvConfigEntry
from .device_handle import SmappeeDeviceHandle
from .helpers import format_as_hms, safe_sum, update_total_increasing

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    # Access runtime data directly (preferred over hass.data lookups)
    runtime = config_entry.runtime_data

    entities: list[SensorEntity] = []

    for sid, site in (runtime.sites or {}).items():
        sid_int = int(sid)
        site_coord = cast(SmappeeSiteCoordinator | SmappeeCoordinator | None, site.site_coordinator)
        if site_coord is None:
            first_bucket = next(iter(site.stations.values()), None)
            site_coord = cast(
                SmappeeSiteCoordinator | SmappeeCoordinator | None,
                first_bucket.station_coordinator if first_bucket else None,
            )
        if site_coord is not None:
            entities.append(SmappeeMqttLastSeenSensor(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridPower(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationPvPower(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationHouseConsumptionPower(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridEnergyImport(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridEnergyExport(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationPvEnergyImport(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridCurrents(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridCurrentL1(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridCurrentL2(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridCurrentL3(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationPvCurrents(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationPvCurrentL1(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationPvCurrentL2(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationPvCurrentL3(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridVoltageL1(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridVoltageL2(site_coord, None, sid_int, f"site-{sid}"))
            entities.append(StationGridVoltageL3(site_coord, None, sid_int, f"site-{sid}"))

        for st_uuid, bucket in site.stations.items():
            coord = cast(SmappeeCoordinator | None, bucket.station_coordinator)
            if coord is None:
                continue
            conns: dict[str, SmappeeDeviceHandle] = {
                key: conn.connector_client for key, conn in bucket.connectors.items()
            }

            # ---- Connector sensors ----
            for cuuid, client in (conns or {}).items():
                entities.append(ConnectorPowerSensor(coord, client, sid_int, st_uuid, cuuid))
                entities.append(ConnectorCurrentASensor(coord, client, sid_int, st_uuid, cuuid))
                entities.append(SmappeeSupportGridSensor(coord, client, sid_int, st_uuid, cuuid))
                entities.append(ConnEnergyImport(coord, client, sid_int, st_uuid, cuuid))
                entities.append(SmappeeChargingStateSensor(coord, client, sid_int, st_uuid, cuuid))
                entities.append(SmappeeEVCCStateSensor(coord, client, sid_int, st_uuid, cuuid))
                entities.append(SmappeeEvseStatusSensor(coord, client, sid_int, st_uuid, cuuid))
                entities.append(ConnCurrentL1(coord, client, sid_int, st_uuid, cuuid))
                entities.append(ConnCurrentL2(coord, client, sid_int, st_uuid, cuuid))
                entities.append(ConnCurrentL3(coord, client, sid_int, st_uuid, cuuid))
                entities.append(
                    ConnectorSessionEnergySensor(coord, client, sid_int, st_uuid, cuuid)
                )

    async_add_entities(entities, False)


# --------------- Bases ---------------


############################################################
# Station sensors
############################################################


# --------------- Station sensors ---------------


def _site_state(coordinator: SmappeeSiteCoordinator | SmappeeCoordinator):
    data = getattr(coordinator, "data", None)
    if data is None:
        return None
    return getattr(data, "site", None) or getattr(data, "station", None)


def _safe_write_ha_state(entity: SensorEntity) -> None:
    if getattr(entity, "platform", None) is not None:
        entity.async_write_ha_state()


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool | None):
        return None
    if isinstance(value, int | float | str):
        with suppress(TypeError, ValueError):
            return float(value)
    return None


class _RestoredTotalSensor(Protocol):
    _last_value: float | None

    async def async_get_last_sensor_data(self) -> SensorExtraStoredData | None:
        """Return restored sensor data."""


class StationGridPower(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_translation_key = "grid_power"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_power",
        )

    @property
    def native_value(self) -> float | None:
        st = _site_state(self.coordinator)
        v = getattr(st, "grid_power_total", None)
        return float(v) if isinstance(v, int | float) else None


class StationHouseConsumptionPower(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_translation_key = "house_consumption_power"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:house_consumption_power",
        )

    @property
    def native_value(self) -> float | None:
        st = _site_state(self.coordinator)
        v = getattr(st, "house_consumption_power", None)
        return float(v) if isinstance(v, int | float) else None


class StationPvPower(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_translation_key = "pv_power"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:pv_power",
        )

    @property
    def native_value(self) -> float | None:
        st = _site_state(self.coordinator)
        v = getattr(st, "pv_power_total", None)
        return float(v) if isinstance(v, int | float) else None


def _total_increasing_value(entity: _RestoredTotalSensor, candidate: object) -> float | None:
    """Protect total-increasing sensors from decreasing across restarts."""
    last = entity._last_value
    raw_value = _coerce_float(candidate)
    value = update_total_increasing(last, raw_value)
    entity._last_value = value
    return value


async def _async_restore_last_total_value(sensor: _RestoredTotalSensor) -> None:
    last = await sensor.async_get_last_sensor_data()
    if last is None or last.native_value is None:
        return
    restored_value = _coerce_float(last.native_value)
    if restored_value is not None:
        sensor._last_value = restored_value


class RestoredEnergyStationSensor(SmappeeSiteEntity, RestoreSensor):
    """Station energy sensor with restore support and coordinator lifecycle."""

    _last_value: float | None = None
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def _total_increasing_value(self, candidate: object) -> float | None:
        return _total_increasing_value(self, candidate)

    async def async_added_to_hass(self) -> None:
        await SmappeeSiteEntity.async_added_to_hass(self)
        await _async_restore_last_total_value(self)


class RestoredEnergyConnectorSensor(SmappeeConnectorMqttEntity, RestoreSensor):
    """Connector energy sensor with restore support and coordinator lifecycle."""

    _last_value: float | None = None
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def _total_increasing_value(self, candidate: object) -> float | None:
        return _total_increasing_value(self, candidate)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await _async_restore_last_total_value(self)


class StationGridEnergyImport(RestoredEnergyStationSensor):
    _attr_translation_key = "grid_energy_import"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_energy_import_kwh",
        )

    @property
    def native_value(self) -> float | None:
        st = _site_state(self.coordinator)
        return self._total_increasing_value(getattr(st, "grid_energy_import_kwh", None))


class StationGridEnergyExport(RestoredEnergyStationSensor):
    _attr_translation_key = "grid_energy_export"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_energy_export_kwh",
        )

    @property
    def native_value(self) -> float | None:
        st = _site_state(self.coordinator)
        return self._total_increasing_value(getattr(st, "grid_energy_export_kwh", None))


class StationPvEnergyImport(RestoredEnergyStationSensor):
    _attr_translation_key = "pv_energy_import"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:pv_energy_import_kwh",
        )

    @property
    def native_value(self) -> float | None:
        st = _site_state(self.coordinator)
        return self._total_increasing_value(getattr(st, "pv_energy_import_kwh", None))


# --------------- Connector sensors ---------------


############################################################
# Connector sensors
############################################################


class ConnCurrentL1(SmappeeConnectorMqttEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "current_l1"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:current_l1"
        )
        self.api_client = api

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class ConnCurrentL2(SmappeeConnectorMqttEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "current_l2"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:current_l2"
        )
        self.api_client = api

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class ConnCurrentL3(SmappeeConnectorMqttEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "current_l3"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:current_l3"
        )
        self.api_client = api

    @property
    def native_value(self):
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class ConnectorPowerSensor(SmappeeConnectorMqttEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_translation_key = "power"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:power_total"
        )
        self.api_client = api

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "power_total", None) if st else None
        return float(v) if isinstance(v, int | float) else None


class ConnectorCurrentASensor(SmappeeConnectorMqttEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "current"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:current_total"
        )
        self.api_client = api

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        vals = getattr(st, "current_phases", None) if st else None
        if isinstance(vals, list) and vals:
            with suppress(TypeError, ValueError):
                return float(sum(float(x) for x in vals))
        return None


class SmappeeSupportGridSensor(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "support_grid"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:support_grid"
        )
        self.api_client = api

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "support_grid", None) if st else None
        return float(v) if isinstance(v, int | float) else None


class ConnEnergyImport(RestoredEnergyConnectorSensor):
    _attr_translation_key = "energy_import"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self,
            c,
            api,
            sid,
            station_uuid,
            uuid,
            unique_suffix="sensor:energy_import_kwh",
        )
        self.api_client = api

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        return self._total_increasing_value(getattr(st, "energy_import_kwh", None) if st else None)


class StationGridCurrents(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "grid_currents"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_currents",
        )
        self._attr_name = "Grid current (L1-L3)"

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "grid_current_phases", None) if st else None
        total = safe_sum(vals)
        return round(total, 3) if isinstance(total, float) else None

    @property
    def extra_state_attributes(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "grid_current_phases", None) if st else None
        return (
            {"L1": vals[0], "L2": vals[1], "L3": vals[2]}
            if isinstance(vals, list) and len(vals) >= 3
            else {}
        )


class StationPvCurrents(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "pv_currents"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:pv_currents",
        )
        self._attr_name = "PV current (L1-L3)"

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "pv_current_phases", None) if st else None
        total = safe_sum(vals)
        return round(total, 3) if isinstance(total, float) else None

    @property
    def extra_state_attributes(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "pv_current_phases", None) if st else None
        return (
            {"L1": vals[0], "L2": vals[1], "L3": vals[2]}
            if isinstance(vals, list) and len(vals) >= 3
            else {}
        )


class StationGridCurrentL1(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "grid_current_l1"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_current_l1",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "grid_current_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class StationGridCurrentL2(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "grid_current_l2"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_current_l2",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "grid_current_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class StationGridCurrentL3(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "grid_current_l3"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_current_l3",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "grid_current_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class StationPvCurrentL1(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "pv_current_l1"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:pv_current_l1",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "pv_current_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class StationPvCurrentL2(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "pv_current_l2"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:pv_current_l2",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "pv_current_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class StationPvCurrentL3(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_translation_key = "pv_current_l3"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:pv_current_l3",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "pv_current_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class StationGridVoltageL1(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_translation_key = "grid_voltage_l1"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_voltage_l1",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "grid_voltage_phases", None) if st else None
        return float(vals[0]) if isinstance(vals, list) and len(vals) >= 1 else None


class StationGridVoltageL2(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_translation_key = "grid_voltage_l2"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_voltage_l2",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "grid_voltage_phases", None) if st else None
        return float(vals[1]) if isinstance(vals, list) and len(vals) >= 2 else None


class StationGridVoltageL3(SmappeeSiteEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_translation_key = "grid_voltage_l3"

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:grid_voltage_l3",
        )

    @property
    def native_value(self):
        st = _site_state(self.coordinator)
        vals = getattr(st, "grid_voltage_phases", None) if st else None
        return float(vals[2]) if isinstance(vals, list) and len(vals) >= 3 else None


class SmappeeChargingStateSensor(SmappeeConnectorMqttEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "charging_state"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:charging_state"
        )
        self.api_client = api

    @property
    def native_value(self) -> str | None:
        """Return the current session state."""
        st = self._conn_state
        if st is None:
            return None

        value = getattr(st, "session_state", None)
        return str(value).lower() if value is not None else None


class SmappeeEVCCStateSensor(SmappeeConnectorMqttEntity, RestoreSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "evcc_state"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:evcc_state"
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
        await super().async_added_to_hass()

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
            _safe_write_ha_state(self)


class SmappeeEvseStatusSensor(SmappeeConnectorMqttEntity, RestoreSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "evse_status"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:status_current"
        )
        self.api_client = api
        self._restored_value: str | None = None

    @property
    def native_value(self):
        st = self._conn_state
        value = getattr(st, "status_current", None) if st else None
        if value is not None:
            return str(value).lower()
        return self._restored_value

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore previous state if available
        last_data = await self.async_get_last_sensor_data()
        restored_value = last_data.native_value if last_data else None
        if isinstance(restored_value, str) and restored_value in ("unknown", "unavailable"):
            restored_value = None
        if restored_value is not None:
            self._restored_value = str(restored_value)
            _safe_write_ha_state(self)


class SmappeeMqttLastSeenSensor(SmappeeSiteEntity, SensorEntity):
    """Site-scope 'last MQTT RX' as timestamp sensor."""

    _attr_translation_key = "mqtt_last_seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # Disable by default in UI

    def __init__(
        self,
        coordinator: SmappeeSiteCoordinator | SmappeeCoordinator,
        api_client: SmappeeDeviceHandle | None,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="sensor:mqtt_last_seen",
        )
        self.api_client = api_client

    @property
    def native_value(self) -> datetime | None:
        st = _site_state(self.coordinator)
        ts = getattr(st, "last_mqtt_rx", None)
        if ts is None:
            return None
        with suppress(TypeError, ValueError):
            return datetime.fromtimestamp(float(ts), tz=UTC)
        return None


def _session_ts_to_datetime(value: object) -> datetime | None:
    """Convert session timestamps that may be seconds or milliseconds."""
    ts = _coerce_float(value)
    if ts is None:
        return None
    if abs(ts) > 10_000_000_000:
        ts /= 1000
    with suppress(OSError, OverflowError, ValueError):
        return datetime.fromtimestamp(ts, tz=UTC)
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
    _attr_translation_key = "session_energy"

    def __init__(
        self,
        c: SmappeeCoordinator,
        api: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self, c, api, sid, station_uuid, uuid, unique_suffix="sensor:session_energy"
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

    def _active_session_match(self) -> tuple[dict[str, Any], bool]:
        if not self.coordinator.data:
            return {}, False
        sessions = self.coordinator.data.recent_sessions
        if not isinstance(sessions, list):
            return {}, False
        for session in sessions:
            if isinstance(session, dict) and self._session_matches_connector(session):
                return session, True
        if (
            len(self.coordinator.connector_clients) == 1
            and sessions
            and isinstance(sessions[0], dict)
            and "energy" in sessions[0]
        ):
            return sessions[0], False
        return {}, False

    @property
    def _active_session_data(self) -> dict[str, Any]:
        return self._active_session_match()[0]

    @property
    def native_value(self) -> float | None:
        energy_kwh = self._active_session_data.get("energy")
        if energy_kwh is None:
            return None
        with suppress(TypeError, ValueError):
            return round(float(energy_kwh), 2)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        session, connector_matched = self._active_session_match()
        if not session:
            return {}

        excluded = {"energy", "controller", "station", "address", "updateChannels", "rfidToken"}
        if not connector_matched:
            excluded |= {"from", "to"}
        attrs = {k: v for k, v in session.items() if k not in excluded}

        start_time = _session_ts_to_datetime(session.get("from"))
        end_time = _session_ts_to_datetime(session.get("to"))

        if connector_matched and start_time:
            attrs["from"] = start_time.isoformat()

            current_time = end_time if end_time else dt_util.now()
            duration = current_time - start_time

            # Keep duration in minutes for legacy/other purposes
            attrs["duration_minutes"] = round(duration.total_seconds() / 60.0, 1)

            # Use the new formatted duration (HH:MM:SS)
            attrs["duration_formatted"] = format_as_hms(duration)

        if connector_matched and end_time:
            attrs["to"] = end_time.isoformat()

        return attrs
