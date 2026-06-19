# custom_components/smappee_ev/helpers.py

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from datetime import timedelta
from typing import Any, cast

from homeassistant.helpers.entity import DeviceInfo

from .const import CONFIGURATION_URL, DOMAIN, MANUFACTURER


def runtime_sites(sites: Any) -> dict[Any, Any]:
    """Return runtime sites only when they have the expected mapping shape."""
    return sites if isinstance(sites, dict) else {}


def runtime_value(container: object, key: str, default: Any = None) -> Any:
    """Read a runtime field from the current dict shape or a future dataclass."""
    if isinstance(container, Mapping):
        return container.get(key, default)
    return getattr(container, key, default)


def _runtime_get(container: object, key: str, default: Any = None) -> Any:
    """Read a runtime field from the current dict shape or a future dataclass."""
    return runtime_value(container, key, default)


def runtime_site(sites: Any, site_id: object) -> Any | None:
    """Return one runtime site, accepting temporary int/string site-id drift."""
    runtime = runtime_sites(sites)
    if site_id in runtime:
        return runtime[site_id]
    text_id = str(site_id)
    if text_id in runtime:
        return runtime[text_id]
    with suppress(TypeError, ValueError):
        int_id = int(text_id)
        if int_id in runtime:
            return runtime[int_id]
    return None


def runtime_mqtt_for_site(mqtt_by_site: object, site_id: object) -> object | None:
    """Return MQTT runtime for a site id, accepting temporary int/string key drift."""
    if not isinstance(mqtt_by_site, Mapping):
        return None
    if site_id in mqtt_by_site:
        return mqtt_by_site[site_id]
    text_id = str(site_id)
    if text_id in mqtt_by_site:
        return mqtt_by_site[text_id]
    with suppress(TypeError, ValueError):
        return mqtt_by_site.get(int(text_id))
    return None


def site_runtime_stations(site: object | None) -> dict[str, Any]:
    """Return station buckets from a runtime site."""
    stations = _runtime_get(site or {}, "stations", {})
    return stations if isinstance(stations, dict) else {}


def site_runtime_coordinator(site: object | None) -> Any:
    """Return the site-scoped coordinator from a runtime site."""
    return _runtime_get(site or {}, "site_coordinator")


def station_runtime_coordinator(station: object | None) -> Any:
    """Return a station coordinator from a runtime station bucket."""
    return _runtime_get(station or {}, "coordinator") or _runtime_get(
        station or {}, "station_coordinator"
    )


def station_runtime_client(station: object | None) -> Any:
    """Return the station command client from a runtime station bucket."""
    return _runtime_get(station or {}, "station_client")


def station_runtime_connector_clients(station: object | None) -> dict[str, Any]:
    """Return connector clients from current dict buckets or future dataclass buckets."""
    clients = _runtime_get(station or {}, "connector_clients", None)
    if isinstance(clients, dict):
        return clients

    connectors = _runtime_get(station or {}, "connectors", {})
    if not isinstance(connectors, dict):
        return {}
    resolved: dict[str, object] = {}
    for key, connector in connectors.items():
        client = _runtime_get(connector, "connector_client", None)
        if client is not None:
            resolved[str(key)] = client
    return resolved


def site_device_identifier(site_sid: int | str) -> tuple[str, str]:
    """Return the registry identifier for a site/gateway device."""
    return (DOMAIN, f"site:{site_sid}")


def station_device_identifier(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
) -> tuple[str, str]:
    """Return the registry identifier for a charging station device."""
    return (DOMAIN, f"station:{site_sid}:{control_sid}:{charging_station_serial}")


def led_device_identifier(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
    led_device_id: str,
) -> tuple[str, str]:
    """Return the registry identifier for a LED controller device."""
    return (DOMAIN, f"led:{site_sid}:{control_sid}:{charging_station_serial}:{led_device_id}")


def connector_device_identifier(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
    connector_key: str,
) -> tuple[str, str]:
    """Return the registry identifier for a connector device."""
    return (
        DOMAIN,
        f"connector:{site_sid}:{control_sid}:{charging_station_serial}:{connector_key}",
    )


def make_site_device_info(
    site_sid: int | str,
    site_name: str | None,
    gateway_serial: str | None = None,
    gateway_type: str | None = None,
) -> DeviceInfo:
    """Return Home Assistant device_info for a site/service location."""
    name = site_name or f"{MANUFACTURER} {site_sid}"
    model_parts = [part for part in (gateway_type, "Service Location") if part]
    return cast(
        DeviceInfo,
        {
            "identifiers": {site_device_identifier(site_sid)},
            "name": name,
            "manufacturer": MANUFACTURER,
            "configuration_url": CONFIGURATION_URL,
            "model": " / ".join(model_parts) if model_parts else "Service Location",
            **({"serial_number": gateway_serial} if gateway_serial else {}),
        },
    )


def make_station_device_info(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
    *,
    station_name: str | None = None,
    station_model: str | None = None,
    legacy_identifier: str | None = None,
) -> DeviceInfo:
    """Return Home Assistant device_info for a charging station."""
    identifiers = {station_device_identifier(site_sid, control_sid, charging_station_serial)}
    if legacy_identifier:
        identifiers.add((DOMAIN, legacy_identifier))
    return cast(
        DeviceInfo,
        {
            "identifiers": identifiers,
            "name": station_name or f"{MANUFACTURER} EV {charging_station_serial}",
            "manufacturer": MANUFACTURER,
            "configuration_url": CONFIGURATION_URL,
            "model": station_model or "EV Wall",
            "serial_number": charging_station_serial,
            "via_device": site_device_identifier(site_sid),
        },
    )


def make_led_device_info(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
    led_device_id: str,
    *,
    led_name: str | None = None,
) -> DeviceInfo:
    """Return Home Assistant device_info for a LED controller."""
    return cast(
        DeviceInfo,
        {
            "identifiers": {
                led_device_identifier(site_sid, control_sid, charging_station_serial, led_device_id)
            },
            "name": led_name or f"{MANUFACTURER} EV {charging_station_serial} LED controller",
            "manufacturer": MANUFACTURER,
            "configuration_url": CONFIGURATION_URL,
            "model": "LED Controller",
            "via_device": station_device_identifier(site_sid, control_sid, charging_station_serial),
        },
    )


def make_connector_device_info(
    site_sid: int | str,
    control_sid: int | str,
    charging_station_serial: str,
    connector_key: str,
    connector_label: str | None = None,
    station_name: str | None = None,
) -> DeviceInfo:
    """Return Home Assistant device_info for a connector."""
    label = connector_label or connector_key
    base = station_name or f"{MANUFACTURER} EV {charging_station_serial}"
    return cast(
        DeviceInfo,
        {
            "identifiers": {
                connector_device_identifier(
                    site_sid, control_sid, charging_station_serial, connector_key
                )
            },
            "name": f"{base} | Connector {label}",
            "manufacturer": MANUFACTURER,
            "configuration_url": CONFIGURATION_URL,
            "model": "Connector",
            "via_device": station_device_identifier(site_sid, control_sid, charging_station_serial),
        },
    )


def make_device_info(
    sid: int,
    serial: str,
    station_uuid: str,
    connector_label: str | None = None,
    *,
    scope: str = "station",
    site_name: str | None = None,
    gateway_serial: str | None = None,
    gateway_type: str | None = None,
    control_sid: int | str | None = None,
    charging_station_serial: str | None = None,
    station_name: str | None = None,
    station_model: str | None = None,
    led_device_id: str | None = None,
    led_name: str | None = None,
    connector_key: str | None = None,
) -> DeviceInfo:
    """Return a Home Assistant device_info dict for a station or connector."""
    if scope == "site":
        return make_site_device_info(sid, site_name, gateway_serial or serial, gateway_type)

    resolved_control_sid = control_sid or sid
    resolved_station_serial = charging_station_serial or serial
    legacy_identifier = f"{sid}:{serial}:{station_uuid}"

    if scope == "led" and led_device_id:
        return make_led_device_info(
            sid,
            resolved_control_sid,
            resolved_station_serial,
            led_device_id,
            led_name=led_name,
        )

    if scope == "connector":
        resolved_connector_key = connector_key or connector_label or "unknown"
        return make_connector_device_info(
            sid,
            resolved_control_sid,
            resolved_station_serial,
            resolved_connector_key,
            connector_label,
            station_name,
        )

    return make_station_device_info(
        sid,
        resolved_control_sid,
        resolved_station_serial,
        station_name=station_name,
        station_model=station_model,
        legacy_identifier=legacy_identifier,
    )


def make_unique_id(
    sid: int,
    serial: str,
    station_uuid: str,
    connector_uuid: str | None,
    metric: str,
) -> str:
    """
    Generate a globally unique ID for any entity.

    Args:
        sid: service location ID
        serial: station serial
        station_uuid: UUID of the station
        connector_uuid: UUID of the connector (None for station-wide entities)
        metric: suffix for the entity type, e.g. "mqtt_connected", "charging_mode"
    """
    if connector_uuid:
        return f"{sid}:{serial}:{station_uuid}:{connector_uuid}:{metric}"
    return f"{sid}:{serial}:{station_uuid}:{metric}"


# ----------------------------------------------------------------------------------
# Additional helpers to reduce duplication across entity platforms
# ----------------------------------------------------------------------------------


def station_serial(coord) -> str:
    """Return the station serial from a coordinator (fallback 'unknown')."""
    station_client = getattr(coord, "station_client", None)
    if station_client is None:
        return "unknown"
    return (
        getattr(station_client, "charging_station_serial", None)
        or getattr(station_client, "serial_id", None)
        or "unknown"
    )


def connector_state(coordinator, connector_uuid: str) -> Any | None:
    """Lookup a connector state object from coordinator data."""
    data = getattr(coordinator, "data", None)
    if not data:
        return None
    return (getattr(data, "connectors", None) or {}).get(connector_uuid)


def build_connector_label(api_client, connector_uuid: str) -> str:
    """Return a human friendly connector label (prefers numeric connector number)."""
    num = getattr(api_client, "connector_number", None)
    return f"Connector {num}" if num is not None else f"Connector {connector_uuid[-4:]}"


def build_connector_id(api_client, connector_uuid: str) -> str:
    """Return a human friendly connector label (prefers numeric connector number)."""
    num = getattr(api_client, "connector_number", None)
    return str(num) if num is not None else str(connector_uuid[-4:])


def update_total_increasing(last: float | None, candidate: float | None) -> float | None:
    """
    Enforce monotonic increasing semantics for total energy-like sensors.

    Rules:
      * If candidate is None -> keep last
      * If last exists and candidate < last or candidate == 0 -> keep last (guards resets)
      * Else accept candidate
    Returns the value to expose (which may be unchanged last).
    """
    if candidate is None:
        return last
    if last is not None and (candidate < last or candidate == 0):
        return last
    return candidate


def safe_sum(values) -> float | None:
    """
    Best effort sum of an iterable of numeric-like values, returning float or None.

    Accepts any list/tuple of values coercible to float. Returns None if empty or any
    element cannot be converted.
    """
    if not isinstance(values, list | tuple) or not values:  # type: ignore[arg-type]
        return None
    with suppress(TypeError, ValueError):
        return float(sum(float(v) for v in values))
    return None


__all__ = [
    "make_device_info",
    "make_site_device_info",
    "make_station_device_info",
    "make_led_device_info",
    "make_connector_device_info",
    "make_unique_id",
    "runtime_mqtt_for_site",
    "runtime_site",
    "runtime_sites",
    "runtime_value",
    "site_runtime_coordinator",
    "site_runtime_stations",
    "station_serial",
    "station_runtime_client",
    "station_runtime_connector_clients",
    "station_runtime_coordinator",
    "connector_state",
    "build_connector_label",
    "update_total_increasing",
    "safe_sum",
]


def format_as_hms(td: timedelta) -> str:
    """
    Format a timedelta object into a human-readable HH:MM:SS string.

    This function calculates the total hours, minutes, and seconds from a
    given timedelta object and returns them as a zero-padded string,
    even if the duration exceeds 24 hours.

    Args:
        td (timedelta): The duration object to format.

    Returns:
        str: The formatted duration in HH:MM:SS format.
    """
    total_seconds = int(td.total_seconds())

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    return f"{hours:02}:{minutes:02}:{seconds:02}"


def anonymize_uuid(uuid: str | None) -> str:
    """Mask a UUID while keeping it consistent for debugging."""
    if uuid is None:
        return "none"
    if not uuid or len(uuid) < 8:
        return "****"

    return f"{uuid[:4]}****{uuid[-4:]}"
