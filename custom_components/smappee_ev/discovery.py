"""Discovery/topology helpers for Smappee Dashboard API payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SmappeeLocationTopology:
    """Site-first view of a Dashboard service-location topology."""

    site_location_id: int
    site_location_uuid: str | None
    site_name: str
    site_function_type: str | None

    control_location_id: int
    control_location_uuid: str | None
    control_name: str
    control_function_type: str | None

    measurement_location_ids: list[int]

    charging_station_serial: str | None

    site_gateway_serial: str | None
    site_gateway_type: str | None
    control_gateway_serial: str | None
    control_gateway_type: str | None

    write_access: bool


@dataclass(slots=True)
class MqttChannelSpec:
    """MQTT channel extracted from Dashboard highlevelconfiguration."""

    service_location_id: int
    role: str
    metric: str
    topic: str
    username: str | None
    password: str | None
    aspect_paths: list[dict[str, Any]]


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    return text


def _location_id(location: dict[str, Any]) -> int | None:
    for key in ("id", "serviceLocationId", "locationId"):
        value = location.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _charging_station(location: dict[str, Any]) -> dict[str, Any]:
    station = location.get("chargingStation") or location.get("chargingstation")
    if isinstance(station, dict):
        return station
    stations = location.get("chargingStations") or location.get("chargingstations")
    if isinstance(stations, list):
        for item in stations:
            if isinstance(item, dict):
                return item
    return {}


def _gateway(location: dict[str, Any]) -> dict[str, Any]:
    for key in ("gateway", "gatewayDevice", "device"):
        value = location.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _gateway_serial(location: dict[str, Any]) -> str | None:
    gateway = _gateway(location)
    for source in (gateway, location):
        for key in ("serialNumber", "serial", "deviceSerialNumber"):
            serial = _safe_str(source.get(key))
            if serial:
                return serial
    return None


def _gateway_type(location: dict[str, Any]) -> str | None:
    gateway = _gateway(location)
    for source in (gateway, location):
        value = _safe_str(source.get("type")) or _safe_str(source.get("deviceType"))
        if value:
            return value
    return None


def _write_access(control: dict[str, Any], site: dict[str, Any]) -> bool:
    for location in (control, site):
        for key in ("writeAccess", "canWrite", "writable"):
            value = location.get(key)
            if isinstance(value, bool):
                return value
    return False


def build_topologies_from_full_details(
    service_locations: list[dict[str, Any]],
) -> list[SmappeeLocationTopology]:
    """Build site-first charger topologies from fullDetails service locations."""
    locations_by_id: dict[int, dict[str, Any]] = {}
    for location in service_locations or []:
        if not isinstance(location, dict):
            continue
        sid = _location_id(location)
        if sid is not None:
            locations_by_id[sid] = location

    topologies: list[SmappeeLocationTopology] = []
    for control_id, control in locations_by_id.items():
        station = _charging_station(control)
        if not station:
            continue

        parent_id = control.get("parentId")
        try:
            parent_id_int = int(parent_id) if parent_id is not None else None
        except (TypeError, ValueError):
            parent_id_int = None

        site_id = parent_id_int if parent_id_int in locations_by_id else control_id
        site = locations_by_id.get(site_id, control)

        measurement_ids = [site_id]
        if control_id != site_id:
            measurement_ids.append(control_id)

        topologies.append(
            SmappeeLocationTopology(
                site_location_id=site_id,
                site_location_uuid=_safe_str(site.get("serviceLocationUuid") or site.get("uuid")),
                site_name=_safe_str(site.get("name")) or f"Smappee {site_id}",
                site_function_type=_safe_str(site.get("functionType")),
                control_location_id=control_id,
                control_location_uuid=_safe_str(
                    control.get("serviceLocationUuid") or control.get("uuid")
                ),
                control_name=_safe_str(control.get("name")) or f"Smappee {control_id}",
                control_function_type=_safe_str(control.get("functionType")),
                measurement_location_ids=measurement_ids,
                charging_station_serial=_safe_str(station.get("serialNumber"))
                or _safe_str(station.get("serial")),
                site_gateway_serial=_gateway_serial(site),
                site_gateway_type=_gateway_type(site),
                control_gateway_serial=_gateway_serial(control),
                control_gateway_type=_gateway_type(control),
                write_access=_write_access(control, site),
            )
        )

    return topologies


def _iter_mqtt_channels(channels: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(channels, dict):
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for metric, channel in channels.items():
        if not isinstance(channel, dict):
            continue
        if str(channel.get("protocol") or "").upper() != "MQTT":
            continue
        topic = _safe_str(channel.get("name"))
        if not topic:
            continue
        out.append((str(metric), channel))
    return out


def _measurement_role(measurement: dict[str, Any]) -> str | None:
    mtype = str(measurement.get("type") or "").upper()
    if mtype == "GRID":
        return "grid"
    if mtype == "PRODUCTION":
        return "production"
    appliance = measurement.get("appliance")
    appliance_type = (
        appliance.get("type") if isinstance(appliance, dict) else measurement.get("category")
    )
    if mtype == "APPLIANCE" and str(appliance_type or "").upper() == "CAR_CHARGER":
        return "car_charger"
    return None


def parse_mqtt_channel_specs_from_highlevel(
    service_location_id: int,
    config: dict[str, Any] | None,
) -> list[MqttChannelSpec]:
    """Extract Dashboard MQTT channel specs from one highlevelconfiguration."""
    if not isinstance(config, dict):
        return []

    specs: list[MqttChannelSpec] = []
    seen: set[tuple[Any, ...]] = set()

    def add_spec(role: str, metric: str, channel: dict[str, Any]) -> None:
        if str(channel.get("protocol") or "").upper() != "MQTT":
            return
        topic = _safe_str(channel.get("name"))
        if not topic:
            return
        aspect_paths = [
            dict(item) for item in channel.get("aspectPaths") or [] if isinstance(item, dict)
        ]
        key = (
            service_location_id,
            role,
            metric,
            topic,
            tuple(str(item.get("path") or "") for item in aspect_paths),
        )
        if key in seen:
            return
        seen.add(key)
        specs.append(
            MqttChannelSpec(
                service_location_id=service_location_id,
                role=role,
                metric=metric,
                topic=topic,
                username=_safe_str(channel.get("userName")),
                password=_safe_str(channel.get("password")),
                aspect_paths=aspect_paths,
            )
        )

    for measurement in config.get("measurements") or []:
        if not isinstance(measurement, dict):
            continue
        role = _measurement_role(measurement)
        if not role:
            continue
        for metric, channel in _iter_mqtt_channels(measurement.get("updateChannels")):
            add_spec(role, metric, channel)
        for actual in measurement.get("actuals") or []:
            if not isinstance(actual, dict):
                continue
            for metric, channel in _iter_mqtt_channels(actual.get("updateChannels")):
                add_spec(role, metric, channel)

    update_spec_roles = {
        "consumption": "consumption",
        "production": "production_total",
        "alwaysOn": "always_on",
    }
    update_specs = config.get("updateSpecs")
    if isinstance(update_specs, dict):
        for key, role in update_spec_roles.items():
            update_spec = update_specs.get(key)
            if not isinstance(update_spec, dict):
                continue
            update_channel = update_spec.get("channel")
            if isinstance(update_channel, dict):
                add_spec(role, key, update_channel)

    return specs


def unique_mqtt_channel_specs(specs: list[MqttChannelSpec]) -> list[MqttChannelSpec]:
    """Return one MQTT spec per topic, preserving first-seen credentials."""
    out: list[MqttChannelSpec] = []
    seen_topics: set[str] = set()
    for spec in specs:
        if spec.topic in seen_topics:
            continue
        seen_topics.add(spec.topic)
        out.append(spec)
    return out
