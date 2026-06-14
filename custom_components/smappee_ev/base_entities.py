from __future__ import annotations

from typing import Any, cast

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SmappeeCoordinator
from .device_handle import SmappeeDeviceHandle
from .helpers import build_connector_id, make_device_info, make_unique_id, station_serial

__all__ = [
    "SmappeeBaseEntity",
    "SmappeeStationEntity",
    "SmappeeStationRestEntity",
    "SmappeeConnectorEntity",
]


class SmappeeBaseEntity(CoordinatorEntity[SmappeeCoordinator]):
    """Common base providing station/connector id storage and device_info."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str,
        connector_label: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._sid = sid
        self._station_uuid = station_uuid
        self._serial = station_serial(coordinator)
        self._connector_label = connector_label

    @property
    def device_info(self) -> DeviceInfo:
        if self._connector_label is None:
            return make_device_info(self._sid, self._serial, self._station_uuid)
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
        api: SmappeeDeviceHandle | int,
        sid: int | str,
        station_uuid: str,
        connector_uuid: str,
        unique_suffix: str,
        name: str | None = None,
    ) -> None:
        if name is None:
            real_api = None
            real_sid = int(cast(int, api))
            real_station_uuid = str(sid)
            real_connector_uuid = station_uuid
            real_unique_suffix = connector_uuid
            real_name = unique_suffix
            connector_label = real_connector_uuid
        else:
            real_api = cast(SmappeeDeviceHandle, api)
            real_sid = int(sid)
            real_station_uuid = station_uuid
            real_connector_uuid = connector_uuid
            real_unique_suffix = unique_suffix
            real_name = name
            connector_label = build_connector_id(
                cast(SmappeeDeviceHandle, real_api), real_connector_uuid
            )

        super().__init__(coordinator, real_sid, real_station_uuid, connector_label=connector_label)
        self._connector_uuid = real_connector_uuid
        self._attr_unique_id = make_unique_id(
            real_sid,
            self._serial,
            real_station_uuid,
            real_connector_uuid,
            real_unique_suffix,
        )
        self._attr_name = real_name

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

