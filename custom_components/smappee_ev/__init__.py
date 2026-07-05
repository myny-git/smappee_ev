"""Set up and manage runtime data for the Smappee EV integration."""

import asyncio
from inspect import isawaitable
import logging
from typing import Any

from aiohttp import ClientError, ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api.dashboard_client import SmappeeDashboardClient
from .api.discovery import SmappeeLocationTopology, build_topologies_from_full_details
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
from .helpers import connector_device_identifier, site_device_identifier, station_device_identifier
from .models.runtime_data import (
    MqttRuntimeValue,
    RuntimeData,
    SmappeeEvConfigEntry,
    SmappeeSiteRuntime,
    SmappeeStationRuntime,
)
from .models.state import DashboardObject, DashboardObjectList, HighLevelConfigMap
from .mqtt_setup import (
    _build_mqtt_clients,
    _build_mqtt_routes,
    _handle_mqtt_connection_change,
    _handle_mqtt_refresh_done,
    _iter_mqtt_clients,
    _log_mqtt_subscriptions,
    _mqtt_client_count,
    _mqtt_runtime_value,
    _setup_mqtt,
)
from .mqtt_specs import (
    _group_mqtt_specs_by_credentials,
    _mqtt_specs_from_highlevel_configs,
    _service_location_uuid_from_mqtt_topic,
    _split_highlevel_configs_by_scope,
)
from .runtime_assembly import (
    _create_coordinators,
    _create_site_coordinator,
    _log_station_runtime_shape,
    _log_stored_runtime_shape,
    _prepare_topology,
)
from .services import register_services
from .topology import (
    _add_connector_runtime,
    _assign_connectors,
    _charging_station_from_service_location,
    _connector_position_from_measurement,
    _connector_uuid,
    _derive_service_serial,
    _device_uuid,
    _fallback_assign,
    _fallback_highlevel_connector_mapping,
    _find_in,
    _is_connector,
    _is_station,
    _make_led_runtimes,
    _make_station_clients,
    _make_station_clients_with_mapping_fallback,
    _normalize_connector_mapping_station_keys,
    _normalize_dashboard_service_location,
    _safe_str,
    _split_devices,
    _station_devices_from_connector_mapping,
    _station_serial,
    _uuid_from_dashboard_channel,
)

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
) -> DashboardObjectList | None:
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
        except (ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            _LOGGER.warning("Dashboard highlevel configuration failed for %s: %s", sid, err)
            continue
        if isinstance(cfg, dict):
            configs[sid] = cfg
    return configs


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


def _begin_runtime_shutdown(rd: RuntimeData) -> None:
    """Synchronously mark runtime resources as stopping."""
    for site in (rd.sites or {}).values():
        for bucket in site.stations.values():
            coord = bucket.station_coordinator
            cancel_delayed = getattr(coord, "cancel_delayed_refreshes", None)
            if callable(cancel_delayed):
                cancel_delayed()

    for mqtt in (rd.mqtt or {}).values():
        for mqtt_client in _iter_mqtt_clients(mqtt):
            begin_shutdown = getattr(mqtt_client, "begin_shutdown", None)
            if callable(begin_shutdown):
                begin_shutdown()


def _register_runtime_stop_cleanup(
    hass: HomeAssistant,
    entry: SmappeeEvConfigEntry,
    runtime: RuntimeData,
) -> None:
    """Cancel runtime background work as soon as Home Assistant begins stopping."""

    @callback
    def _handle_homeassistant_stop(_event: Event) -> None:
        _begin_runtime_shutdown(runtime)
        hass.async_create_task(_async_shutdown_runtime_resources(runtime))

    remove_stop_listener = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP,
        _handle_homeassistant_stop,
    )
    entry.async_on_unload(remove_stop_listener)


def _remove_legacy_led_controller_devices(
    registry: dr.DeviceRegistry,
    entry: SmappeeEvConfigEntry,
) -> None:
    """Remove old standalone LED Controller devices for this config entry."""
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not any(
            domain == DOMAIN and identifier.startswith("led:")
            for domain, identifier in device.identifiers
        ):
            continue

        if device.config_entries == {entry.entry_id}:
            registry.async_remove_device(device.id)
        else:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


def _register_runtime_devices(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> None:
    """Ensure the HA device registry contains the real Smappee hierarchy."""
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        return
    registry = dr.async_get(hass)
    _remove_legacy_led_controller_devices(registry, entry)
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
            control_sid = bucket.control_location_id
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

    if entry.data.get(CONF_NEEDS_DASHBOARD_REAUTH) or not _dashboard_client_configured(
        dashboard_client
    ):
        raise ConfigEntryAuthFailed(
            "Smappee Dashboard credentials are required after migration to API v10/v11"
        )

    # 1) Discover site-first topologies
    topologies = await _load_dashboard_topologies(dashboard_client)

    sites: dict[int, SmappeeSiteRuntime] = {}
    mqtt_clients: dict[int, MqttRuntimeValue] = {}
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
            site.stations.update(stations_map)
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
                mqtt=mqtt_clients,
                dashboard=dashboard_client,
                background_tasks=background_tasks,
            )
        )
        raise

    # Store runtime data only on the entry (preferred pattern); avoid duplicating in hass.data
    runtime = RuntimeData(
        api=dashboard_client,
        sites=sites,
        mqtt=mqtt_clients,
        dashboard=dashboard_client,
        background_tasks=background_tasks,
    )
    entry.runtime_data = runtime
    _register_runtime_stop_cleanup(hass, entry, runtime)
    _log_stored_runtime_shape(runtime)
    try:
        _register_runtime_devices(hass, entry)

        # Platforms start
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Services already registered domain-wide in async_setup
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
    shutdown = getattr(site_coord, "async_shutdown", None)
    if callable(shutdown):
        try:
            result = shutdown()
            if isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError, ValueError) as exc:
            _LOGGER.debug("Site coordinator shutdown issue: %s", exc)


async def _async_shutdown_runtime_resources(rd: RuntimeData) -> None:
    """Stop MQTT clients and coordinator background tasks for runtime data."""
    # Mark coordinators as shutting down before stopping MQTT, because MQTT
    # disconnect callbacks can otherwise schedule fallback refreshes.
    _begin_runtime_shutdown(rd)
    for site in (rd.sites or {}).values():
        await _shutdown_site_coordinator(site)
        for bucket in site.stations.values():
            coord = bucket.station_coordinator
            shutdown = getattr(coord, "async_shutdown", None)
            if callable(shutdown):
                try:
                    result = shutdown()
                    if isawaitable(result):
                        await result
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

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


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
