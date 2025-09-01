# custom_components/smappee_ev/helpers.py

from __future__ import annotations

from typing import Any

from .const import DOMAIN


def make_device_info(
    sid: int,
    serial: str,
    station_uuid: str,
    name: str | None = None,
) -> dict:
    """Return a Home Assistant device_info dict for a given station."""
    return {
        "identifiers": {(DOMAIN, f"{sid}:{serial}:{station_uuid}")},
        "name": name or f"Smappee EV {serial}",
        "manufacturer": "Smappee",
    }


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
    return getattr(getattr(coord, "station_client", None), "serial_id", "unknown")


def station_name(coord) -> str | None:
    """Return the station's display name if available."""
    data = getattr(coord, "data", None)
    st = getattr(data, "station", None) if data else None
    return getattr(st, "name", None)


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
    try:
        return float(sum(float(v) for v in values))
    except (TypeError, ValueError):  # any non-numeric
        return None


__all__ = [
    "make_device_info",
    "make_unique_id",
    "station_serial",
    "station_name",
    "connector_state",
    "build_connector_label",
    "update_total_increasing",
    "safe_sum",
]
