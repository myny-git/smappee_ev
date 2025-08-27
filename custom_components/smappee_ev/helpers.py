# custom_components/smappee_ev/helpers.py

from __future__ import annotations

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
