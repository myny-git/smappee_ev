from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api_client import SmappeeApiClient
from .base_entities import SmappeeConnectorEntity, SmappeeStationEntity
from .coordinator import SmappeeCoordinator
from .data import RuntimeData
from .helpers import build_connector_label, safe_sum, update_total_increasing


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    # Access runtime data directly (preferred over hass.data lookups)
    runtime: RuntimeData = config_entry.runtime_data  # type: ignore[attr-defined]
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


class StationGridEnergyImport(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

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
        self._last_value: float | None = None

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_energy_import_kwh", None)
        candidate = float(v) if isinstance(v, int | float) else None
        value = update_total_increasing(self._last_value, candidate)
        self._last_value = value
        return value


class StationGridEnergyExport(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

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
        self._last_value: float | None = None

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "grid_energy_export_kwh", None)
        candidate = float(v) if isinstance(v, int | float) else None
        value = update_total_increasing(self._last_value, candidate)
        self._last_value = value
        return value


class StationPvEnergyImport(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

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
        self._last_value: float | None = None

    @property
    def native_value(self) -> float | None:
        st = self.coordinator.data.station if self.coordinator.data else None
        v = getattr(st, "pv_energy_import_kwh", None)
        candidate = float(v) if isinstance(v, int | float) else None
        value = update_total_increasing(self._last_value, candidate)
        self._last_value = value
        return value


# --------------- Connector sensors ---------------


############################################################
# Connector sensors
############################################################


class ConnCurrentL1(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, sid, station_uuid, uuid):
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

    def __init__(self, c, api, sid, station_uuid, uuid):
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

    def __init__(self, c, api, sid, station_uuid, uuid):
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


class ConnEnergyImport(SmappeeConnectorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self, c: SmappeeCoordinator, api: SmappeeApiClient, sid: int, station_uuid: str, uuid: str
    ) -> None:
        name = f"{build_connector_label(api, uuid)} Energy import"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:energy_import_kwh", name=name
        )
        self.api_client = api
        self._last_value: float | None = None

    @property
    def native_value(self) -> float | None:
        st = self._conn_state
        v = getattr(st, "energy_import_kwh", None) if st else None
        candidate = float(v) if isinstance(v, int | float) else None
        value = update_total_increasing(self._last_value, candidate)
        self._last_value = value
        return value


class StationGridCurrents(SmappeeStationEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, coordinator, api, sid, station_uuid):
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

    def __init__(self, coordinator, api, sid, station_uuid):
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
        return str(getattr(st, "session_state", None)) if st else None


class SmappeeEVCCStateSensor(SmappeeConnectorEntity, SensorEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, api, sid, station_uuid, uuid):
        name = f"{build_connector_label(api, uuid)} EVCC state"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:evcc_state", name=name
        )
        self.api_client = api
        self._restored_value = None
        self._restored_attributes = {}

    @property
    def native_value(self):
        st = self._conn_state
        if st and getattr(st, "evcc_state", None) is not None:
            return str(getattr(st, "evcc_state", None))
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
                "status_current": getattr(st, "session_cause", None),
            }
        # Return restored attributes if we have them and no current state
        if self._restored_attributes:
            return self._restored_attributes
        return None

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore previous state if available
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            self._restored_value = last_state.state
            # Also restore attributes
            if last_state.attributes:
                self._restored_attributes = {
                    "iec_status": last_state.attributes.get("iec_status"),
                    "session_state": last_state.attributes.get("session_state"),
                    "charging_mode": last_state.attributes.get("charging_mode"),
                    "optimization_strategy": last_state.attributes.get("optimization_strategy"),
                    "paused": last_state.attributes.get("paused"),
                    "status_current": last_state.attributes.get("status_current"),
                }
            self.async_write_ha_state()


class SmappeeEvseStatusSensor(SmappeeConnectorEntity, SensorEntity, RestoreEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, c, api, sid, station_uuid, uuid):
        name = f"{build_connector_label(api, uuid)} EVSE status"
        SmappeeConnectorEntity.__init__(
            self, c, sid, station_uuid, uuid, unique_suffix="sensor:status_current", name=name
        )
        self.api_client = api
        self._restored_value = None

    @property
    def native_value(self):
        st = self._conn_state
        if st and getattr(st, "status_current", None) is not None:
            return str(getattr(st, "status_current", None))
        return self._restored_value

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore previous state if available
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            self._restored_value = last_state.state
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
