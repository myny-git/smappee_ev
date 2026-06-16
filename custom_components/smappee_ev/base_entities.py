from __future__ import annotations

from typing import Any, cast

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmappeeCoordinator, SmappeeSiteCoordinator, SmappeeStationCoordinator
from .device_handle import SmappeeDeviceHandle
from .helpers import build_connector_id, make_device_info, make_unique_id, station_serial

__all__ = [
    "SmappeeBaseEntity",
    "SmappeeSiteEntity",
    "SmappeeStationEntity",
    "SmappeeStationRestEntity",
    "SmappeeLedEntity",
    "SmappeeConnectorEntity",
    "SmappeeConnectorRestEntity",
    "SmappeeConnectorMqttEntity",
]


def _text_attr(obj: object, name: str) -> str | None:
    value = getattr(obj, name, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int):
        return str(value)
    return None


def _is_int_like(value: object) -> bool:
    try:
        int(cast(Any, value))
    except (TypeError, ValueError):
        return False
    return True


type SmappeeEntityCoordinator = SmappeeSiteCoordinator | SmappeeStationCoordinator


class SmappeeBaseEntity(CoordinatorEntity[SmappeeEntityCoordinator]):
    """Common base providing station/connector id storage and device_info."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmappeeEntityCoordinator,
        sid: int,
        station_uuid: str,
        unique_suffix: str = "entity",
        connector_uuid: str | None = None,
        connector_label: str | None = None,
        device_scope: str = "station",
        led_device_id: str | None = None,
        led_name: str | None = None,
    ) -> None:
        self._sid = sid
        self._station_uuid = station_uuid
        self._serial = station_serial(coordinator)
        self._connector_label = connector_label
        self._device_scope = device_scope
        self._led_device_id = led_device_id
        self._led_name = led_name
        self._connector_key = connector_uuid
        unique_suffix = unique_suffix or "entity"
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
        station_client = getattr(self.coordinator, "station_client", None)
        site_name = _text_attr(self.coordinator, "site_name")
        gateway_serial = _text_attr(self.coordinator, "gateway_serial")
        gateway_type = _text_attr(self.coordinator, "gateway_type")
        control_sid = _text_attr(station_client, "service_location_id")
        station_name = _text_attr(self.coordinator, "station_name")
        station_model = _text_attr(self.coordinator, "station_model")
        charging_station_serial = _text_attr(station_client, "charging_station_serial")
        if self._device_scope == "station" and not any(
            (
                site_name,
                gateway_serial,
                gateway_type,
                control_sid and control_sid != str(self._sid),
                station_name,
                station_model,
                charging_station_serial,
            )
        ):
            if self._connector_label is None:
                return make_device_info(self._sid, self._serial, self._station_uuid)
            return make_device_info(
                self._sid,
                self._serial,
                self._station_uuid,
                connector_label=self._connector_label,
            )
        return make_device_info(
            self._sid,
            self._serial,
            self._station_uuid,
            connector_label=self._connector_label,
            scope=self._device_scope,
            site_name=site_name,
            gateway_serial=gateway_serial,
            gateway_type=gateway_type,
            control_sid=control_sid,
            charging_station_serial=charging_station_serial,
            station_name=station_name,
            station_model=station_model,
            led_device_id=self._led_device_id,
            led_name=self._led_name,
            connector_key=self._connector_key,
        )

    @property
    def _coordinator_available(self) -> bool:
        """Return the base CoordinatorEntity availability state."""
        return super().available


class SmappeeStationEntity(SmappeeBaseEntity):
    """Base for station-scope entities (no connector)."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str,
        unique_suffix: str = "entity",
        name: str | None = None,
        *,
        device_scope: str = "station",
        led_device_id: str | None = None,
        led_name: str | None = None,
    ) -> None:
        super().__init__(
            coordinator,
            sid,
            station_uuid,
            unique_suffix=unique_suffix,
            device_scope=device_scope,
            led_device_id=led_device_id,
            led_name=led_name,
        )
        if name is not None:
            self._attr_name = name


class SmappeeSiteEntity(SmappeeBaseEntity):
    """Base for site-scope entities."""

    def __init__(
        self,
        coordinator: SmappeeEntityCoordinator,
        sid: int,
        unique_suffix: str = "entity",
    ) -> None:
        super().__init__(
            coordinator,
            sid,
            station_uuid=f"site-{sid}",
            unique_suffix=unique_suffix,
            device_scope="site",
        )


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


class SmappeeLedEntity(SmappeeStationRestEntity):
    """Base for LED-controller entities."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str,
        unique_suffix: str,
        led_device_id: str | None = None,
        led_name: str | None = None,
    ) -> None:
        super().__init__(
            coordinator,
            sid,
            station_uuid,
            unique_suffix=unique_suffix,
            device_scope="led",
            led_device_id=led_device_id,
            led_name=led_name,
        )


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
        if not _is_int_like(sid) and _is_int_like(api):
            old_sid = int(cast(int | str, api))
            old_station_uuid = str(sid)
            old_connector_uuid = str(station_uuid)
            old_unique_suffix = str(connector_uuid)
            old_name = str(unique_suffix) if unique_suffix is not None else name
            api = old_sid
            sid = old_sid
            station_uuid = old_station_uuid
            connector_uuid = old_connector_uuid
            unique_suffix = old_unique_suffix
            name = old_name
        self._connector_uuid = connector_uuid
        self._api = cast(SmappeeDeviceHandle, api)
        self._unique_suffix = unique_suffix
        sid_int = int(sid)
        connector_label = build_connector_id(self._api, connector_uuid)
        super().__init__(
            coordinator,
            sid_int,
            station_uuid,
            unique_suffix=unique_suffix,
            connector_uuid=connector_uuid,
            connector_label=connector_label,
            device_scope="connector",
        )
        if name is not None:
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


class SmappeeConnectorRestEntity(SmappeeConnectorEntity):
    """Explicit base for connector entities that require REST/Dashboard reachability."""


class SmappeeConnectorMqttEntity(SmappeeConnectorEntity):
    """Base for connector entities whose values are primarily MQTT-backed."""

    @property
    def available(self) -> bool:
        """Return True when coordinator data exists and MQTT is not known down."""
        if not self._coordinator_available:
            return False
        conn = self._conn_state
        if conn is None:
            return False
        data = getattr(self.coordinator, "data", None)
        station = getattr(data, "station", None) if data else None
        mqtt_connected = getattr(station, "mqtt_connected", None)
        return mqtt_connected is not False
