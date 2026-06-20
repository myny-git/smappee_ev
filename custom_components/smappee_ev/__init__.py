"""Set up and manage runtime data for the Smappee EV integration."""

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from datetime import timedelta
from inspect import isawaitable
import logging
import re
from typing import Any, cast

from aiohttp import ClientError, ClientSession
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    CONF_NEEDS_DASHBOARD_REAUTH,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    MANUFACTURER,
    UPDATE_INTERVAL_DEFAULT,
)
from .coordinator import SmappeeCoordinator, SmappeeSiteCoordinator, SmappeeStationCoordinator
from .dashboard_client import SmappeeDashboardClient
from .data import (
    RuntimeData,
    SmappeeConnectorRuntime,
    SmappeeEvConfigEntry,
    SmappeeLedRuntime,
    SmappeeSiteRuntime,
    SmappeeStationRuntime,
)
from .device_handle import SmappeeDeviceHandle
from .discovery import (
    MqttChannelSpec,
    SmappeeLocationTopology,
    build_topologies_from_full_details,
    parse_mqtt_channel_specs_from_highlevel,
)
from .helpers import (
    connector_device_identifier,
    led_device_identifier,
    site_device_identifier,
    station_device_identifier,
)
from .mqtt_gateway import SmappeeMqtt, redact_mqtt_topic
from .services import register_services, unregister_services

_LOGGER = logging.getLogger(__name__)
_SERVICE_REGISTRATION_SENTINEL = "start_charging"
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)
MqttRuntimeValue = SmappeeMqtt | list[SmappeeMqtt] | None
MqttRouteTarget = SmappeeSiteCoordinator | SmappeeStationCoordinator

# Discovery helpers.


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
    # direct
    for k in keys:
        if k in dev and _safe_str(dev[k]):
            return _safe_str(dev[k])
    # scan configuration/properties for a field that looks like a serial
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


def _uuid_from_dashboard_channel(smart_device: dict[str, Any]) -> str | None:
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


def _device_uuid(dev: dict[str, Any]) -> str | None:
    return (
        _safe_str(dev.get("uuid"))
        or _safe_str(dev.get("smartDeviceUuid"))
        or _uuid_from_dashboard_channel(dev)
    )


def _connector_uuid(dev: dict[str, Any]) -> str | None:
    return _uuid_from_dashboard_channel(dev) or _device_uuid(dev)


def _station_serial(dev: dict[str, Any]) -> str | None:
    return _find_in(dev, "serialNumber", "serial") or _device_uuid(dev)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entry versions to the current format.

    Version history:
      - v5 removes user control of update interval and drops old OAuth/v3 fields.
      - v6 marks entries without Dashboard credentials for reauthentication.
    """
    version = entry.version
    data = dict(entry.data)
    options = dict(entry.options)

    updated = False

    # v5 cleanup: remove legacy update interval key if present.
    if version < 5:
        if "update_interval" in data:
            data.pop("update_interval")
            updated = True
        if "update_interval" in options:
            options.pop("update_interval")
            updated = True
        version = 5

    # Remove old v3/OAuth credentials.
    for old_key in (
        "client_id",
        "client_secret",
        "access_token",
        "refresh_token",
        "token_expires_at",
    ):
        if old_key in data:
            data.pop(old_key)
            updated = True

    # v6: Dashboard v10/v11 requires Dashboard credentials.
    if version < 6:
        has_dashboard_credentials = bool(
            data.get(CONF_DASHBOARD_REFRESH_TOKEN)
            or (data.get(CONF_USERNAME) and data.get(CONF_PASSWORD))
        )

        if not has_dashboard_credentials:
            data[CONF_NEEDS_DASHBOARD_REAUTH] = True
            updated = True

        version = 6

    if updated or version != entry.version:
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=version,
        )
        _LOGGER.info(
            "Config entry %s migrated to version %s",
            entry.entry_id,
            version,
        )
    else:
        _LOGGER.debug(
            "Config entry %s already at latest version %s",
            entry.entry_id,
            version,
        )

    return True


def _split_devices(devices: list[dict]) -> tuple[list[dict], list[dict]]:
    stations = [d for d in (devices or []) if _is_station(d)]
    cars = [d for d in (devices or []) if _is_connector(d)]
    return stations, cars


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


def _charging_station_from_service_location(item: dict[str, Any]) -> dict[str, Any]:
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
    item: dict[str, Any], *, allow_non_charging_function_type: bool = False
) -> dict[str, Any] | None:
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


async def _dashboard_discover_service_locations(
    dashboard_client: SmappeeDashboardClient | None,
) -> list[dict[str, Any]] | None:
    if not _dashboard_client_configured(dashboard_client):
        return None
    if dashboard_client is None:
        return None
    try:
        locations = await dashboard_client.async_get_service_locations_full_details()
    except asyncio.CancelledError:
        raise
    except (ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
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
    except (ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
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
) -> list[dict[str, Any]] | None:
    if not _dashboard_client_configured(dashboard_client):
        return None
    if dashboard_client is None:
        return None
    try:
        devices = await dashboard_client.async_get_smart_devices(sid)
    except asyncio.CancelledError:
        raise
    except (ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
        _LOGGER.warning("Dashboard smart device discovery failed for %s: %s", sid, err)
        return []
    return devices if isinstance(devices, list) else []


async def _dashboard_fetch_highlevel_configs(
    dashboard_client: SmappeeDashboardClient | None,
    measurement_sids: list[int],
) -> dict[int, dict[str, Any]]:
    """Fetch highlevelconfiguration for all measurement service locations."""
    if not _dashboard_client_configured(dashboard_client) or dashboard_client is None:
        return {}

    configs: dict[int, dict[str, Any]] = {}
    for sid in dict.fromkeys(measurement_sids):
        try:
            cfg = await dashboard_client.async_get_highlevel_configuration(sid)
        except asyncio.CancelledError:
            raise
        except (ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            _LOGGER.warning("Dashboard highlevel configuration failed for %s: %s", sid, err)
            continue
        if isinstance(cfg, dict):
            configs[sid] = cfg
    return configs


def _mqtt_specs_from_highlevel_configs(configs: dict[int, dict[str, Any]]) -> list[MqttChannelSpec]:
    """Return all highlevel MQTT specs.

    Multiple logical measurements can share one physical MQTT topic. Keep every
    role here so routing can deliver that topic to every coordinator that needs
    it; the MQTT client deduplicates subscriptions by topic later.
    """
    specs: list[MqttChannelSpec] = []
    for sid, cfg in configs.items():
        parsed = parse_mqtt_channel_specs_from_highlevel(sid, cfg)
        for spec in parsed:
            _LOGGER.debug(
                "Smappee highlevel mapping: sid=%s role=%s metric=%s topic=%s paths=%s",
                spec.service_location_id,
                spec.role,
                spec.metric,
                redact_mqtt_topic(spec.topic),
                spec.aspect_paths,
            )
        specs.extend(parsed)
    return specs


def _split_highlevel_configs_by_scope(
    configs: dict[int, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Return ``(site_configs, station_configs)`` based on highlevel MQTT roles."""
    site_configs: dict[int, dict[str, Any]] = {}
    station_configs: dict[int, dict[str, Any]] = {}
    for sid, cfg in configs.items():
        specs = parse_mqtt_channel_specs_from_highlevel(sid, cfg)
        if any(spec.role in {"grid", "production", "consumption", "always_on"} for spec in specs):
            site_configs[sid] = cfg
        if any(spec.role == "car_charger" for spec in specs):
            station_configs[sid] = cfg
    return site_configs, station_configs


def _build_mqtt_routes(
    mqtt_specs: list[MqttChannelSpec] | None,
    site_coordinator: SmappeeSiteCoordinator | None,
    stations: dict[str, SmappeeStationRuntime],
) -> dict[str, list[MqttRouteTarget]]:
    """Build explicit MQTT topic routes for site and station coordinators."""
    routes: dict[str, list[MqttRouteTarget]] = {}
    station_coordinators: list[MqttRouteTarget] = []
    for bucket in stations.values():
        coordinator = bucket.station_coordinator
        if coordinator is not None:
            station_coordinators.append(cast(MqttRouteTarget, coordinator))
    for spec in mqtt_specs or []:
        if spec.role in {"grid", "production", "consumption", "always_on"}:
            if site_coordinator is not None:
                routes.setdefault(spec.topic, []).append(site_coordinator)
            continue
        if spec.role == "car_charger":
            for coord in station_coordinators:
                routes.setdefault(spec.topic, []).append(coord)

    deduped_routes: dict[str, list[MqttRouteTarget]] = {}
    for topic, targets in routes.items():
        seen: set[int] = set()
        deduped_targets: list[MqttRouteTarget] = []
        for target in targets:
            target_id = id(target)
            if target_id in seen:
                continue
            seen.add(target_id)
            deduped_targets.append(target)
        deduped_routes[topic] = deduped_targets
    return deduped_routes


def _service_location_uuid_from_mqtt_topic(topic: str | None) -> str | None:
    """Return the service-location UUID embedded in a Dashboard MQTT topic."""
    if not topic:
        return None
    match = re.match(r"^servicelocation/([^/]+)/", topic)
    return _safe_str(match.group(1)) if match else None


def _group_mqtt_specs_by_credentials(
    suuid: str | None,
    sid: int | str,
    mqtt_specs: list[MqttChannelSpec],
) -> list[
    tuple[
        list[MqttChannelSpec],
        list[str],
        dict[str, int | str],
    ]
]:
    """Group MQTT specs so every MQTT connection uses one credential set."""
    grouped_specs: dict[tuple[str | None, str | None], list[MqttChannelSpec]] = {}
    service_location_ids_by_group: dict[tuple[str | None, str | None], dict[str, int | str]] = {}

    for spec in mqtt_specs:
        spec_suuid = _service_location_uuid_from_mqtt_topic(spec.topic) or suuid
        username = spec.username or spec_suuid or suuid
        password = spec.password or spec_suuid or suuid
        key = (username, password)
        grouped_specs.setdefault(key, []).append(spec)
        if spec_suuid:
            service_location_ids_by_group.setdefault(key, {})[spec_suuid] = spec.service_location_id

    groups = []
    for key, specs in grouped_specs.items():
        service_location_ids_by_uuid = service_location_ids_by_group.get(key, {})
        service_location_uuids = list(service_location_ids_by_uuid)
        if not service_location_uuids and suuid:
            service_location_uuids = [suuid]
            service_location_ids_by_uuid = {suuid: sid}
        groups.append((specs, service_location_uuids, service_location_ids_by_uuid))
    return groups


async def _fetch_dashboard_connector_mapping(
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
        except (ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
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

    for station_serial, bucket in mapping.items():
        if not isinstance(bucket, dict):
            continue
        connectors = bucket.get("connectors") or {}
        key = _safe_str(station_serial)
        if not key:
            orphan_connectors.update(connectors)
            continue
        target = normalized.setdefault(key, {"connectors": {}})
        target["connectors"].update(connectors)

    if orphan_connectors:
        fallback_key = _safe_str(fallback_station_serial) or next(iter(normalized), None)
        if fallback_key:
            target = normalized.setdefault(fallback_key, {"connectors": {}})
            target["connectors"].update(orphan_connectors)

    return normalized


def _station_devices_from_connector_mapping(mapping: dict[str, dict]) -> list[dict[str, Any]]:
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


def _derive_service_serial(sl: dict[str, Any], station_devs: list[dict]) -> str | None:
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


def _log_station_runtime_shape(context: str, stations: Mapping[str, object]) -> None:
    """Log the station runtime container shape without exposing raw identifiers."""
    station_count = len(stations)
    connector_count = sum(
        len(station.connectors)
        for station in stations.values()
        if isinstance(station, SmappeeStationRuntime)
    )
    runtime_type = type(next(iter(stations.values()))).__name__ if stations else "none"
    _LOGGER.debug(
        "Prepared Smappee EV %s runtime: station_runtime=%s, stations=%d, connectors=%d",
        context,
        runtime_type,
        station_count,
        connector_count,
    )


def _log_stored_runtime_shape(runtime: RuntimeData) -> None:
    """Log the stored RuntimeData shape without exposing raw identifiers."""
    site_count = len(runtime.sites or {})
    site_types = sorted({type(site).__name__ for site in (runtime.sites or {}).values()}) or [
        "none"
    ]
    stations = [
        bucket for site in (runtime.sites or {}).values() for bucket in site.stations.values()
    ]
    station_types = sorted({type(bucket).__name__ for bucket in stations}) or ["none"]
    connector_count = sum(len(bucket.connectors) for bucket in stations)
    _LOGGER.debug(
        "Stored Smappee EV runtime_data: sites=%d, site_runtime_types=%s, "
        "station_runtime_types=%s, stations=%d, connectors=%d",
        site_count,
        ",".join(site_types),
        ",".join(station_types),
        len(stations),
        connector_count,
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
        st_client = SmappeeDeviceHandle(
            serial_str,
            st_uuid,
            st_id,
            sid,
            is_station=True,
            charging_station_serial=st_serial,
            site_location_id=site_location_id or sid,
            charging_station_model=metadata.get("station_model"),
        )
        stations[st_uuid] = SmappeeStationRuntime(
            site_location_id=site_location_id or sid,
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
    total_assigned = sum(len(b.connectors) for b in stations.values())
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
    for d in car_devs:
        cuuid = _connector_uuid(d)
        cid = _safe_str(d.get("id")) or cuuid
        if not cuuid or not cid:
            continue
        site_location_id = first_bucket.site_location_id
        position = d.get("connectorNumber") or d.get("position") or 1
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


async def _create_coordinators(
    hass,
    stations: dict[str, SmappeeStationRuntime],
    update_interval,
    config_entry=None,
    dashboard_client=None,
    highlevel_configs: dict[int, dict[str, Any]] | None = None,
):
    for bucket in stations.values():
        kwargs = {
            "station_client": bucket.station_client,
            "connector_clients": {
                key: connector.connector_client for key, connector in bucket.connectors.items()
            },
            "update_interval": update_interval,
            "config_entry": config_entry,
        }
        if dashboard_client is not None:
            kwargs["dashboard_client"] = dashboard_client
        if highlevel_configs is not None:
            kwargs["highlevel_configs"] = highlevel_configs
        for key in (
            "site_name",
            "gateway_serial",
            "gateway_type",
            "station_name",
            "station_model",
        ):
            value = (
                bucket.charging_station_model if key == "station_model" else getattr(bucket, key)
            )
            if value is not None:
                kwargs[key] = value
        coord = SmappeeCoordinator(hass, **kwargs)
        await coord.async_config_entry_first_refresh()
        bucket.station_coordinator = coord
        coord.async_start_session_tracking()


async def _create_site_coordinator(
    hass,
    *,
    topology: SmappeeLocationTopology,
    update_interval: int,
    config_entry=None,
    highlevel_configs: dict[int, dict[str, Any]] | None = None,
) -> SmappeeSiteCoordinator:
    """Create the site-scoped coordinator for grid/PV/house data."""
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=topology.site_location_id,
        site_name=topology.site_name,
        site_uuid=topology.site_location_uuid,
        gateway_serial=topology.site_gateway_serial,
        gateway_type=topology.site_gateway_type,
        update_interval=update_interval,
        config_entry=config_entry,
        highlevel_configs=highlevel_configs,
    )
    await coord.async_config_entry_first_refresh()
    return coord


def _mqtt_runtime_value(mqtt_clients: list[SmappeeMqtt]) -> MqttRuntimeValue:
    """Return a backwards-compatible MQTT runtime value."""
    if not mqtt_clients:
        return None
    if len(mqtt_clients) == 1:
        return mqtt_clients[0]
    return mqtt_clients


def _build_mqtt_clients(
    *,
    suuid: str | None,
    serial_str: str,
    sid: int | str,
    client_id_prefix: str,
    on_properties,
    on_connection_change,
    mqtt_specs: list[MqttChannelSpec] | None,
) -> list[SmappeeMqtt]:
    """Create one or more MQTT clients for a site based on credential sets."""
    if mqtt_specs is None:
        return [
            SmappeeMqtt(
                service_location_uuid=suuid,
                client_id=f"{client_id_prefix}-{sid}",
                serial_number=serial_str,
                on_properties=on_properties,
                service_location_id=sid,
                on_connection_change=on_connection_change,
            )
        ]

    spec_groups = _group_mqtt_specs_by_credentials(suuid, sid, mqtt_specs)
    mqtt_clients: list[SmappeeMqtt] = []
    for index, (specs, service_location_uuids, service_location_ids_by_uuid) in enumerate(
        spec_groups, start=1
    ):
        mqtt_clients.append(
            SmappeeMqtt(
                service_location_uuid=service_location_uuids[0]
                if service_location_uuids
                else suuid,
                service_location_uuids=service_location_uuids,
                service_location_ids_by_uuid=service_location_ids_by_uuid,
                client_id=f"{client_id_prefix}-{sid}"
                if len(spec_groups) == 1
                else f"{client_id_prefix}-{sid}-{index}",
                serial_number=serial_str,
                on_properties=on_properties,
                service_location_id=sid,
                on_connection_change=on_connection_change,
                mqtt_specs=specs,
            )
        )
    return mqtt_clients


def _log_mqtt_subscriptions(
    sid: int | str,
    suuid: str | None,
    mqtt_specs: list[MqttChannelSpec] | None,
) -> None:
    """Log MQTT subscriptions without leaking credentials."""
    seen_topics: set[str] = set()
    for spec in mqtt_specs or []:
        if spec.topic in seen_topics:
            continue
        seen_topics.add(spec.topic)
        _LOGGER.debug(
            "Smappee MQTT subscription: sid=%s topic=%s username_present=%s source=highlevel",
            spec.service_location_id,
            redact_mqtt_topic(spec.topic),
            bool(spec.username),
        )
    if suuid and not mqtt_specs:
        _LOGGER.debug(
            "Smappee MQTT subscription: sid=%s topic=%s username_present=True source=legacy",
            sid,
            redact_mqtt_topic(f"servicelocation/{suuid}/power"),
        )


def _handle_mqtt_refresh_done(
    refresh_tasks: dict[int, asyncio.Task], done_task: asyncio.Task, key: int
) -> None:
    """Drop a finished MQTT fallback refresh task and consume its exception."""
    refresh_tasks.pop(key, None)
    if done_task.cancelled():
        return
    try:
        exc = done_task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _LOGGER.debug("MQTT fallback refresh failed: %s", exc)


def _handle_mqtt_connection_change(
    up: bool,
    site_coordinator: SmappeeSiteCoordinator | None,
    stations: dict[str, SmappeeStationRuntime],
    update_interval: int,
    schedule_refresh,
) -> None:
    """Apply MQTT connection state to site and station coordinators."""
    if site_coordinator is not None:
        site_coordinator.apply_mqtt_connection_change(up)
    for bucket in stations.values():
        coord = bucket.station_coordinator
        if coord:
            if up:
                coord.update_interval = None
            elif coord.update_interval is None:
                coord.update_interval = timedelta(seconds=update_interval)
                schedule_refresh(coord)
            coord.apply_mqtt_connection_change(up)


def _setup_mqtt(
    hass,
    suuid,
    serial_str,
    sid,
    stations,
    client_id_prefix: str,
    update_interval: int,
    mqtt_specs: list[MqttChannelSpec] | None = None,
    site_coordinator: SmappeeSiteCoordinator | None = None,
    background_tasks: set[asyncio.Task] | None = None,
) -> MqttRuntimeValue:
    if not suuid and not mqtt_specs:
        _LOGGER.warning("No serviceLocationUuid for %s; MQTT disabled for this site", sid)
        return None

    def _on_props(topic: str, payload: dict) -> None:
        targets = mqtt_routes.get(topic)
        if targets is None:
            targets = []
            if site_coordinator is not None and topic.endswith("/power") and not mqtt_specs:
                targets.append(site_coordinator)
            targets.extend(
                bucket.station_coordinator
                for bucket in stations.values()
                if bucket.station_coordinator is not None
            )
        for coord in targets:
            if coord:
                try:
                    coord.apply_mqtt_properties(topic, payload)
                except Exception:
                    _LOGGER.exception("Failed to apply MQTT properties from %s", topic)

    refresh_tasks: dict[int, asyncio.Task] = {}

    def _schedule_refresh(coord) -> None:
        if getattr(coord, "_shutting_down", False):
            return
        task_key = id(coord)
        existing = refresh_tasks.get(task_key)
        if existing is not None and not existing.done():
            return

        refresh = getattr(coord, "async_request_refresh", None)
        if not callable(refresh):
            return

        refresh_result = refresh()
        if not isawaitable(refresh_result):
            return

        task = hass.async_create_task(refresh_result)
        refresh_tasks[task_key] = task
        if background_tasks is not None:
            background_tasks.add(task)
        task.add_done_callback(
            lambda done_task, key=task_key: _handle_mqtt_refresh_done(refresh_tasks, done_task, key)
        )
        if background_tasks is not None:
            task.add_done_callback(background_tasks.discard)

    def _on_conn(up: bool) -> None:
        _handle_mqtt_connection_change(
            up,
            site_coordinator,
            stations,
            update_interval,
            _schedule_refresh,
        )

    mqtt_routes = _build_mqtt_routes(mqtt_specs, site_coordinator, stations)

    mqtt_clients = _build_mqtt_clients(
        suuid=suuid,
        serial_str=serial_str,
        sid=sid,
        client_id_prefix=client_id_prefix,
        on_properties=_on_props,
        on_connection_change=_on_conn,
        mqtt_specs=mqtt_specs,
    )
    for mqtt in mqtt_clients:
        mqtt.track_start_task(hass.async_create_task(mqtt.start()))

    _log_mqtt_subscriptions(sid, suuid, mqtt_specs)
    return _mqtt_runtime_value(mqtt_clients)


def _iter_mqtt_clients(value: object) -> list[object]:
    """Return MQTT clients from legacy single-client or grouped-client runtime values."""
    if isinstance(value, list | tuple):
        return list(value)
    return [value] if value is not None else []


def _mqtt_client_count(mqtt_by_site: dict[int, object]) -> int:
    """Return the total number of MQTT clients across all sites."""
    return sum(len(_iter_mqtt_clients(mqtt)) for mqtt in mqtt_by_site.values())


def _register_runtime_devices(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> None:
    """Ensure the HA device registry contains the real Smappee hierarchy."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        return
    registry = dr.async_get(hass)
    rd = entry.runtime_data
    for site_sid, site in (rd.sites or {}).items():
        site_identifier = site_device_identifier(site_sid)
        site_name = site.site_name or f"Smappee {site_sid}"
        gateway_serial = site.gateway_serial
        gateway_type = site.gateway_type
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={site_identifier},
            manufacturer=MANUFACTURER,
            name=f"Smappee {site_name}",
            model=f"{gateway_type} / Service Location" if gateway_type else "Service Location",
            serial_number=str(gateway_serial) if gateway_serial else None,
        )

        for station_uuid, bucket in site.stations.items():
            station_serial = bucket.charging_station_serial
            if not station_serial:
                continue
            control_sid = bucket.control_location_id or site_sid
            station_identifier = station_device_identifier(site_sid, control_sid, station_serial)
            station_identifiers = {station_identifier}
            station_client = bucket.station_client
            legacy_serial = getattr(station_client, "serial_id", None) or station_serial
            station_identifiers.add((DOMAIN, f"{site_sid}:{legacy_serial}:{station_uuid}"))
            registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers=station_identifiers,
                manufacturer=MANUFACTURER,
                name=bucket.station_name or f"Smappee EV {station_serial}",
                model=bucket.charging_station_model or "EV Wall",
                serial_number=str(station_serial),
                via_device=site_identifier,
            )

            for led_key, led in bucket.led_devices.items():
                led_id = led.led_device_id or led_key
                if not led_id:
                    continue
                registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={
                        led_device_identifier(site_sid, control_sid, station_serial, led_id)
                    },
                    manufacturer=MANUFACTURER,
                    name=led.led_device_name or f"Smappee EV {station_serial} LED controller",
                    model="LED Controller",
                    via_device=station_identifier,
                )

            for connector_uuid, info in bucket.connectors.items():
                client = info.connector_client
                position = info.connector_position or getattr(client, "connector_number", None)
                connector_key = connector_uuid or (
                    f"position:{position}" if position else "unknown"
                )
                label = str(position) if position is not None else str(connector_key)
                registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={
                        connector_device_identifier(
                            site_sid, control_sid, station_serial, str(connector_key)
                        )
                    },
                    manufacturer=MANUFACTURER,
                    name=f"Smappee EV {station_serial} | Connector {label}",
                    model="Connector",
                    via_device=station_identifier,
                )


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


async def _async_prepare_site(
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
        (m.get("connectors") or {}) for m in station_serial_to_connectors.values()
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
            sd
            for sd in station_devs
            if (_station_serial(sd) or _safe_str(sd.get("uuid"))) in allowed_station_serials
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
        cu for m in station_serial_to_connectors.values() for cu in (m.get("connectors") or {})
    }
    if allowed_connector_uuids:
        car_devs = [cd for cd in car_devs if _connector_uuid(cd) in allowed_connector_uuids]

    # build station map with station_client + empty connector buckets
    stations = _make_station_clients_with_mapping_fallback(
        serial_str,
        sid,
        station_devs,
        station_serial_to_connectors,
        has_connector_mapping,
    )

    # fill connector buckets / fallback only when connector mapping exists
    if has_connector_mapping:
        _assign_connectors(stations, car_devs, station_serial_to_connectors, serial_str, sid)
        total_assigned = sum(len(b.connectors) for b in stations.values())
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
    for b in stations.values():
        b.mqtt = mqtt

    return stations, mqtt


async def _prepare_topology(
    hass: HomeAssistant,
    topology: SmappeeLocationTopology,
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
    dashboard_client: SmappeeDashboardClient | None = None,
    background_tasks: set[asyncio.Task] | None = None,
) -> tuple[dict[str, SmappeeStationRuntime] | None, MqttRuntimeValue]:
    """Prepare one site-first Dashboard topology."""

    site_sid = topology.site_location_id
    control_sid = topology.control_location_id
    measurement_sids = topology.measurement_location_ids

    highlevel_configs = await _dashboard_fetch_highlevel_configs(dashboard_client, measurement_sids)
    site_highlevel_configs, station_highlevel_configs = _split_highlevel_configs_by_scope(
        highlevel_configs
    )
    mqtt_specs = _mqtt_specs_from_highlevel_configs(highlevel_configs)

    devices = await _dashboard_fetch_devices(dashboard_client, control_sid)
    if devices is None:
        return None, None

    station_devs, car_devs = _split_devices(devices)
    station_serial = topology.charging_station_serial
    serial_str = (
        topology.site_gateway_serial
        or topology.control_gateway_serial
        or station_serial
        or f"smappee-{site_sid}"
    )

    if station_serial and not station_devs:
        station_devs = [
            {
                "uuid": station_serial,
                "id": station_serial,
                "serialNumber": station_serial,
                "type": "CHARGINGSTATION",
            }
        ]
        _LOGGER.debug(
            "No CHARGINGSTATION smartdevice at control %s; using topology station serial %s",
            control_sid,
            station_serial,
        )

    if not station_devs and not car_devs and not station_serial:
        _LOGGER.info("No chargers at %s (%s); skipping", topology.control_name, control_sid)
        return None, None

    station_serial_to_connectors = await _fetch_dashboard_connector_mapping(
        dashboard_client, station_devs
    )
    if station_serial_to_connectors == {} and _dashboard_client_configured(dashboard_client):
        station_serial_to_connectors = _fallback_dashboard_connector_mapping(station_devs, car_devs)
    if station_serial_to_connectors == {}:
        station_serial_to_connectors = _fallback_highlevel_connector_mapping(
            station_serial,
            station_highlevel_configs,
        )
    if station_serial_to_connectors is None:
        station_serial_to_connectors = {}
    else:
        station_serial_to_connectors = _normalize_connector_mapping_station_keys(
            station_serial_to_connectors,
            station_serial or serial_str,
        )

    has_connector_mapping = any(
        (m.get("connectors") or {}) for m in station_serial_to_connectors.values()
    )

    allowed_station_serials = set(station_serial_to_connectors.keys())
    if allowed_station_serials:
        filtered_station_devs = [
            sd
            for sd in station_devs
            if (_station_serial(sd) or _safe_str(sd.get("uuid"))) in allowed_station_serials
        ]
        if filtered_station_devs:
            station_devs = filtered_station_devs
        elif has_connector_mapping:
            station_devs = _station_devices_from_connector_mapping(station_serial_to_connectors)

    allowed_connector_uuids = {
        cu for m in station_serial_to_connectors.values() for cu in (m.get("connectors") or {})
    }
    if allowed_connector_uuids:
        car_devs = [cd for cd in car_devs if _connector_uuid(cd) in allowed_connector_uuids]

    stations = _make_station_clients_with_mapping_fallback(
        serial_str,
        control_sid,
        station_devs,
        station_serial_to_connectors,
        has_connector_mapping,
        site_location_id=site_sid,
        site_name=topology.site_name,
        gateway_serial=topology.site_gateway_serial,
        gateway_type=topology.site_gateway_type,
        control_name=topology.control_name,
        control_uuid=topology.control_location_uuid,
        control_function_type=topology.control_function_type,
        station_metadata=station_serial_to_connectors,
    )

    if has_connector_mapping:
        _assign_connectors(
            stations,
            car_devs,
            station_serial_to_connectors,
            serial_str,
            control_sid,
        )
        total_assigned = sum(len(b.connectors) for b in stations.values())
        if total_assigned == 0 and len(stations) == 1:
            _fallback_assign(stations, car_devs, serial_str, control_sid)
        elif total_assigned == 0:
            _LOGGER.warning(
                "Connector mapping exists at control %s, but no connectors could be "
                "assigned across %d station buckets",
                control_sid,
                len(stations),
            )
    _log_station_runtime_shape("site-topology", stations)

    site_coordinator = await _create_site_coordinator(
        hass,
        topology=topology,
        update_interval=update_interval,
        config_entry=config_entry,
        highlevel_configs=site_highlevel_configs,
    )

    await _create_coordinators(
        hass,
        stations,
        update_interval,
        config_entry=config_entry,
        dashboard_client=dashboard_client,
        highlevel_configs=station_highlevel_configs,
    )

    mqtt = _setup_mqtt(
        hass,
        topology.site_location_uuid or topology.control_location_uuid,
        serial_str,
        site_sid,
        stations,
        client_id_prefix,
        update_interval,
        mqtt_specs=mqtt_specs,
        site_coordinator=site_coordinator,
        background_tasks=background_tasks,
    )

    for bucket in stations.values():
        bucket.mqtt = mqtt
        bucket.site_coordinator = site_coordinator
        bucket.highlevel_configs = highlevel_configs

    return stations, mqtt


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
) -> list[dict[str, Any]]:
    try:
        locations = await _dashboard_discover_service_locations(dashboard_client)
    except (ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
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
    except (ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
        _LOGGER.debug("Transient error loading dashboard topologies: %s", err)
        raise ConfigEntryNotReady(f"Loading service location topology failed: {err}") from err

    if topologies is None:
        _LOGGER.debug("Dashboard discovery is not configured yet (retry later)")
        raise ConfigEntryNotReady("Dashboard API is not configured")
    if not topologies:
        _LOGGER.debug("No candidate charging topologies found (retry later)")
        raise ConfigEntryNotReady("No candidate charging topologies found")
    return topologies


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Smappee EV component."""
    # Register services once domain-wide (multi-entry safe)
    if not hass.services.has_service(DOMAIN, _SERVICE_REGISTRATION_SENTINEL):
        await register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> bool:  # noqa: C901
    """Set up a Smappee EV account entry that discovers all service locations with a charger."""
    _LOGGER.debug("Setting up Smappee EV account entry: %s", entry.title)

    # Use HA's aiohttp session
    session: ClientSession = async_get_clientsession(hass)

    update_interval = UPDATE_INTERVAL_DEFAULT

    dashboard_client = _create_dashboard_client(hass, entry, session)

    if entry.data.get(CONF_NEEDS_DASHBOARD_REAUTH) or not _dashboard_client_configured(
        dashboard_client
    ):
        raise ConfigEntryAuthFailed(
            "Smappee Dashboard credentials are required after migration to API v10/v11"
        )

    # 1) Discover site-first topologies
    topologies = await _load_dashboard_topologies(dashboard_client)

    sites: dict[int, SmappeeSiteRuntime] = {}
    mqtt_clients: dict[int, object] = {}
    background_tasks: set[asyncio.Task] = set()

    # 2) Prepare each site in parallel
    client_id_prefix = f"ha-{entry.entry_id[-6:]}"
    prep_tasks = [
        _prepare_topology(
            hass,
            topology,
            update_interval,
            client_id_prefix,
            config_entry=entry,
            dashboard_client=dashboard_client,
            background_tasks=background_tasks,
        )
        for topology in topologies
    ]
    try:
        results = await asyncio.gather(*prep_tasks, return_exceptions=True)
        hard_error: BaseException | None = None
        for topology, res in zip(topologies, results, strict=True):
            if isinstance(res, asyncio.CancelledError):
                hard_error = hard_error or res
                continue
            if isinstance(res, ConfigEntryAuthFailed):
                hard_error = hard_error or res
                continue
            if isinstance(res, BaseException):
                _LOGGER.warning("Site %s preparation failed: %s", topology.site_location_id, res)
                continue
            stations_map, mqtt = res
            sid = topology.site_location_id
            if mqtt:
                mqtt_clients[sid] = mqtt
            if not stations_map:
                continue
            site = sites.get(sid)
            if site is None:
                site = SmappeeSiteRuntime(
                    site_location_id=sid,
                    site_name=topology.site_name,
                    site_function_type=topology.site_function_type,
                    site_uuid=topology.site_location_uuid,
                    gateway_serial=topology.site_gateway_serial,
                    gateway_type=topology.site_gateway_type,
                )
                sites[sid] = site
            first_bucket: SmappeeStationRuntime | None = next(iter(stations_map.values()), None)
            if site.site_coordinator is None:
                site.site_coordinator = first_bucket.site_coordinator if first_bucket else None
            site.stations.update(cast(dict[str, SmappeeStationRuntime], stations_map))
            site.control_location_ids = list(
                dict.fromkeys([*site.control_location_ids, topology.control_location_id])
            )
            site.measurement_location_ids = list(
                dict.fromkeys([*site.measurement_location_ids, *topology.measurement_location_ids])
            )
            if first_bucket is not None:
                site.highlevel_configs.update(first_bucket.highlevel_configs)

        if hard_error is not None:
            raise hard_error

        if not sites:
            _LOGGER.debug("Discovered service locations but no stations mapped yet (retry later)")
            raise ConfigEntryNotReady("No Smappee EV stations discovered (will retry)")
    except BaseException:
        await _async_shutdown_runtime_resources(
            RuntimeData(
                api=dashboard_client,
                sites=sites,
                mqtt=cast(dict[int, object], mqtt_clients),
                dashboard=dashboard_client,
                background_tasks=background_tasks,
            )
        )
        raise

    # Store runtime data only on the entry (preferred pattern); avoid duplicating in hass.data
    runtime = RuntimeData(
        api=dashboard_client,
        sites=sites,
        mqtt=cast(dict[int, object], mqtt_clients),
        dashboard=dashboard_client,
        background_tasks=background_tasks,
    )
    entry.runtime_data = runtime
    _log_stored_runtime_shape(runtime)
    try:
        _register_runtime_devices(hass, entry)

        # Platforms start
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Services already registered domain-wide in async_setup

        for _svc in (
            "set_available",
            "set_unavailable",
            "set_min_surpluspct",
            "pause_charging_chargingstations",
            "set_charging_mode_chargingstations",
        ):
            try:
                if hass.services.has_service(DOMAIN, _svc):
                    hass.services.async_remove(DOMAIN, _svc)
                    _LOGGER.info("Removed deprecated service %s.%s", DOMAIN, _svc)
            except (RuntimeError, ValueError) as err:
                _LOGGER.debug("While removing deprecated service %s: %s", _svc, err)
    except asyncio.CancelledError:
        await _async_shutdown_runtime_resources(runtime)
        raise
    except Exception:
        await _async_shutdown_runtime_resources(runtime)
        raise

    return True


async def _shutdown_site_coordinator(site: SmappeeSiteRuntime) -> None:
    """Shutdown a site coordinator if present."""
    site_coord = site.site_coordinator
    if site_coord and hasattr(site_coord, "async_shutdown"):
        try:
            await site_coord.async_shutdown()
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError, ValueError) as exc:
            _LOGGER.debug("Site coordinator shutdown issue: %s", exc)


async def _async_shutdown_runtime_resources(rd: RuntimeData) -> None:
    """Stop MQTT clients and coordinator background tasks for runtime data."""
    # Mark coordinators as shutting down before stopping MQTT, because MQTT
    # disconnect callbacks can otherwise schedule fallback refreshes.
    for site in (rd.sites or {}).values():
        await _shutdown_site_coordinator(site)
        for bucket in site.stations.values():
            coord = bucket.station_coordinator
            if coord and hasattr(coord, "async_shutdown"):
                try:
                    await coord.async_shutdown()
                except asyncio.CancelledError:
                    raise
                except (RuntimeError, OSError, ValueError) as exc:
                    _LOGGER.debug("Coordinator shutdown issue: %s", exc)

    for sid, mqtt in (rd.mqtt or {}).items():
        for mqtt_client in _iter_mqtt_clients(mqtt):
            stop_fn = getattr(mqtt_client, "stop", None)
            if not callable(stop_fn):  # pragma: no cover - defensive
                continue
            try:
                result = stop_fn()
                if asyncio.iscoroutine(result):
                    await result
            except asyncio.CancelledError:
                raise
            except (RuntimeError, OSError) as err:
                _LOGGER.warning("Failed to stop MQTT client for service location %s: %s", sid, err)

    pending_tasks = [task for task in rd.background_tasks if not task.done()]
    for task in pending_tasks:
        task.cancel()
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    rd.background_tasks.difference_update(pending_tasks)


async def async_unload_entry(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading config entry: %s", entry.entry_id)
    try:
        rd = entry.runtime_data
    except AttributeError:
        _LOGGER.debug(
            "Unload requested for %s but no runtime_data present (may have failed early)",
            entry.entry_id,
        )
    else:
        if isinstance(rd, RuntimeData):
            await _async_shutdown_runtime_resources(rd)
        else:
            _LOGGER.debug(
                "Unload requested for %s but runtime_data is invalid (may have failed early)",
                entry.entry_id,
            )

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # If no other loaded entries, unregister services
        active_entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]
        if not active_entries and hass.services.has_service(DOMAIN, _SERVICE_REGISTRATION_SENTINEL):
            await unregister_services(hass)
    return unload_ok


def _current_station_device_identifiers(entry: SmappeeEvConfigEntry) -> set[str]:
    """Return Smappee EV device identifiers currently known for this entry."""
    try:
        rd = entry.runtime_data
    except AttributeError:
        return set()
    if not isinstance(rd, RuntimeData):
        return set()

    identifiers: set[str] = set()
    for sid, site in (rd.sites or {}).items():
        for station_uuid, bucket in site.stations.items():
            serial: Any = bucket.charging_station_serial
            if not serial:
                station_client = bucket.station_client
                serial = getattr(station_client, "serial_id", None) or getattr(
                    station_client, "serial", None
                )
            if serial:
                control_sid = bucket.control_location_id or sid
                identifiers.add(f"station:{sid}:{control_sid}:{serial}")
                legacy_serial = getattr(bucket.station_client, "serial_id", None)
                if not isinstance(legacy_serial, str) or not legacy_serial.strip():
                    legacy_serial = serial
                identifiers.add(f"{sid}:{legacy_serial}:{station_uuid}")
    return identifiers


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: SmappeeEvConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow users to remove stale Smappee EV devices from the registry."""
    domain_identifiers = {
        identifier for domain, identifier in device_entry.identifiers if domain == DOMAIN
    }
    if not domain_identifiers:
        return True

    current_identifiers = _current_station_device_identifiers(entry)
    if not current_identifiers:
        return False

    return domain_identifiers.isdisjoint(current_identifiers)
