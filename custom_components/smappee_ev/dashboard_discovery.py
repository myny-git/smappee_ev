"""Dashboard discovery helpers for Smappee EV setup."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import ClientError, ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api.dashboard_client import SmappeeDashboardClient
from .api.discovery import SmappeeLocationTopology, build_topologies_from_full_details
from .api.errors import SmappeeError
from .const import CONF_DASHBOARD_REFRESH_TOKEN, CONF_PASSWORD, CONF_USERNAME
from .models.runtime_data import SmappeeEvConfigEntry
from .models.state import DashboardObjectList, HighLevelConfigMap
from .topology import (
    _connector_uuid,
    _is_connector,
    _normalize_dashboard_service_location,
    _safe_str,
    _station_serial,
)

_LOGGER = logging.getLogger(__name__)


def _dashboard_client_configured(dashboard_client: SmappeeDashboardClient | None) -> bool:
    if dashboard_client is None:
        return False
    return bool(
        getattr(dashboard_client, "_token", None)
        or getattr(dashboard_client, "refresh_token", None)
        or (
            getattr(dashboard_client, "username", None)
            and getattr(dashboard_client, "password", None)
        )
    )


async def _dashboard_discover_service_locations(
    dashboard_client: SmappeeDashboardClient | None,
) -> DashboardObjectList | None:
    if not _dashboard_client_configured(dashboard_client):
        return None
    if dashboard_client is None:
        return None
    try:
        locations = await dashboard_client.async_get_service_locations_full_details()
    except asyncio.CancelledError:
        raise
    except ConfigEntryAuthFailed:
        raise
    except (SmappeeError, ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
        _LOGGER.warning("Dashboard service location discovery failed: %s", err)
        raise

    normalized = [
        loc
        for item in locations or []
        if isinstance(item, dict)
        for loc in [_normalize_dashboard_service_location(item)]
        if loc is not None
    ]
    if normalized:
        return normalized

    fallback = [
        loc
        for item in locations or []
        if isinstance(item, dict)
        for loc in [
            _normalize_dashboard_service_location(item, allow_non_charging_function_type=True)
        ]
        if loc is not None
    ]
    if fallback:
        _LOGGER.debug(
            "Dashboard discovery found no CHARGINGSTATION functionType entries; "
            "trying %d service locations without functionType filtering",
            len(fallback),
        )
    return fallback


async def _dashboard_discover_topologies(
    dashboard_client: SmappeeDashboardClient | None,
) -> list[SmappeeLocationTopology] | None:
    """Discover Dashboard service locations and build site-first topologies."""
    if not _dashboard_client_configured(dashboard_client):
        return None
    if dashboard_client is None:
        return None
    try:
        locations = await dashboard_client.async_get_service_locations_full_details()
    except asyncio.CancelledError:
        raise
    except ConfigEntryAuthFailed:
        raise
    except (SmappeeError, ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
        _LOGGER.warning("Dashboard service location topology discovery failed: %s", err)
        raise

    topologies = build_topologies_from_full_details(
        [location for location in locations or [] if isinstance(location, dict)]
    )
    for topology in topologies:
        _LOGGER.debug(
            "Smappee topology: site=%s(%s), control=%s(%s), measurements=%s, "
            "station_serial=%s, site_gateway=%s, control_gateway=%s",
            topology.site_location_id,
            topology.site_function_type,
            topology.control_location_id,
            topology.control_function_type,
            topology.measurement_location_ids,
            topology.charging_station_serial,
            topology.site_gateway_serial,
            topology.control_gateway_serial,
        )
    return topologies


async def _dashboard_fetch_devices(
    dashboard_client: SmappeeDashboardClient | None, sid: int | str
) -> DashboardObjectList | None:
    if not _dashboard_client_configured(dashboard_client):
        return None
    if dashboard_client is None:
        return None
    try:
        devices = await dashboard_client.async_get_smart_devices(sid)
    except asyncio.CancelledError:
        raise
    except ConfigEntryAuthFailed:
        raise
    except (SmappeeError, ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
        _LOGGER.warning("Dashboard smart device discovery failed for %s: %s", sid, err)
        raise
    if devices is None:
        raise RuntimeError(f"Dashboard smart device discovery returned no data for {sid}")
    if not isinstance(devices, list):
        raise TypeError(f"Dashboard smart device discovery returned malformed data for {sid}")
    return devices


async def _dashboard_fetch_highlevel_configs(
    dashboard_client: SmappeeDashboardClient | None,
    measurement_sids: list[int],
) -> HighLevelConfigMap:
    """Fetch highlevelconfiguration for all measurement service locations."""
    if not _dashboard_client_configured(dashboard_client) or dashboard_client is None:
        return {}

    configs: HighLevelConfigMap = {}
    for sid in dict.fromkeys(measurement_sids):
        try:
            cfg = await dashboard_client.async_get_highlevel_configuration(sid)
        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            raise
        except (
            SmappeeError,
            ClientError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        ) as err:
            _LOGGER.warning("Dashboard highlevel configuration failed for %s: %s", sid, err)
            continue
        if isinstance(cfg, dict):
            configs[sid] = cfg
    return configs


async def _fetch_dashboard_connector_mapping(  # noqa: C901 - validates nested remote payload
    dashboard_client: SmappeeDashboardClient | None, station_devs: list[dict]
) -> dict[str, dict] | None:
    if not _dashboard_client_configured(dashboard_client):
        return None
    if dashboard_client is None:
        return None

    out: dict[str, dict] = {}
    for station in station_devs:
        station_serial = _station_serial(station)
        if not station_serial:
            continue
        try:
            details = await dashboard_client.async_get_charging_station_details(station_serial)
        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            raise
        except (
            SmappeeError,
            ClientError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        ) as err:
            _LOGGER.warning(
                "Dashboard charging station details failed for %s: %s", station_serial, err
            )
            continue
        if not isinstance(details, dict):
            continue
        charging_station = details.get("chargingStation")
        station_model = None
        if isinstance(charging_station, dict):
            station_model = _safe_str(charging_station.get("model"))
        bucket = out.setdefault(station_serial, {"connectors": {}})
        station_model = station_model or _safe_str(details.get("model"))
        if station_model:
            bucket["station_model"] = station_model
        station_name = _safe_str(details.get("name"))
        if station_name:
            bucket["station_name"] = station_name
        for module in details.get("modules") or []:
            if not isinstance(module, dict):
                continue
            smart_device = module.get("smartDevice")
            if not isinstance(smart_device, dict):
                continue
            device_type = smart_device.get("type")
            category = device_type.get("category") if isinstance(device_type, dict) else device_type
            if str(category or "").upper() == "LED":
                led_id = _safe_str(smart_device.get("id")) or _safe_str(
                    smart_device.get("smartDeviceId")
                )
                if led_id:
                    bucket.setdefault("led_devices", {})[led_id] = {
                        "id": led_id,
                        "uuid": _safe_str(smart_device.get("uuid"))
                        or _safe_str(smart_device.get("smartDeviceUuid")),
                        "name": _safe_str(smart_device.get("name")),
                        "smart_device": smart_device,
                    }
                continue
            if not _is_connector(smart_device):
                continue
            connector_uuid = _connector_uuid(smart_device)
            if not connector_uuid:
                continue
            bucket["connectors"][connector_uuid] = {
                "id": _safe_str(smart_device.get("id"))
                or _safe_str(smart_device.get("smartDeviceId")),
                "position": module.get("position"),
                "smart_device": smart_device,
            }
    return out


def _fallback_dashboard_connector_mapping(
    station_devs: list[dict], car_devs: list[dict]
) -> dict[str, dict]:
    if len(station_devs) != 1:
        return {}
    station_serial = _station_serial(station_devs[0])
    if not station_serial:
        return {}

    connectors: dict[str, dict] = {}
    for index, car in enumerate(car_devs, start=1):
        connector_uuid = _connector_uuid(car)
        if not connector_uuid:
            continue
        connectors[connector_uuid] = {
            "id": _safe_str(car.get("id")) or _safe_str(car.get("smartDeviceId")),
            "position": car.get("connectorNumber") or car.get("position") or index,
        }
    return {station_serial: {"connectors": connectors}} if connectors else {}


def _create_dashboard_client(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry, session: ClientSession
) -> SmappeeDashboardClient:
    """Create the optional Dashboard API client for one config entry."""

    def _store_dashboard_tokens(tokens: dict[str, object]) -> None:
        data = dict(entry.data)
        data.update(tokens)
        hass.config_entries.async_update_entry(entry, data=data)

    return SmappeeDashboardClient(
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
        refresh_token=entry.data.get(CONF_DASHBOARD_REFRESH_TOKEN),
        session=session,
        token_update_callback=_store_dashboard_tokens,
    )


async def _load_dashboard_service_locations(
    dashboard_client: SmappeeDashboardClient,
) -> DashboardObjectList:
    try:
        locations = await _dashboard_discover_service_locations(dashboard_client)
    except ConfigEntryAuthFailed:
        raise
    except (SmappeeError, ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
        _LOGGER.debug("Transient error loading dashboard service locations: %s", err)
        raise ConfigEntryNotReady(f"Loading service locations failed: {err}") from err

    if locations is None:
        _LOGGER.debug("Dashboard discovery is not configured yet (retry later)")
        raise ConfigEntryNotReady("Dashboard API is not configured")
    if not locations:
        _LOGGER.debug("No candidate charging service locations found (retry later)")
        raise ConfigEntryNotReady("No candidate charging service locations found")
    return locations


async def _load_dashboard_topologies(
    dashboard_client: SmappeeDashboardClient,
) -> list[SmappeeLocationTopology]:
    try:
        topologies = await _dashboard_discover_topologies(dashboard_client)
    except ConfigEntryAuthFailed:
        raise
    except (SmappeeError, ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
        _LOGGER.debug("Transient error loading dashboard topologies: %s", err)
        raise ConfigEntryNotReady(f"Loading service location topology failed: {err}") from err

    if topologies is None:
        _LOGGER.debug("Dashboard discovery is not configured yet (retry later)")
        raise ConfigEntryNotReady("Dashboard API is not configured")
    if not topologies:
        _LOGGER.debug("No candidate charging topologies found (retry later)")
        raise ConfigEntryNotReady("No candidate charging topologies found")
    return topologies
