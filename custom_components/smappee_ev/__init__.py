import asyncio
from datetime import timedelta
from inspect import isawaitable
import logging
from typing import cast

from aiohttp import ClientError, ClientSession
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from . import discovery
from .api_client import SmappeeApiClient
from .const import CONF_PASSWORD, DOMAIN, UPDATE_INTERVAL_DEFAULT
from .coordinator import SmappeeCoordinator
from .data import RuntimeData, SmappeeEvConfigEntry
from .mqtt_gateway import SmappeeMqtt
from .oauth import OAuth2Client, SmappeeAuthError
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
    t = dev.get("type")
    if isinstance(t, dict):
        return (t.get("category") or "").upper() == "CARCHARGER"
    return (dev.get("type") or "").upper() == "CARCHARGER"


def _safe_str(val) -> str | None:
    """Convert to stripped string or None if not possible."""
    try:
        s = str(val)
    except (TypeError, ValueError):
        return None
    return s.strip() or None


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

    if data.get("refresh_token") and CONF_PASSWORD in data:
        data.pop(CONF_PASSWORD)
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


async def _fetch_metering_cfg(
    oauth_client, session, sid, serial_str, station_devs
) -> dict[str, dict]:
    """Return {station_serial: {connectors{uuid:{id,position}}}}"""
    try:
        tmp_client = SmappeeApiClient(
            oauth_client,
            serial_str,
            _safe_str(station_devs[0].get("uuid")) or "station",
            _safe_str(station_devs[0].get("id")) or "0",
            sid,
            session=session,
            is_station=True,
        )
        cfg = await tmp_client.async_get_metering_configuration()
    except (ClientError, ValueError, KeyError, TimeoutError) as err:
        _LOGGER.warning("Failed to parse meteringconfiguration for %s: %s", sid, err)
        return {}

    out: dict[str, dict] = {}
    for st in (cfg or {}).get("chargingStations", []) or []:
        st_serial = _safe_str(st.get("serialNumber")) or _safe_str(st.get("serial"))
        if not st_serial:
            continue
        # Only stations at this site
        # -id == sid
        # or connecSerialNumber == deviceserialNumber
        st_id = _safe_str(st.get("id"))
        connect_sn = _safe_str(st.get("connectSerialNumber"))
        if not (st_id == _safe_str(sid) or connect_sn == serial_str):
            continue
        bucket = out.setdefault(st_serial, {"connectors": {}})
        for chg in st.get("chargers", []) or []:
            cuuid = _safe_str(chg.get("uuid"))
            if not cuuid:
                continue
            bucket["connectors"][cuuid] = {
                "id": _safe_str(chg.get("id")) or _safe_str(chg.get("smartDeviceId")),
                "position": chg.get("position"),
            }
    return out


def _make_station_clients(
    oauth_client, serial_str, sid, session, station_devs: list[dict]
) -> dict[str, dict]:
    stations: dict[str, dict] = {}
    for sd in station_devs:
        st_uuid = _safe_str(sd.get("uuid"))
        st_id = _safe_str(sd.get("id"))
        if not st_uuid or not st_id:
            continue

        st_serial = _find_in(sd, "serialNumber", "serial") or st_uuid
        st_client = SmappeeApiClient(
            oauth_client, serial_str, st_uuid, st_id, sid, session=session, is_station=True
        )
        stations[st_uuid] = {
            "station_client": st_client,
            "connector_clients": {},
            "coordinator": None,
            "mqtt": None,
            "serial": st_serial,
        }
    return stations


def _assign_connectors(stations, car_devs, mapping, oauth_client, serial_str, sid, session):
    for bucket in stations.values():
        st_serial = bucket.get("serial")
        if not st_serial or st_serial not in mapping:
            continue
        for cuuid, info in mapping[st_serial]["connectors"].items():
            src = next((d for d in car_devs if _safe_str(d.get("uuid")) == cuuid), None)
            if not src:
                continue
            cid = _safe_str(src.get("id")) or info.get("id") or "0"
            bucket["connector_clients"][cuuid] = SmappeeApiClient(
                oauth_client,
                serial_str,
                cuuid,
                cid,
                sid,
                session=session,
                connector_number=info.get("position")
                or src.get("connectorNumber")
                or src.get("position")
                or 1,
                charging_station_serial=st_serial,
            )


def _fallback_assign(stations, car_devs, oauth_client, serial_str, sid, session):
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
        cuuid = _safe_str(d.get("uuid"))
        cid = _safe_str(d.get("id"))
        if not cuuid or not cid:
            continue
        subset[cuuid] = SmappeeApiClient(
            oauth_client,
            serial_str,
            cuuid,
            cid,
            sid,
            session=session,
            connector_number=d.get("connectorNumber") or d.get("position") or 1,
            charging_station_serial=st_serial,
        )
    stations[first_uuid]["connector_clients"] = subset


async def _create_coordinators(hass, stations, update_interval, config_entry=None):
    for bucket in stations.values():
        coord = SmappeeCoordinator(
            hass,
            station_client=bucket["station_client"],
            connector_clients=bucket["connector_clients"],
            update_interval=update_interval,
            config_entry=config_entry,
        )
        await coord.async_config_entry_first_refresh()
        bucket["coordinator"] = coord
        coord.async_start_session_tracking()


def _setup_mqtt(
    hass, suuid, serial_str, sid, stations, client_id_prefix: str, update_interval: int
) -> SmappeeMqtt | None:
    if not suuid:
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

    mqtt = SmappeeMqtt(
        service_location_uuid=suuid,
        client_id=f"{client_id_prefix}-{sid}",
        serial_number=serial_str,
        on_properties=_on_props,
        service_location_id=sid,
        on_connection_change=_on_conn,
    )
    mqtt.track_start_task(hass.async_create_task(mqtt.start()))

    return mqtt


async def _prepare_site(
    hass: HomeAssistant,
    session: ClientSession,
    oauth_client: OAuth2Client,
    sl: dict,
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
) -> tuple[dict[str, dict] | None, SmappeeMqtt | None]:
    """Build coordinators, station/connector clients and MQTT for one service location."""

    sid = sl["serviceLocationId"]
    suuid = sl.get("serviceLocationUuid")
    serial_str = (sl.get("deviceSerialNumber") or "").strip()
    if not serial_str:
        _LOGGER.warning("Service location %s has no valid deviceSerialNumber; skipping", sid)
        return None, None

    devices = await discovery.async_fetch_devices(session, oauth_client, sid)
    if devices is None:
        return None, None

    station_devs, car_devs = _split_devices(devices)
    if not station_devs and not car_devs:
        _LOGGER.info("No chargers at %s (%s); skipping", sl.get("name") or f"Smappee {sid}", sid)
        return None, None
    if not station_devs:
        _LOGGER.warning(
            "No CHARGINGSTATION smartdevice at %s (%s); skipping site", sl.get("name"), sid
        )
        return None, None

    # station_serial -> {connectors}
    station_serial_to_connectors = await _fetch_metering_cfg(
        oauth_client, session, sid, serial_str, station_devs
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
        station_devs = [
            sd
            for sd in station_devs
            if (_find_in(sd, "serialNumber", "serial") or _safe_str(sd.get("uuid")))
            in allowed_station_serials
        ]

    allowed_connector_uuids = {
        cu for m in station_serial_to_connectors.values() for cu in (m.get("connectors") or {})
    }
    if allowed_connector_uuids:
        car_devs = [cd for cd in car_devs if _safe_str(cd.get("uuid")) in allowed_connector_uuids]

    # build station map with station_client + empty connector buckets
    stations = _make_station_clients(oauth_client, serial_str, sid, session, station_devs)

    # fill connector buckets / fallback only when connector mapping exists
    if has_connector_mapping:
        _assign_connectors(
            stations, car_devs, station_serial_to_connectors, oauth_client, serial_str, sid, session
        )
        total_assigned = sum(len(b["connector_clients"]) for b in stations.values())
        if total_assigned == 0 and len(stations) == 1:
            _fallback_assign(stations, car_devs, oauth_client, serial_str, sid, session)
        elif total_assigned == 0:
            _LOGGER.warning(
                "Connector mapping exists at %s, but no connectors could be assigned; "
                "not using fallback because multiple stations exist",
                sid,
            )

    # create coordinators per station
    await _create_coordinators(hass, stations, update_interval, config_entry=config_entry)

    # MQTT (shared per site, but updates all station coordinators)
    mqtt = _setup_mqtt(hass, suuid, serial_str, sid, stations, client_id_prefix, update_interval)

    # put mqtt ref in each bucket
    for b in stations.values():
        b["mqtt"] = mqtt

    return stations, mqtt


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

    def _store_tokens(tokens: dict[str, object]) -> None:
        data = dict(entry.data)
        data.update(tokens)
        if data.get("refresh_token"):
            data.pop(CONF_PASSWORD, None)
        hass.config_entries.async_update_entry(entry, data=data)

    oauth_client = OAuth2Client(entry.data, session=session, token_update_callback=_store_tokens)

    # 1) Discover sites
    try:
        with_serial = await discovery.async_discover_service_locations(session, oauth_client)
    except SmappeeAuthError as err:
        raise ConfigEntryAuthFailed(f"Auth failed: {err}") from err
    except (ClientError, RuntimeError, ValueError) as err:
        # Authentication / authorization problems should trigger reauth
        if getattr(oauth_client, "access_token", None) is None:
            raise ConfigEntryAuthFailed(f"Auth failed: {err}") from err
        _LOGGER.debug("Transient error loading service locations: %s", err)
        raise ConfigEntryNotReady(f"Loading service locations failed: {err}") from err
    if not with_serial:
        _LOGGER.debug("No service locations with deviceSerialNumber found (retry later)")
        raise ConfigEntryNotReady("No service locations with deviceSerialNumber found")

    sites: dict[int, dict] = {}
    mqtt_clients: dict[int, SmappeeMqtt] = {}

    # 2) Prepare each site in parallel
    client_id_prefix = f"ha-{entry.entry_id[-6:]}"
    prep_tasks = [
        _prepare_site(
            hass,
            session,
            oauth_client,
            sl,
            update_interval,
            client_id_prefix,
            config_entry=entry,
        )
        for sl in with_serial
    ]
    results = await asyncio.gather(*prep_tasks, return_exceptions=True)
    for sl, res in zip(with_serial, results, strict=True):
        if isinstance(res, asyncio.CancelledError):
            raise res
        if isinstance(res, ConfigEntryAuthFailed):
            raise res
        if isinstance(res, SmappeeAuthError):
            raise ConfigEntryAuthFailed(f"Smappee authentication failed: {res}") from res
        if isinstance(res, BaseException):
            _LOGGER.warning("Site %s preparation failed: %s", sl.get("serviceLocationId"), res)
            continue
        stations_map, mqtt = res
        if not stations_map:
            continue
        sid = sl["serviceLocationId"]
        if mqtt:
            mqtt_clients[sid] = mqtt
        sites[sid] = {
            "stations": stations_map,
            "name": sl.get("name"),
            "serviceLocationUuid": sl.get("serviceLocationUuid"),
            "deviceSerialNumber": sl.get("deviceSerialNumber"),
        }

    if not sites:
        _LOGGER.debug("Discovered service locations but no stations mapped yet (retry later)")
        raise ConfigEntryNotReady("No Smappee EV stations discovered (will retry)")

    # Store runtime data only on the entry (preferred pattern); avoid duplicating in hass.data
    runtime = RuntimeData(
        api=oauth_client,
        sites=sites,
        mqtt=cast(dict[int, object], mqtt_clients),
    )
    entry.runtime_data = runtime

    # Platforms start
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Services already registered domain-wide in async_setup

    for _svc in ("set_available", "set_unavailable", "set_brightness", "set_min_surpluspct"):
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
