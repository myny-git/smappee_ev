"""Topology parsing and runtime-builder helpers for Smappee EV setup."""

from __future__ import annotations

from contextlib import suppress
import logging
import re
from typing import Any

from .api.device_handle import SmappeeDeviceHandle
from .models.runtime_data import SmappeeConnectorRuntime, SmappeeLedRuntime, SmappeeStationRuntime
from .models.state import DashboardObject, DashboardObjectList

_LOGGER = logging.getLogger(__name__)


def _is_station(dev: dict[str, Any]) -> bool:
    """True if device is a CHARGINGSTATION smartdevice."""
    t = dev.get("type")
    if isinstance(t, dict):
        return (t.get("category") or "").upper() == "CHARGINGSTATION"
    return (dev.get("type") or "").upper() == "CHARGINGSTATION"


def _is_connector(dev: dict[str, Any]) -> bool:
    """True if device is a CARCHARGER smartdevice."""
    if isinstance(dev.get("carCharger"), dict):
        return True
    t = dev.get("type")
    if isinstance(t, dict):
        return (t.get("category") or "").upper() == "CARCHARGER"
    return (dev.get("type") or "").upper() == "CARCHARGER"


def _safe_str(value: object) -> str | None:
    """Convert to stripped string or None if not possible."""
    if value is None:
        return None
    with suppress(TypeError, ValueError):
        s = str(value).strip()
        if s.lower() in {"none", "null"}:
            return None
        return s or None
    return None


def _find_in(dev: dict, *keys: str) -> str | None:
    """Try to discover a 'serialNumber' (or similar) inside a smartdevice."""
    for k in keys:
        if k in dev and _safe_str(dev[k]):
            return _safe_str(dev[k])
    for bag in ("configurationProperties", "properties"):
        for prop in dev.get(bag, []) or []:
            spec = prop.get("spec") or {}
            name = (spec.get("name") or "").lower()
            if "serial" in name:
                v = prop.get("value")
                if isinstance(v, dict):
                    v = v.get("value")
                if _safe_str(v):
                    return _safe_str(v)
    return None


def _uuid_from_dashboard_channel(smart_device: DashboardObject) -> str | None:
    car_charger = smart_device.get("carCharger")
    if not isinstance(car_charger, dict):
        return None
    channel = car_charger.get("chargingStateUpdateChannel")
    if isinstance(channel, dict):
        channel_name = _safe_str(channel.get("name"))
    else:
        channel_name = _safe_str(channel)
    if not channel_name:
        return None
    marker = "/devices/"
    if marker not in channel_name:
        return None
    return channel_name.split(marker, 1)[1].split("/", 1)[0] or None


def _device_uuid(dev: DashboardObject) -> str | None:
    return (
        _safe_str(dev.get("uuid"))
        or _safe_str(dev.get("smartDeviceUuid"))
        or _uuid_from_dashboard_channel(dev)
    )


def _connector_uuid(dev: DashboardObject) -> str | None:
    return _uuid_from_dashboard_channel(dev) or _device_uuid(dev)


def _station_serial(dev: DashboardObject) -> str | None:
    return _find_in(dev, "serialNumber", "serial") or _device_uuid(dev)


def _split_devices(devices: list[dict]) -> tuple[list[dict], list[dict]]:
    stations = [d for d in (devices or []) if _is_station(d)]
    cars = [d for d in (devices or []) if _is_connector(d)]
    return stations, cars


def _charging_station_from_service_location(item: DashboardObject) -> DashboardObject:
    charging_station = item.get("chargingStation")
    if not isinstance(charging_station, dict):
        charging_station = item.get("chargingstation")
    if isinstance(charging_station, dict):
        return charging_station

    charging_stations = item.get("chargingStations") or item.get("chargingstations")
    if isinstance(charging_stations, list):
        return next((station for station in charging_stations if isinstance(station, dict)), {})
    return {}


def _normalize_dashboard_service_location(
    item: DashboardObject, *, allow_non_charging_function_type: bool = False
) -> DashboardObject | None:
    function_type = _safe_str(item.get("functionType"))
    charging_station = _charging_station_from_service_location(item)
    if (
        function_type
        and function_type.upper() != "CHARGINGSTATION"
        and not allow_non_charging_function_type
        and not charging_station
    ):
        return None

    sid = item.get("serviceLocationId") or item.get("id") or item.get("locationId")
    serial = (
        _safe_str(charging_station.get("serialNumber"))
        or _safe_str(charging_station.get("serial"))
        or _safe_str(item.get("deviceSerialNumber"))
        or _safe_str(item.get("serialNumber"))
    )
    if sid is None:
        return None

    suuid = item.get("serviceLocationUuid") or item.get("uuid")
    return {
        "serviceLocationId": sid,
        "serviceLocationUuid": suuid,
        "deviceSerialNumber": serial,
        "chargingStation": charging_station,
        "functionType": function_type,
        "name": item.get("name"),
    }


def _connector_position_from_measurement(measurement: dict[str, Any]) -> int | None:
    for key in ("position", "connectorNumber"):
        value = measurement.get(key)
        if value is not None:
            with suppress(TypeError, ValueError):
                return int(value)
    name = str(measurement.get("name") or "")
    match = re.search(r"(?:^|\s-\s|\s)(\d+)\s*$", name)
    if not match:
        return None
    with suppress(TypeError, ValueError):
        return int(match.group(1))
    return None


def _fallback_highlevel_connector_mapping(
    station_serial: str | None,
    highlevel_configs: dict[int, dict[str, Any]],
) -> dict[str, dict]:
    """Build measurement-only connector mapping from APPLIANCE/CAR_CHARGER entries."""
    if not station_serial:
        return {}

    connectors: dict[str, dict] = {}
    for cfg in highlevel_configs.values():
        for measurement in cfg.get("measurements") or []:
            if not isinstance(measurement, dict):
                continue
            if str(measurement.get("type") or "").upper() != "APPLIANCE":
                continue
            appliance = measurement.get("appliance")
            appliance_data = appliance if isinstance(appliance, dict) else {}
            appliance_type = appliance_data.get("type") or measurement.get("category")
            if str(appliance_type or "").upper() != "CAR_CHARGER":
                continue
            connector_uuid = (
                _safe_str(measurement.get("uuid"))
                or _safe_str(measurement.get("smartDeviceUuid"))
                or _safe_str(measurement.get("deviceUuid"))
                or _safe_str(appliance_data.get("uuid"))
                or _safe_str(appliance_data.get("smartDeviceUuid"))
                or _safe_str(appliance_data.get("deviceUuid"))
            )
            if not connector_uuid:
                continue
            connectors.setdefault(
                connector_uuid,
                {
                    "id": connector_uuid,
                    "smart_device_uuid": connector_uuid,
                    "position": _connector_position_from_measurement(measurement) or 1,
                    "station_serial": station_serial,
                },
            )
    return {station_serial: {"connectors": connectors}} if connectors else {}


def _normalize_connector_mapping_station_keys(
    mapping: dict[Any, dict], fallback_station_serial: str | None
) -> dict[str, dict]:
    """Move connector buckets with missing station keys under a usable station serial."""
    normalized: dict[str, dict] = {}
    orphan_connectors: dict[str, dict] = {}
    orphan_metadata: dict[str, object] = {}

    for station_serial, bucket in mapping.items():
        if not isinstance(bucket, dict):
            continue
        connectors = bucket.get("connectors") or {}
        metadata = {key: value for key, value in bucket.items() if key != "connectors"}
        key = _safe_str(station_serial)
        if not key:
            orphan_connectors.update(connectors)
            for meta_key, meta_value in metadata.items():
                orphan_metadata.setdefault(meta_key, meta_value)
            continue
        target = normalized.setdefault(key, {"connectors": {}})
        target["connectors"].update(connectors)
        for meta_key, meta_value in metadata.items():
            target.setdefault(meta_key, meta_value)

    if orphan_connectors:
        fallback_key = _safe_str(fallback_station_serial) or next(iter(normalized), None)
        if fallback_key:
            target = normalized.setdefault(fallback_key, {"connectors": {}})
            target["connectors"].update(orphan_connectors)
            for meta_key, meta_value in orphan_metadata.items():
                target.setdefault(meta_key, meta_value)

    return normalized


def _station_devices_from_connector_mapping(mapping: dict[str, dict]) -> DashboardObjectList:
    return [
        {
            "uuid": station_serial,
            "id": station_serial,
            "serialNumber": station_serial,
            "type": "CHARGINGSTATION",
        }
        for station_serial, bucket in mapping.items()
        if _safe_str(station_serial) and (bucket.get("connectors") or {})
    ]


def _derive_service_serial(sl: DashboardObject, station_devs: list[dict]) -> str | None:
    """Return the best serial to use for site-level handles and MQTT."""
    serial = _safe_str(sl.get("deviceSerialNumber")) or _safe_str(sl.get("serialNumber"))
    if serial:
        return serial

    charging_station = sl.get("chargingStation")
    if isinstance(charging_station, dict):
        serial = _safe_str(charging_station.get("serialNumber")) or _safe_str(
            charging_station.get("serial")
        )
        if serial:
            return serial

    for station in station_devs:
        serial = _find_in(station, "serialNumber", "serial", "deviceSerialNumber")
        if serial:
            return serial

    if len(station_devs) == 1:
        return _device_uuid(station_devs[0])
    return None


def _make_led_runtimes(led_devices: object) -> dict[str, SmappeeLedRuntime]:
    """Convert Dashboard LED metadata into typed runtime containers."""
    if not isinstance(led_devices, dict):
        return {}

    runtimes: dict[str, SmappeeLedRuntime] = {}
    for led_key, led in led_devices.items():
        key = str(led_key)
        led_id = led.get("id", key) if isinstance(led, dict) else key
        led_uuid = led.get("uuid") if isinstance(led, dict) else None
        led_name = led.get("name") if isinstance(led, dict) else None
        runtimes[key] = SmappeeLedRuntime(
            led_key=key,
            led_device_id=led_id,
            led_device_uuid=led_uuid,
            led_device_name=led_name,
        )
    return runtimes


def _add_connector_runtime(
    station: SmappeeStationRuntime,
    *,
    connector_key: str,
    connector_uuid: str | None,
    connector_position: int | None,
    connector_client: SmappeeDeviceHandle,
) -> None:
    """Attach a connector client to a typed station runtime."""
    station.connectors[connector_key] = SmappeeConnectorRuntime(
        connector_key=connector_key,
        connector_uuid=connector_uuid,
        connector_position=connector_position,
        connector_client=connector_client,
    )


def _make_station_clients(
    serial_str,
    sid: int,
    station_devs: list[dict],
    *,
    site_location_id: int | None = None,
    site_name: str | None = None,
    gateway_serial: str | None = None,
    gateway_type: str | None = None,
    control_name: str | None = None,
    control_uuid: str | None = None,
    control_function_type: str | None = None,
    station_metadata: dict[str, dict] | None = None,
) -> dict[str, SmappeeStationRuntime]:
    stations: dict[str, SmappeeStationRuntime] = {}
    station_metadata = station_metadata or {}
    for sd in station_devs:
        st_uuid = _device_uuid(sd)
        st_id = _safe_str(sd.get("id")) or st_uuid
        if not st_uuid or not st_id:
            continue

        st_serial = _station_serial(sd) or st_uuid
        metadata = station_metadata.get(st_serial, {})
        resolved_site_location_id = site_location_id if site_location_id is not None else sid
        st_client = SmappeeDeviceHandle(
            serial_str,
            st_uuid,
            st_id,
            sid,
            is_station=True,
            charging_station_serial=st_serial,
            site_location_id=resolved_site_location_id,
            charging_station_model=metadata.get("station_model"),
        )
        stations[st_uuid] = SmappeeStationRuntime(
            site_location_id=resolved_site_location_id,
            control_location_id=sid,
            site_name=site_name,
            gateway_serial=gateway_serial,
            gateway_type=gateway_type,
            control_name=control_name,
            control_uuid=control_uuid,
            control_function_type=control_function_type,
            station_name=metadata.get("station_name") or control_name,
            charging_station_serial=st_serial,
            charging_station_model=metadata.get("station_model"),
            station_client=st_client,
            station_coordinator=None,
            led_devices=_make_led_runtimes(metadata.get("led_devices", {})),
        )
    return stations


def _make_station_clients_with_mapping_fallback(
    serial_str: str,
    sid: int,
    station_devs: list[dict],
    station_serial_to_connectors: dict[str, dict],
    has_connector_mapping: bool,
    **metadata: Any,
) -> dict[str, SmappeeStationRuntime]:
    stations = _make_station_clients(serial_str, sid, station_devs, **metadata)
    if stations or not has_connector_mapping:
        return stations

    mapping_station_devs = _station_devices_from_connector_mapping(station_serial_to_connectors)
    if not mapping_station_devs:
        return stations

    _LOGGER.debug(
        "Connector mapping at %s yielded no usable station smartdevices; "
        "using %d station serials from Dashboard mapping",
        sid,
        len(mapping_station_devs),
    )
    return _make_station_clients(serial_str, sid, mapping_station_devs, **metadata)


def _assign_connectors(
    stations: dict[str, SmappeeStationRuntime], car_devs, mapping, serial_str, sid: int
) -> None:
    for bucket in stations.values():
        st_serial = bucket.charging_station_serial
        if not st_serial or st_serial not in mapping:
            continue
        for cuuid, info in mapping[st_serial]["connectors"].items():
            src = next((d for d in car_devs if _connector_uuid(d) == cuuid), None)
            if src is None and isinstance(info, dict):
                module_device = info.get("smart_device")
                if isinstance(module_device, dict):
                    src = module_device
            if not src:
                continue
            cid = _safe_str(src.get("id")) or info.get("id") or cuuid
            site_location_id = bucket.site_location_id
            position = (
                info.get("position") or src.get("connectorNumber") or src.get("position") or 1
            )
            connector_client = SmappeeDeviceHandle(
                serial_str,
                cuuid,
                cid,
                sid,
                connector_number=position,
                charging_station_serial=st_serial,
                site_location_id=site_location_id,
            )
            _add_connector_runtime(
                bucket,
                connector_key=cuuid,
                connector_uuid=cuuid,
                connector_position=position,
                connector_client=connector_client,
            )


def _fallback_assign(stations: dict[str, SmappeeStationRuntime], car_devs, serial_str, sid: int):
    total_assigned = sum(len(bucket.connectors) for bucket in stations.values())
    if total_assigned > 0:
        return
    first_uuid = next(iter(stations.keys()), None)
    if not first_uuid:
        return
    first_bucket = stations.get(first_uuid)
    if first_bucket is None:
        return
    st_serial = first_bucket.charging_station_serial
    _LOGGER.warning(
        "Could not map connectors to stations at %s; assigning all to first station", sid
    )
    for dev in car_devs:
        cuuid = _connector_uuid(dev)
        cid = _safe_str(dev.get("id")) or cuuid
        if not cuuid or not cid:
            continue
        site_location_id = first_bucket.site_location_id
        position = dev.get("connectorNumber") or dev.get("position") or 1
        connector_client = SmappeeDeviceHandle(
            serial_str,
            cuuid,
            cid,
            sid,
            connector_number=position,
            charging_station_serial=st_serial,
            site_location_id=site_location_id,
        )
        _add_connector_runtime(
            first_bucket,
            connector_key=cuuid,
            connector_uuid=cuuid,
            connector_position=position,
            connector_client=connector_client,
        )
