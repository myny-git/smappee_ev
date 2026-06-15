from __future__ import annotations

from typing import Any, cast

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .device_handle import SmappeeDeviceHandle
from .helpers import build_connector_id, make_device_info, make_unique_id, station_serial

__all__ = [
    "SmappeeBaseEntity",
    "SmappeeStationEntity",
    "SmappeeStationRestEntity",
    "SmappeeConnectorEntity",
    "SmappeeConnectorRestEntity",
    "SmappeeConnectorMqttEntity",
]


class SmappeeBaseEntity(CoordinatorEntity[SmappeeCoordinator]):
    """Common base providing station/connector id storage and device_info."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str,
        unique_suffix: str,
        connector_uuid: str | None = None,
        connector_label: str | None = None,
    ) -> None:
        self._sid = sid
        self._station_uuid = station_uuid
        self._serial = station_serial(coordinator)
        self._connector_label = connector_label
        self.internal_integration_suggested_object_id = (
            f"{DOMAIN}_{self._serial}_{unique_suffix.split(':')[-1]}"
            f"{'_' + connector_label if connector_label else ''}"
        )
        self._attr_unique_id = make_unique_id(
            sid,
            self._serial,
            station_uuid,
            connector_uuid,
            unique_suffix,
        )
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        return make_device_info(
            self._sid, self._serial, self._station_uuid, connector_label=self._connector_label
        )


class SmappeeStationEntity(SmappeeBaseEntity):
    """Base for station-scope entities (no connector)."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator, sid, station_uuid, unique_suffix=unique_suffix)


class SmappeeStationRestEntity(SmappeeStationEntity):
    """Base for station-scope entities that depend on REST reachability."""

    @property
    def available(self) -> bool:
        """Return True when coordinator and station REST reachability are available."""
        if not super().available:
            return False
        data = getattr(self.coordinator, "data", None)
        if data is None:
            return False
        station = getattr(data, "station", None)
        if station is None:
            return False
        return bool(getattr(station, "api_available", True))


class SmappeeConnectorEntity(SmappeeBaseEntity):
    """Base for connector-scope entities."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api: SmappeeDeviceHandle | int,
        sid: int | str,
        station_uuid: str,
        connector_uuid: str,
        unique_suffix: str,
    ) -> None:
        self._connector_uuid = connector_uuid
        self._api = cast(SmappeeDeviceHandle, api)
        self._sid = int(sid)
        self._station_uuid = station_uuid
        self._connector_uuid = connector_uuid
        self._unique_suffix = unique_suffix
        connector_label = build_connector_id(
            cast(SmappeeDeviceHandle, api), connector_uuid
        )
        super().__init__(coordinator, self._sid, station_uuid, unique_suffix=unique_suffix, connector_uuid=connector_uuid, connector_label=connector_label)

    # Convenience accessors
    @property
    def connector_uuid(self) -> str:
        return self._connector_uuid

    @property
    def available(self) -> bool:
        """Return True when coordinator and connector REST reachability are available."""
        if not super().available:
            return False
        conn = self._conn_state
        if conn is None:
            return False
        return bool(getattr(conn, "api_available", True))

    @property
    def _conn_state(self) -> Any | None:
        data = getattr(self.coordinator, "data", None)
        if not data:
            return None
        return (getattr(data, "connectors", None) or {}).get(self._connector_uuid)


class SmappeeConnectorRestEntity(SmappeeConnectorEntity):
    """Explicit base for connector entities that require REST/Dashboard reachability."""


class SmappeeConnectorMqttEntity(SmappeeConnectorEntity):
    """Base for connector entities whose values are primarily MQTT-backed."""

    @property
    def available(self) -> bool:
        """Return True when coordinator data exists and MQTT is not known down."""
        if not SmappeeBaseEntity.available.fget(self):
            return False
        conn = self._conn_state
        if conn is None:
            return False
        data = getattr(self.coordinator, "data", None)
        station = getattr(data, "station", None) if data else None
        mqtt_connected = getattr(station, "mqtt_connected", None)
        return mqtt_connected is not False
