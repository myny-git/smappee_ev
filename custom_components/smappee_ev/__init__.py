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


async def _prepare_site(
    hass: HomeAssistant,
    session: ClientSession,
    oauth_client: OAuth2Client,
    sl: dict,
    update_interval: int,
) -> tuple[
    SmappeeCoordinator | None,
    SmappeeMqtt | None,
    SmappeeApiClient | None,
    dict[str, SmappeeApiClient],
]:
    """Build clients, coordinator and MQTT for one service location."""
    sid = sl["serviceLocationId"]
    suuid = sl.get("serviceLocationUuid")
    serial = sl.get("deviceSerialNumber")
    name = (sl.get("name") or f"Smappee {sid}").strip()

    if not isinstance(serial, str) or not serial.strip():
        _LOGGER.warning("Service location %s has no valid deviceSerialNumber; skipping", sid)
        return None, None, None, {}
    serial_str = serial.strip()

    await oauth_client.ensure_token_valid()
    headers = {
        "Authorization": f"Bearer {oauth_client.access_token}",
        "Content-Type": "application/json",
    }
    # Smartdevices
    resp = await session.get(f"{BASE_URL}/servicelocation/{sid}/smartdevices", headers=headers)
    if resp.status != 200:
        _LOGGER.warning(
            "GET smartdevices for %s failed: %s - %s", sid, resp.status, await resp.text()
        )
        return None, None, None, {}
    devices = await resp.json()

    st_dev = next(
        (
            d
            for d in devices
            if (
                d.get("type", {}).get("category") == "CHARGINGSTATION"
                or d.get("type") == "CHARGINGSTATION"
            )
        ),
        None,
    )
    car_devs = [
        d
        for d in devices
        if (d.get("type", {}).get("category") == "CARCHARGER" or d.get("type") == "CARCHARGER")
    ]
    if not st_dev and not car_devs:
        _LOGGER.info("No chargers at %s (%s); skipping", name, sid)
        return None, None, None, {}
    if not st_dev:
        _LOGGER.warning("No CHARGINGSTATION smartdevice at %s (%s); skipping site", name, sid)
        return None, None, None, {}

    # Station client (uuid/id are mandatory)
    try:
        st_uuid = str(st_dev["uuid"])
        st_id = str(st_dev["id"])
    except KeyError as err:
        _LOGGER.warning("Station device missing key %s at %s; skipping", err, sid)
        return None, None, None, {}

    station_client = SmappeeApiClient(
        oauth_client,
        serial_str,
        st_uuid,
        st_id,
        sid,
        session=session,
        is_station=True,
    )

    # Connector clients
    conn_map: dict[str, SmappeeApiClient] = {}
    for d in car_devs:
        try:
            cuuid = str(d["uuid"])
            cid = str(d["id"])
        except KeyError as err:
            _LOGGER.debug("Skip CARCHARGER with missing key %s at %s", err, sid)
            continue
        conn_map[cuuid] = SmappeeApiClient(
            oauth_client,
            serial_str,
            cuuid,
            cid,
            sid,
            session=session,
            connector_number=d.get("connectorNumber") or d.get("position") or 1,
        )

    # Coordinator
    coordinator = SmappeeCoordinator(
        hass,
        station_client=station_client,
        connector_clients=conn_map,
        update_interval=update_interval,
    )
    await coordinator.async_config_entry_first_refresh()

    # MQTT
    mqtt: SmappeeMqtt | None = None
    if suuid:

        def _on_props(topic: str, payload: dict) -> None:
            coordinator.apply_mqtt_properties(topic, payload)
            if coordinator.data and coordinator.data.station:
                coordinator.data.station.last_mqtt_rx = time.time()
                coordinator.async_set_updated_data(coordinator.data)

        def _on_conn(up: bool) -> None:
            if coordinator.data and coordinator.data.station:
                coordinator.data.station.mqtt_connected = up
                coordinator.data.station.last_mqtt_rx = time.time()
                coordinator.async_set_updated_data(coordinator.data)

        mqtt = SmappeeMqtt(
            service_location_uuid=suuid,
            client_id=f"ha-{hass.data.get(DOMAIN, {}).get('iid', 'x')}-{sid}",
            serial_number=serial_str,
            on_properties=_on_props,
            service_location_id=sid,
            on_connection_change=_on_conn,
        )
        hass.async_create_task(mqtt.start())
        # REST polling disabled
        coordinator.update_interval = None
    else:
        _LOGGER.warning("No serviceLocationUuid for %s; MQTT disabled for this site", sid)

    return coordinator, mqtt, station_client, conn_map


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

    coordinators: dict[int, SmappeeCoordinator] = {}
    mqtt_clients: dict[int, SmappeeMqtt] = {}
    station_clients: dict[int, SmappeeApiClient] = {}
    connector_clients: dict[int, dict[str, SmappeeApiClient]] = {}

    # 2) Prepare each site
    for sl in with_serial:
        coord, mqtt, st_client, conn_map = await _prepare_site(
            hass, session, oauth_client, sl, update_interval
        )
        if not coord or not st_client:
            continue
        sid = sl["serviceLocationId"]
        coordinators[sid] = coord
        station_clients[sid] = st_client
        connector_clients[sid] = conn_map
        if mqtt:
            mqtt_clients[sid] = mqtt

    if not coordinators:
        _LOGGER.error("No Smappee EV stations discovered; aborting setup")
        return False

    # Platforms store
    hass.data[DOMAIN][entry.entry_id] = {
        "api": oauth_client,
        "coordinators": coordinators,  # {sid: coordinator}
        "station_clients": station_clients,  # {sid: station_client}
        "connector_clients": connector_clients,  # {sid: {uuid: client}}
        "mqtt": mqtt_clients,  # {sid: mqtt}
        "_last_options": dict(entry.options),
    }

    # Platforms start
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.data[DOMAIN].get("services_registered", False):
        register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    for _svc in ("set_available", "set_unavailable", "set_brightness"):
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

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    # Stop all MQTT-clients
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
