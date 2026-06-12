from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SmappeeCoordinator
from .helpers import make_device_info, make_unique_id, station_serial

__all__ = [
    "SmappeeBaseEntity",
    "SmappeeStationEntity",
    "SmappeeStationRestEntity",
    "SmappeeConnectorEntity",
]


class SmappeeBaseEntity(CoordinatorEntity[SmappeeCoordinator]):
    """Common base providing station/connector id storage and device_info."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmappeeCoordinator, sid: int, station_uuid: str) -> None:
        super().__init__(coordinator)
        self._sid = sid
        self._station_uuid = station_uuid
        self._serial = station_serial(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        return make_device_info(self._sid, self._serial, self._station_uuid)


class SmappeeStationEntity(SmappeeBaseEntity):
    """Base for station-scope entities (no connector)."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str,
        unique_suffix: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, sid, station_uuid)
        self._attr_unique_id = make_unique_id(sid, self._serial, station_uuid, None, unique_suffix)
        self._attr_name = name


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
        sid: int,
        station_uuid: str,
        connector_uuid: str,
        unique_suffix: str,
        name: str,
    ) -> None:
        super().__init__(coordinator, sid, station_uuid)
        self._connector_uuid = connector_uuid
        self._attr_unique_id = make_unique_id(
            sid, self._serial, station_uuid, connector_uuid, unique_suffix
        )
        self._attr_name = name

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
