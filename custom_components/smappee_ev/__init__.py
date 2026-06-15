import asyncio
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
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    UPDATE_INTERVAL_DEFAULT,
)
from .coordinator import SmappeeCoordinator
from .dashboard_client import SmappeeDashboardClient
from .data import RuntimeData, SmappeeEvConfigEntry
from .device_handle import SmappeeDeviceHandle
from .discovery import (
    MqttChannelSpec,
    SmappeeLocationTopology,
    build_topologies_from_full_details,
    parse_mqtt_channel_specs_from_highlevel,
    unique_mqtt_channel_specs,
)
from .mqtt_gateway import SmappeeMqtt
from .services import register_services, unregister_services

_LOGGER = logging.getLogger(__name__)
_SERVICE_REGISTRATION_SENTINEL = "start_charging"
PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
]

CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)

# -------------------------
# Helpers for discovery
# -------------------------


def _is_station(dev: dict) -> bool:
    """True if device is a CHARGINGSTATION smartdevice."""
    t = dev.get("type")
    if isinstance(t, dict):
        return (t.get("category") or "").upper() == "CHARGINGSTATION"
    return (dev.get("type") or "").upper() == "CHARGINGSTATION"


def _is_connector(dev: dict) -> bool:
    """True if device is a CARCHARGER smartdevice."""
    if isinstance(dev.get("carCharger"), dict):
        return True
    t = dev.get("type")
    if isinstance(t, dict):
        return (t.get("category") or "").upper() == "CARCHARGER"
    return (dev.get("type") or "").upper() == "CARCHARGER"


def _safe_str(val) -> str | None:
    """Convert to stripped string or None if not possible."""
    if val is None:
        return None
    try:
        s = str(val)
    except (TypeError, ValueError):
        return None
    s = s.strip()
    if s.lower() in {"none", "null"}:
        return None
    return s or None


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

    Flow VERSION = 5.
    Version history relevant here:
      - v4 (and earlier) could still persist an 'update_interval' in data/options.
      - v5 removes user control of update interval (internal only) and drops that field.
    We migrate incrementally so users can skip versions safely.
    """
    version = entry.version
    data = dict(entry.data)
    options = dict(entry.options)

    updated = False

    # v5 cleanup: remove legacy 'update_interval' key if present (from v4 or earlier)
    if version < 5:
        if "update_interval" in data:
            data.pop("update_interval")
            updated = True
        if "update_interval" in options:
            options.pop("update_interval")
            updated = True
        version = 5

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

    if updated or version != entry.version:
        hass.config_entries.async_update_entry(entry, data=data, options=options, version=version)
        _LOGGER.info("Smappee EV config entry %s migrated to version %s", entry.entry_id, version)
    else:
        _LOGGER.debug(
            "Smappee EV config entry %s already at latest version %s", entry.entry_id, version
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


def _mqtt_specs_from_highlevel_configs(
    configs: dict[int, dict[str, Any]]
) -> list[MqttChannelSpec]:
    specs: list[MqttChannelSpec] = []
    for sid, cfg in configs.items():
        parsed = parse_mqtt_channel_specs_from_highlevel(sid, cfg)
        for spec in parsed:
            _LOGGER.debug(
                "Smappee highlevel mapping: sid=%s role=%s metric=%s topic=%s paths=%s",
                spec.service_location_id,
                spec.role,
                spec.metric,
                spec.topic,
                spec.aspect_paths,
            )
        specs.extend(parsed)
    return unique_mqtt_channel_specs(specs)


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
        bucket = out.setdefault(station_serial, {"connectors": {}})
        for module in details.get("modules") or []:
            if not isinstance(module, dict):
                continue
            smart_device = module.get("smartDevice")
            if not isinstance(smart_device, dict) or not _is_connector(smart_device):
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
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    name = str(measurement.get("name") or "")
    match = re.search(r"(?:^|\s-\s|\s)(\d+)\s*$", name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
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


def _make_station_clients(serial_str, sid, station_devs: list[dict]) -> dict[str, dict]:
    stations: dict[str, dict] = {}
    for sd in station_devs:
        st_uuid = _device_uuid(sd)
        st_id = _safe_str(sd.get("id")) or st_uuid
        if not st_uuid or not st_id:
            continue

        st_serial = _station_serial(sd) or st_uuid
        st_client = SmappeeDeviceHandle(
            serial_str,
            st_uuid,
            st_id,
            sid,
            is_station=True,
            charging_station_serial=st_serial,
        )
        stations[st_uuid] = {
            "station_client": st_client,
            "connector_clients": {},
            "coordinator": None,
            "mqtt": None,
            "serial": st_serial,
        }
    return stations


def _make_station_clients_with_mapping_fallback(
    serial_str: str,
    sid: int | str,
    station_devs: list[dict],
    station_serial_to_connectors: dict[str, dict],
    has_connector_mapping: bool,
) -> dict[str, dict]:
    stations = _make_station_clients(serial_str, sid, station_devs)
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
    return _make_station_clients(serial_str, sid, mapping_station_devs)


def _assign_connectors(stations, car_devs, mapping, serial_str, sid):
    for bucket in stations.values():
        st_serial = bucket.get("serial")
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
            bucket["connector_clients"][cuuid] = SmappeeDeviceHandle(
                serial_str,
                cuuid,
                cid,
                sid,
                connector_number=info.get("position")
                or src.get("connectorNumber")
                or src.get("position")
                or 1,
                charging_station_serial=st_serial,
            )


def _fallback_assign(stations, car_devs, serial_str, sid):
    total_assigned = sum(len(b["connector_clients"]) for b in stations.values())
    if total_assigned > 0:
        return
    first_uuid = next(iter(stations.keys()), None)
    if not first_uuid:
        return
    st_serial = stations.get(first_uuid, {}).get("serial")
    _LOGGER.warning(
        "Could not map connectors to stations at %s; assigning all to first station", sid
    )
    subset = {}
    for d in car_devs:
        cuuid = _connector_uuid(d)
        cid = _safe_str(d.get("id")) or cuuid
        if not cuuid or not cid:
            continue
        subset[cuuid] = SmappeeDeviceHandle(
            serial_str,
            cuuid,
            cid,
            sid,
            connector_number=d.get("connectorNumber") or d.get("position") or 1,
            charging_station_serial=st_serial,
        )
    stations[first_uuid]["connector_clients"] = subset


async def _create_coordinators(
    hass,
    stations,
    update_interval,
    config_entry=None,
    dashboard_client=None,
    highlevel_configs: dict[int, dict[str, Any]] | None = None,
):
    for bucket in stations.values():
        kwargs = {
            "station_client": bucket["station_client"],
            "connector_clients": bucket["connector_clients"],
            "update_interval": update_interval,
            "config_entry": config_entry,
        }
        if dashboard_client is not None:
            kwargs["dashboard_client"] = dashboard_client
        if highlevel_configs is not None:
            kwargs["highlevel_configs"] = highlevel_configs
        coord = SmappeeCoordinator(hass, **kwargs)
        await coord.async_config_entry_first_refresh()
        bucket["coordinator"] = coord
        coord.async_start_session_tracking()


def _setup_mqtt(
    hass,
    suuid,
    serial_str,
    sid,
    stations,
    client_id_prefix: str,
    update_interval: int,
    mqtt_specs: list[MqttChannelSpec] | None = None,
) -> SmappeeMqtt | None:
    if not suuid and not mqtt_specs:
        _LOGGER.warning("No serviceLocationUuid for %s; MQTT disabled for this site", sid)
        return None

    def _on_props(topic: str, payload: dict) -> None:
        for bucket in stations.values():
            coord = bucket.get("coordinator")
            if coord:
                coord.apply_mqtt_properties(topic, payload)

    refresh_tasks: dict[int, asyncio.Task] = {}

    def _schedule_refresh(coord) -> None:
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
        task.add_done_callback(lambda _task, key=task_key: refresh_tasks.pop(key, None))

    def _on_conn(up: bool) -> None:
        for bucket in stations.values():
            coord = bucket.get("coordinator")
            if coord:
                if up:
                    coord.update_interval = None
                elif coord.update_interval is None:
                    coord.update_interval = timedelta(seconds=update_interval)
                    _schedule_refresh(coord)
                coord.apply_mqtt_connection_change(up)

    if mqtt_specs is None:
        mqtt = SmappeeMqtt(
            service_location_uuid=suuid,
            client_id=f"{client_id_prefix}-{sid}",
            serial_number=serial_str,
            on_properties=_on_props,
            service_location_id=sid,
            on_connection_change=_on_conn,
        )
    else:
        mqtt = SmappeeMqtt(
            service_location_uuid=suuid,
            client_id=f"{client_id_prefix}-{sid}",
            serial_number=serial_str,
            on_properties=_on_props,
            service_location_id=sid,
            on_connection_change=_on_conn,
            mqtt_specs=mqtt_specs,
        )
    for spec in mqtt_specs or []:
        _LOGGER.debug(
            "Smappee MQTT subscription: sid=%s topic=%s username_present=%s source=highlevel",
            spec.service_location_id,
            spec.topic,
            bool(spec.username),
        )
    if suuid and not mqtt_specs:
        _LOGGER.debug(
            "Smappee MQTT subscription: sid=%s topic=servicelocation/%s/power "
            "username_present=True source=legacy",
            sid,
            suuid,
        )
    mqtt.track_start_task(hass.async_create_task(mqtt.start()))

    return mqtt


async def _prepare_site(
    hass: HomeAssistant,
    session: ClientSession,
    sl: dict,
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
    dashboard_client: SmappeeDashboardClient | None = None,
) -> tuple[dict[str, dict] | None, SmappeeMqtt | None]:
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
        total_assigned = sum(len(b["connector_clients"]) for b in stations.values())
        if total_assigned == 0 and len(stations) == 1:
            _fallback_assign(stations, car_devs, serial_str, sid)
        elif total_assigned == 0:
            _LOGGER.warning(
                "Connector mapping exists at %s, but no connectors could be assigned "
                "across %d station buckets",
                sid,
                len(stations),
            )

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
        b["mqtt"] = mqtt

    return stations, mqtt


async def _prepare_topology(
    hass: HomeAssistant,
    topology: SmappeeLocationTopology,
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
    dashboard_client: SmappeeDashboardClient | None = None,
) -> tuple[dict[str, dict] | None, SmappeeMqtt | None]:
    """Prepare one site-first Dashboard topology."""

    site_sid = topology.site_location_id
    control_sid = topology.control_location_id
    measurement_sids = topology.measurement_location_ids

    highlevel_configs = await _dashboard_fetch_highlevel_configs(
        dashboard_client, measurement_sids
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
            highlevel_configs,
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
    )

    if has_connector_mapping:
        _assign_connectors(
            stations,
            car_devs,
            station_serial_to_connectors,
            serial_str,
            control_sid,
        )
        total_assigned = sum(len(b["connector_clients"]) for b in stations.values())
        if total_assigned == 0 and len(stations) == 1:
            _fallback_assign(stations, car_devs, serial_str, control_sid)
        elif total_assigned == 0:
            _LOGGER.warning(
                "Connector mapping exists at control %s, but no connectors could be "
                "assigned across %d station buckets",
                control_sid,
                len(stations),
            )

    await _create_coordinators(
        hass,
        stations,
        update_interval,
        config_entry=config_entry,
        dashboard_client=dashboard_client,
        highlevel_configs=highlevel_configs,
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
    )

    for bucket in stations.values():
        bucket["mqtt"] = mqtt

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


async def async_setup_entry(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> bool:
    """Set up a Smappee EV account entry that discovers all service locations with a charger."""
    _LOGGER.debug("Setting up Smappee EV account entry: %s", entry.title)

    # Use HA's aiohttp session
    session: ClientSession = async_get_clientsession(hass)

    update_interval = UPDATE_INTERVAL_DEFAULT

    dashboard_client = _create_dashboard_client(hass, entry, session)

    # 1) Discover site-first topologies
    topologies = await _load_dashboard_topologies(dashboard_client)

    sites: dict[int, dict] = {}
    mqtt_clients: dict[int, SmappeeMqtt] = {}

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
        )
        for topology in topologies
    ]
    results = await asyncio.gather(*prep_tasks, return_exceptions=True)
    for topology, res in zip(topologies, results, strict=True):
        if isinstance(res, asyncio.CancelledError):
            raise res
        if isinstance(res, ConfigEntryAuthFailed):
            raise res
        if isinstance(res, BaseException):
            _LOGGER.warning("Site %s preparation failed: %s", topology.site_location_id, res)
            continue
        stations_map, mqtt = res
        if not stations_map:
            continue
        sid = topology.site_location_id
        if mqtt:
            mqtt_clients[sid] = mqtt
        site = sites.setdefault(
            sid,
            {
                "stations": {},
                "name": topology.site_name,
                "serviceLocationUuid": topology.site_location_uuid,
                "deviceSerialNumber": topology.site_gateway_serial,
                "controlLocationIds": [],
                "measurementLocationIds": [],
            },
        )
        site["stations"].update(stations_map)
        site["controlLocationIds"].append(topology.control_location_id)
        site["measurementLocationIds"] = list(
            dict.fromkeys(site["measurementLocationIds"] + topology.measurement_location_ids)
        )

    if not sites:
        _LOGGER.debug("Discovered service locations but no stations mapped yet (retry later)")
        raise ConfigEntryNotReady("No Smappee EV stations discovered (will retry)")

    # Store runtime data only on the entry (preferred pattern); avoid duplicating in hass.data
    runtime = RuntimeData(
        api=dashboard_client,
        sites=sites,
        mqtt=cast(dict[int, object], mqtt_clients),
        dashboard=dashboard_client,
    )
    entry.runtime_data = runtime

    # Platforms start
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Services already registered domain-wide in async_setup

    for _svc in ("set_available", "set_unavailable", "set_min_surpluspct"):
        try:
            if hass.services.has_service(DOMAIN, _svc):
                hass.services.async_remove(DOMAIN, _svc)
                _LOGGER.info("Removed deprecated service %s.%s", DOMAIN, _svc)
        except (RuntimeError, ValueError) as err:
            _LOGGER.debug("While removing deprecated service %s: %s", _svc, err)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> bool:
    """Unload a Smappee EV config entry."""
    _LOGGER.debug("Unloading Smappee EV config entry: %s", entry.entry_id)
    try:
        rd = entry.runtime_data
    except AttributeError:
        _LOGGER.debug(
            "Unload requested for %s but no runtime_data present (may have failed early)",
            entry.entry_id,
        )
    else:
        if isinstance(rd, RuntimeData):
            # Stop all MQTT clients referenced in runtime_data
            for sid, mqtt in (rd.mqtt or {}).items():
                stop_fn = getattr(mqtt, "stop", None)
                if not callable(stop_fn):  # pragma: no cover - defensive
                    continue
                try:
                    result = stop_fn()
                    if asyncio.iscoroutine(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except (RuntimeError, OSError) as err:
                    _LOGGER.warning(
                        "Failed to stop MQTT client for service location %s: %s", sid, err
                    )

            # Allow coordinators to shutdown any background tasks
            for site in (rd.sites or {}).values():
                for bucket in site.get("stations", {}).values():
                    coord = bucket.get("coordinator")
                    if coord and hasattr(coord, "async_shutdown"):
                        try:
                            await coord.async_shutdown()
                        except asyncio.CancelledError:
                            raise
                        except (RuntimeError, OSError, ValueError) as exc:
                            _LOGGER.debug("Coordinator shutdown issue: %s", exc)
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
        for station_uuid, bucket in (site.get("stations") or {}).items():
            serial = bucket.get("serial")
            if not serial:
                station_client = bucket.get("station_client")
                serial = getattr(station_client, "serial_id", None) or getattr(
                    station_client, "serial", None
                )
            if serial:
                identifiers.add(f"{sid}:{serial}:{station_uuid}")
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
