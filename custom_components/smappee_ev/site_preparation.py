"""Legacy service-location preparation helpers for Smappee EV setup."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import ClientSession
from homeassistant.core import HomeAssistant

from .api.dashboard_client import SmappeeDashboardClient
from .dashboard_discovery import (
    _dashboard_client_configured,
    _dashboard_fetch_devices,
    _fallback_dashboard_connector_mapping,
    _fetch_dashboard_connector_mapping,
)
from .models.runtime_data import MqttRuntimeValue, SmappeeEvConfigEntry, SmappeeStationRuntime
from .mqtt_setup import _setup_mqtt
from .runtime_assembly import _create_coordinators, _log_station_runtime_shape
from .topology import (
    _assign_connectors,
    _connector_uuid,
    _derive_service_serial,
    _fallback_assign,
    _make_station_clients,
    _normalize_connector_mapping_station_keys,
    _safe_str,
    _split_devices,
    _station_devices_from_connector_mapping,
    _station_serial,
)

_LOGGER = logging.getLogger(__name__)


async def _prepare_site(
    hass: HomeAssistant,
    session: ClientSession,
    sl: dict,
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
    dashboard_client: SmappeeDashboardClient | None = None,
) -> tuple[dict[str, SmappeeStationRuntime] | None, MqttRuntimeValue]:
    """Build coordinators, station/connector clients and MQTT for one service location."""
    try:
        return await _async_prepare_site(
            hass,
            session,
            sl,
            update_interval,
            client_id_prefix,
            config_entry=config_entry,
            dashboard_client=dashboard_client,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception("Failed to prepare service location %s", sl.get("serviceLocationId"))
        return None, None


async def _async_prepare_site(  # noqa: C901 - moved as-is from __init__.py.
    hass: HomeAssistant,
    session: ClientSession,
    sl: dict,
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
    dashboard_client: SmappeeDashboardClient | None = None,
) -> tuple[dict[str, SmappeeStationRuntime] | None, MqttRuntimeValue]:
    """Build coordinators, station/connector clients and MQTT for one service location."""
    sid = sl["serviceLocationId"]
    suuid = sl.get("serviceLocationUuid")

    devices = await _dashboard_fetch_devices(dashboard_client, sid)
    if devices is None:
        return None, None

    station_devs, car_devs = _split_devices(devices)

    serial_str = _derive_service_serial(sl, station_devs)
    if not station_devs and _dashboard_client_configured(dashboard_client):
        if not serial_str:
            _LOGGER.warning("Service location %s has no discoverable station serial; skipping", sid)
            return None, None
        station_devs = [
            {
                "uuid": serial_str,
                "id": serial_str,
                "serialNumber": serial_str,
                "type": "CHARGINGSTATION",
            }
        ]
        _LOGGER.debug(
            "No CHARGINGSTATION smartdevice at %s (%s); using service-location serial %s",
            sl.get("name"),
            sid,
            serial_str,
        )
    if not serial_str:
        _LOGGER.warning("Service location %s has no discoverable station serial; skipping", sid)
        return None, None
    if not station_devs:
        _LOGGER.warning(
            "No CHARGINGSTATION smartdevice at %s (%s); skipping site", sl.get("name"), sid
        )
        return None, None

    # station_serial -> {connectors}
    station_serial_to_connectors = await _fetch_dashboard_connector_mapping(
        dashboard_client, station_devs
    )
    if station_serial_to_connectors == {} and _dashboard_client_configured(dashboard_client):
        station_serial_to_connectors = _fallback_dashboard_connector_mapping(station_devs, car_devs)
    if station_serial_to_connectors is None:
        station_serial_to_connectors = {}
    else:
        station_serial_to_connectors = _normalize_connector_mapping_station_keys(
            station_serial_to_connectors,
            serial_str,
        )

    # Check if this service location actually has connector mappings.
    # Some Smappee monitor-only service locations return an empty mapping.
    has_connector_mapping = any(
        (mapping.get("connectors") or {}) for mapping in station_serial_to_connectors.values()
    )

    if not has_connector_mapping:
        _LOGGER.debug(
            "No connector mapping found at %s; assuming monitor-only service location",
            sid,
        )

    # filter smartdevices who only belong in mappings to stations/connectors
    allowed_station_serials = set(station_serial_to_connectors.keys())
    if allowed_station_serials:
        filtered_station_devs = [
            station
            for station in station_devs
            if (_station_serial(station) or _safe_str(station.get("uuid")))
            in allowed_station_serials
        ]
        if filtered_station_devs:
            station_devs = filtered_station_devs
        elif has_connector_mapping:
            station_devs = _station_devices_from_connector_mapping(station_serial_to_connectors)
            _LOGGER.debug(
                "Connector mapping at %s did not match discovered station identifiers; "
                "using %d station serials from Dashboard mapping",
                sid,
                len(station_devs),
            )

    allowed_connector_uuids = {
        connector_uuid
        for mapping in station_serial_to_connectors.values()
        for connector_uuid in (mapping.get("connectors") or {})
    }
    if allowed_connector_uuids:
        car_devs = [
            connector
            for connector in car_devs
            if _connector_uuid(connector) in allowed_connector_uuids
        ]

    # build station map with station_client + empty connector buckets
    stations = _make_station_clients(serial_str, sid, station_devs)
    if not stations and has_connector_mapping:
        mapping_station_devs = _station_devices_from_connector_mapping(station_serial_to_connectors)
        if mapping_station_devs:
            _LOGGER.debug(
                "Connector mapping at %s yielded no usable station smartdevices; "
                "using %d station serials from Dashboard mapping",
                sid,
                len(mapping_station_devs),
            )
            stations = _make_station_clients(serial_str, sid, mapping_station_devs)

    # fill connector buckets / fallback only when connector mapping exists
    if has_connector_mapping:
        _assign_connectors(stations, car_devs, station_serial_to_connectors, serial_str, sid)
        total_assigned = sum(len(bucket.connectors) for bucket in stations.values())
        if total_assigned == 0 and len(stations) == 1:
            _fallback_assign(stations, car_devs, serial_str, sid)
        elif total_assigned == 0:
            _LOGGER.warning(
                "Connector mapping exists at %s, but no connectors could be assigned "
                "across %d station buckets",
                sid,
                len(stations),
            )
    _log_station_runtime_shape("service-location", stations)

    # create coordinators per station
    await _create_coordinators(
        hass,
        stations,
        update_interval,
        config_entry=config_entry,
        dashboard_client=dashboard_client,
    )

    # MQTT (shared per site, but updates all station coordinators)
    mqtt = _setup_mqtt(hass, suuid, serial_str, sid, stations, client_id_prefix, update_interval)

    # put mqtt ref in each bucket
    for bucket in stations.values():
        bucket.mqtt = mqtt

    return stations, mqtt
