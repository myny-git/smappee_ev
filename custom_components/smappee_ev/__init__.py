import asyncio
import logging
import time

from aiohttp import ClientError, ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api_client import SmappeeApiClient
from .const import BASE_URL, CONF_UPDATE_INTERVAL, DOMAIN, UPDATE_INTERVAL_DEFAULT
from .coordinator import SmappeeCoordinator
from .mqtt_gateway import SmappeeMqtt
from .oauth import OAuth2Client
from .services import register_services, unregister_services

_LOGGER = logging.getLogger(__name__)
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


async def _discover_service_locations(
    session: ClientSession, oauth_client: OAuth2Client
) -> list[dict]:
    """Return all service locations that have a deviceSerialNumber."""
    await oauth_client.ensure_token_valid()
    headers = {
        "Authorization": f"Bearer {oauth_client.access_token}",
        "Content-Type": "application/json",
    }
    resp = await session.get(f"{BASE_URL}/servicelocation", headers=headers)
    if resp.status != 200:
        text = await resp.text()
        raise RuntimeError(f"/servicelocation failed: {resp.status} - {text}")
    data = await resp.json()
    locations = data.get("serviceLocations", []) if isinstance(data, dict) else (data or [])
    return [sl for sl in locations if sl.get("deviceSerialNumber")]


# prepare site helpers
async def _fetch_devices(
    session: ClientSession, oauth_client: OAuth2Client, sid: int
) -> list[dict] | None:
    await oauth_client.ensure_token_valid()
    headers = {
        "Authorization": f"Bearer {oauth_client.access_token}",
        "Content-Type": "application/json",
    }
    resp = await session.get(f"{BASE_URL}/servicelocation/{sid}/smartdevices", headers=headers)
    if resp.status != 200:
        _LOGGER.warning(
            "GET smartdevices for %s failed: %s - %s", sid, resp.status, await resp.text()
        )
        return None
    return await resp.json()


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
        #-id == sid
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
        st_client = SmappeeApiClient(
            oauth_client, serial_str, st_uuid, st_id, sid, session=session, is_station=True
        )
        stations[st_uuid] = {
            "station_client": st_client,
            "connector_clients": {},
            "coordinator": None,
            "mqtt": None,
            "serial": _find_in(sd, "serialNumber", "serial"),
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
            )


def _fallback_assign(stations, car_devs, oauth_client, serial_str, sid, session):
    total_assigned = sum(len(b["connector_clients"]) for b in stations.values())
    if total_assigned > 0:
        return
    first_uuid = next(iter(stations.keys()), None)
    if not first_uuid:
        return
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
        )
    stations[first_uuid]["connector_clients"] = subset


async def _create_coordinators(hass, stations, update_interval):
    for bucket in stations.values():
        coord = SmappeeCoordinator(
            hass,
            station_client=bucket["station_client"],
            connector_clients=bucket["connector_clients"],
            update_interval=update_interval,
        )
        await coord.async_config_entry_first_refresh()
        bucket["coordinator"] = coord


def _setup_mqtt(hass, suuid, serial_str, sid, stations) -> SmappeeMqtt | None:
    if not suuid:
        _LOGGER.warning("No serviceLocationUuid for %s; MQTT disabled for this site", sid)
        return None

    def _on_props(topic: str, payload: dict) -> None:
        for bucket in stations.values():
            coord = bucket.get("coordinator")
            if coord:
                coord.apply_mqtt_properties(topic, payload)
                if coord.data and coord.data.station:
                    coord.data.station.last_mqtt_rx = time.time()
                    coord.async_set_updated_data(coord.data)

    def _on_conn(up: bool) -> None:
        for bucket in stations.values():
            coord = bucket.get("coordinator")
            if coord and coord.data and coord.data.station:
                coord.data.station.mqtt_connected = up
                coord.data.station.last_mqtt_rx = time.time()
                coord.async_set_updated_data(coord.data)

    mqtt = SmappeeMqtt(
        service_location_uuid=suuid,
        client_id=f"ha-{hass.data.get(DOMAIN, {}).get('iid', 'x')}-{sid}",
        serial_number=serial_str,
        on_properties=_on_props,
        service_location_id=sid,
        on_connection_change=_on_conn,
    )
    hass.async_create_task(mqtt.start())

    # disable polling if MQTT is active
    for b in stations.values():
        coord = b.get("coordinator")
        if coord:
            coord.update_interval = None
    return mqtt


async def _prepare_site(
    hass: HomeAssistant,
    session: ClientSession,
    oauth_client: OAuth2Client,
    sl: dict,
    update_interval: int,
) -> tuple[dict[str, dict] | None, SmappeeMqtt | None]:
    """Build coordinators, station/connector clients and MQTT for one service location."""

    sid = sl["serviceLocationId"]
    suuid = sl.get("serviceLocationUuid")
    serial_str = (sl.get("deviceSerialNumber") or "").strip()
    if not serial_str:
        _LOGGER.warning("Service location %s has no valid deviceSerialNumber; skipping", sid)
        return None, None

    devices = await _fetch_devices(session, oauth_client, sid)
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

    # filter smartdevices who only belong in mappings to stations/connectors
    allowed_station_serials = set(station_serial_to_connectors.keys())
    if allowed_station_serials:
        station_devs = [
            sd for sd in station_devs
            if (_find_in(sd, "serialNumber", "serial") or _safe_str(sd.get("uuid"))) in allowed_station_serials
        ]

    allowed_connector_uuids = {
        cu for m in station_serial_to_connectors.values() for cu in (m.get("connectors") or {})
    }
    if allowed_connector_uuids:
        car_devs = [
            cd for cd in car_devs
            if _safe_str(cd.get("uuid")) in allowed_connector_uuids
        ]

    # build station map with station_client + empty connector buckets
    stations = _make_station_clients(oauth_client, serial_str, sid, session, station_devs)

    # fill connector buckets
    _assign_connectors(
        stations, car_devs, station_serial_to_connectors, oauth_client, serial_str, sid, session
    )

    # fallback if no assignment worked
    _fallback_assign(stations, car_devs, oauth_client, serial_str, sid, session)

    # create coordinators per station
    await _create_coordinators(hass, stations, update_interval)

    # MQTT (shared per site, but updates all station coordinators)
    mqtt = _setup_mqtt(hass, suuid, serial_str, sid, stations)

    # put mqtt ref in each bucket
    for b in stations.values():
        b["mqtt"] = mqtt

    return stations, mqtt


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Smappee EV component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Smappee EV account entry that discovers all service locations with a charger."""
    _LOGGER.debug("Setting up Smappee EV account entry: %s", entry.title)

    hass.data.setdefault(DOMAIN, {})

    # Use HA's aiohttp session
    session: ClientSession = async_get_clientsession(hass)

    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_DEFAULT)

    oauth_client = OAuth2Client(entry.data, session=session)

    # 1) Discover sites
    try:
        with_serial = await _discover_service_locations(session, oauth_client)
    except (ClientError, RuntimeError, ValueError) as err:
        _LOGGER.error("Loading service locations failed: %s", err)
        return False
    if not with_serial:
        _LOGGER.warning("No service locations with deviceSerialNumber found")
        return False

    sites: dict[int, dict] = {}
    mqtt_clients: dict[int, SmappeeMqtt] = {}

    # 2) Prepare each site
    for sl in with_serial:
        stations_map, mqtt = await _prepare_site(hass, session, oauth_client, sl, update_interval)
        if not stations_map:
            continue
        sid = sl["serviceLocationId"]
        if mqtt:
            mqtt_clients[sid] = mqtt
        sites[sid] = {"stations": stations_map}

    if not sites:
        _LOGGER.error("No Smappee EV stations discovered; aborting setup")
        return False

    # Platforms store
    hass.data[DOMAIN][entry.entry_id] = {
        "api": oauth_client,
        "sites": sites,  # { sid: { "stations": { station_uuid: {coordinator, station_client, connector_clients, mqtt} } } }
        "mqtt": mqtt_clients,  # {sid: mqtt}
        "_last_options": dict(entry.options),
    }

    # Platforms start
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.data[DOMAIN].get("services_registered", False):
        register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    for _svc in ("set_available", "set_unavailable", "set_brightness", "set_min_surpluspct"):
        try:
            if hass.services.has_service(DOMAIN, _svc):
                hass.services.async_remove(DOMAIN, _svc)
                _LOGGER.info("Removed deprecated service %s.%s", DOMAIN, _svc)
        except (RuntimeError, ValueError) as err:
            _LOGGER.debug("While removing deprecated service %s: %s", _svc, err)

    entry.async_on_unload(entry.add_update_listener(async_entry_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Smappee EV config entry."""
    _LOGGER.debug("Unloading Smappee EV config entry: %s", entry.entry_id)

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}) or {}
    # Stop all MQTT-clients per site
    for sid, mqtt in (data.get("mqtt") or {}).items():
        try:
            await mqtt.stop()
        except asyncio.CancelledError:
            raise
        except (RuntimeError, OSError) as err:
            _LOGGER.warning("Failed to stop MQTT client for service location %s: %s", sid, err)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        active_keys = [k for k in hass.data[DOMAIN] if k != "services_registered"]
        if not active_keys:
            unregister_services(hass)
            hass.data.pop(DOMAIN, None)
    return unload_ok


async def async_entry_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle updates to config entry options."""
    ctx = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    prev = ctx.get("_last_options", {})
    cur = dict(entry.options)

    if cur != prev:
        ctx["_last_options"] = cur
        hass.data[DOMAIN][entry.entry_id] = ctx
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        _LOGGER.debug("ConfigEntry change.")
