from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV sensors from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: dict[str, SmappeeApiClient] = data["connector_clients"]
    station_client: SmappeeApiClient = data["station_client"]

    entities: list[SensorEntity] = []
    for uuid, client in connector_clients.items():
        entities.append(
            SmappeeChargingStateSensor(coordinator=coordinator, api_client=client, uuid=uuid)
        )
        entities.append(
            SmappeeEVCCStateSensor(coordinator=coordinator, api_client=client, uuid=uuid)
        )
        entities.append(
            SmappeeEvseStatusSensor(coordinator=coordinator, api_client=client, uuid=uuid)
        )

    entities.append(SmappeeMqttLastSeenSensor(coordinator, station_client))

    entities += [
        StationGridPower(coordinator, station_client),
        StationGridEnergyImport(coordinator, station_client),
        StationGridEnergyExport(coordinator, station_client),
        StationGridCurrents(coordinator, station_client),
        StationPvPower(coordinator, station_client),
        StationPvEnergyExport(coordinator, station_client),
        StationPvCurrents(coordinator, station_client),
    ]

    # --------------------- ADDED: Per-connector power/current/energy ------------------
    for uuid, client in connector_clients.items():
        entities.append(ConnPowerTotal(coordinator, client, uuid))
        entities.append(ConnCurrentL1(coordinator, client, uuid))
        entities.append(ConnCurrentL2(coordinator, client, uuid))
        entities.append(ConnCurrentL3(coordinator, client, uuid))
        entities.append(ConnEnergyImport(coordinator, client, uuid))

    async_add_entities(entities, update_before_add=True)


class _Base(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Base class for Smappee EV sensors."""

    _attr_should_poll = False  # Event-driven, no polling

    def __init__(
        self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client  # kept only for device_info
        self._uuid = uuid

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._uuid) if data else None


class SmappeeChargingStateSensor(_Base):
    """Raw charging/session state reported by the connector."""

    def __init__(
        self, *, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str
    ) -> None:
        super().__init__(coordinator=coordinator, api_client=api_client, uuid=uuid)
        self._attr_name = f"Charging state {api_client.connector_number}"
        self._attr_unique_id = (
            f"{api_client.serial_id}_connector{api_client.connector_number}_charging_state"
        )
        self._attr_icon = "mdi:ev-station"

    @property
    def native_value(self):
        st = self._state()
        return st.session_state if st else None


class SmappeeEVCCStateSensor(_Base, RestoreEntity):
    """EVCC A/B/C/E mapping derived from the session state."""

    def __init__(
        self, *, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str
    ) -> None:
        super().__init__(coordinator=coordinator, api_client=api_client, uuid=uuid)
        self._attr_name = f"EVCC state {api_client.connector_number}"
        self._attr_unique_id = (
            f"{api_client.serial_id}_connector{api_client.connector_number}_evcc_state"
        )
        self._attr_icon = "mdi:car-electric"
        self._restored: str | None = None

    @property
    def native_value(self):
        st = self._state()
        # 1) live from coordinator
        if st and st.evcc_state:
            return st.evcc_state
        # 2) fallback: last known value
        return self._restored

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state and last.state not in ("unknown", "unavailable"):
            self._restored = last.state

    @property
    def extra_state_attributes(self):
        st = self._state()
        if not st:
            return None
        return {
            "iec_status": st.iec_status,  # example C2
            "session_state": st.session_state,  # STARTED/STOPPED/...
            "charging_mode": st.raw_charging_mode,  # NORMAL/SMART/PAUSED
            "optimization_strategy": st.optimization_strategy,
            "paused": st.paused,
            "status_current": st.session_cause,  # AP-status
        }


class SmappeeEvseStatusSensor(_Base):
    """Smappee Dashboard connector status."""

    def __init__(
        self, *, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, uuid: str
    ) -> None:
        super().__init__(coordinator=coordinator, api_client=api_client, uuid=uuid)
        self._attr_name = f"EVSE status {api_client.connector_number}"
        self._attr_unique_id = (
            f"{api_client.serial_id}_connector{api_client.connector_number}_evse_status"
        )
        self._attr_icon = "mdi:information-outline"

    @property
    def native_value(self):
        st = self._state()
        return st.status_current if st else None


class SmappeeMqttLastSeenSensor(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    """Station-scope 'last MQTT RX' as timestamp sensor."""

    _attr_has_entity_name = True
    _attr_name = "MQTT last seen"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check"

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
    def native_value(self) -> datetime | None:
        data: IntegrationData | None = self.coordinator.data
        st = data.station if data else None
        ts = getattr(st, "last_mqtt_rx", None)
        if not ts:
            return None
        return datetime.fromtimestamp(float(ts), tz=UTC)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data: IntegrationData | None = self.coordinator.data
        st = data.station if data else None
        if not st:
            return None
        return {
            "connected": bool(getattr(st, "mqtt_connected", False)),
        }


def _as_phases(obj: Any) -> list[float] | None:
    """Return a list[float] if obj is a sequence of numbers; otherwise None."""
    if isinstance(obj, Sequence) and not isinstance(obj, str | bytes):
        try:
            return [float(x) for x in obj]
        except (TypeError, ValueError):
            return None
    return None


class _StationBase(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, name: str, uid: str
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._attr_name = name
        self._attr_unique_id = f"{api_client.serial_id}_{uid}"

    @property
    def device_info(self) -> DeviceInfo:
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    @property
    def _st(self):
        d: IntegrationData | None = self.coordinator.data
        return d.station if d else None


class StationGridPower(_StationBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, c, api):
        super().__init__(c, api, "Grid power", "grid_power")

    @property
    def native_value(self):
        st = self._st
        return st.grid_power_total if st else None


class StationGridEnergyImport(_StationBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, c, api):
        super().__init__(c, api, "Grid energy import", "grid_energy_import_kwh")

    @property
    def native_value(self):
        st = self._st
        return st.grid_energy_import_kwh if st else None


class StationGridEnergyExport(_StationBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, c, api):
        super().__init__(c, api, "Grid energy export", "grid_energy_export_kwh")

    @property
    def native_value(self):
        st = self._st
        return st.grid_energy_export_kwh if st else None


class StationGridCurrents(_StationBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api):
        super().__init__(c, api, "Grid current (L1–L3)", "grid_currents")

    @property
    def native_value(self):
        st = self._st
        ph = _as_phases(st.grid_current_phases) if st else None
        return round(sum(ph), 3) if ph else None

    @property
    def extra_state_attributes(self):
        st = self._st
        ph = _as_phases(st.grid_current_phases) if st else None
        return {"L1": ph[0], "L2": ph[1], "L3": ph[2]} if ph and len(ph) >= 3 else {}


class StationPvPower(_StationBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, c, api):
        super().__init__(c, api, "PV power", "pv_power")

    @property
    def native_value(self):
        st = self._st
        return st.pv_power_total if st else None


class StationPvEnergyExport(_StationBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, c, api):
        super().__init__(c, api, "PV energy export", "pv_energy_export_kwh")

    @property
    def native_value(self):
        st = self._st
        return st.pv_energy_export_kwh if st else None


class StationPvCurrents(_StationBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api):
        super().__init__(c, api, "PV current (L1–L3)", "pv_currents")

    @property
    def native_value(self):
        st = self._st
        ph = _as_phases(st.pv_current_phases) if st else None
        return round(sum(ph), 3) if ph else None

    @property
    def extra_state_attributes(self):
        st = self._st
        ph = _as_phases(st.pv_current_phases) if st else None
        return {"L1": ph[0], "L2": ph[1], "L3": ph[2]} if ph and len(ph) >= 3 else {}


# ============================ ADDED: Per-connector sensors ===========================


class _ConnBase(CoordinatorEntity[SmappeeCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        uuid: str,
        name: str,
        uid: str,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._uuid = uuid
        self._attr_name = f"{name} {api_client.connector_number}"
        self._attr_unique_id = f"{api_client.serial_id}_conn{api_client.connector_number}_{uid}"

    @property
    def device_info(self) -> DeviceInfo:
        return {
            "identifiers": {(DOMAIN, f"{self.api_client.serial_id}:{self._uuid}")},
            "name": f"Smappee EV Connector {self.api_client.connector_number}",
            "manufacturer": "Smappee",
            "via_device": (DOMAIN, self.api_client.serial_id),
        }

    @property
    def _st(self) -> ConnectorState | None:
        d: IntegrationData | None = self.coordinator.data
        return d.connectors.get(self._uuid) if d else None


class ConnPowerTotal(_ConnBase):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, c, api, uuid):
        super().__init__(c, api, uuid, "Connector power", "power_total")

    @property
    def native_value(self):
        st = self._st
        return int(st.power_total) if st and st.power_total is not None else None


class ConnCurrentL1(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, uuid):
        super().__init__(c, api, uuid, "Connector current L1", "current_l1")

    @property
    def native_value(self):
        st = self._st
        return (
            float(st.current_phases[0])
            if st and st.current_phases and len(st.current_phases) >= 1
            else None
        )


class ConnCurrentL2(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, uuid):
        super().__init__(c, api, uuid, "Connector current L2", "current_l2")

    @property
    def native_value(self):
        st = self._st
        return (
            float(st.current_phases[1])
            if st and st.current_phases and len(st.current_phases) >= 2
            else None
        )


class ConnCurrentL3(_ConnBase):
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    def __init__(self, c, api, uuid):
        super().__init__(c, api, uuid, "Connector current L3", "current_l3")

    @property
    def native_value(self):
        st = self._st
        return (
            float(st.current_phases[2])
            if st and st.current_phases and len(st.current_phases) >= 3
            else None
        )


class ConnEnergyImport(_ConnBase):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, c, api, uuid):
        super().__init__(c, api, uuid, "Connector energy import", "energy_import_kwh")

    @property
    def native_value(self):
        st = self._st
        return float(st.energy_import_kwh) if st and st.energy_import_kwh is not None else None
