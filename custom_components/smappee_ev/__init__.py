"""Smappee EV Home Assistant integration package."""

import asyncio
import json
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
from .const import (
    BASE_URL,
    CONF_SERIAL,
    CONF_SERVICE_LOCATION_ID,
    CONF_SERVICE_LOCATION_UUID,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    UPDATE_INTERVAL_DEFAULT,
)
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


async def _ensure_service_location_uuid_in_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    oauth_client,
    session: ClientSession,
) -> str | None:
    if entry.data.get(CONF_SERVICE_LOCATION_UUID):
        return entry.data[CONF_SERVICE_LOCATION_UUID]

    sl_id = entry.data.get(CONF_SERVICE_LOCATION_ID)
    serial = str(entry.data.get(CONF_SERIAL, ""))

    # Token
    await oauth_client.ensure_token_valid()
    headers = {
        "Authorization": f"Bearer {oauth_client.access_token}",
        "Content-Type": "application/json",
    }

    url = f"{BASE_URL}/servicelocation"
    try:
        resp = await session.get(url, headers=headers)
    except (TimeoutError, ClientError, asyncio.CancelledError) as err:
        _LOGGER.warning("Failed to call %s: %s", url, err)
        return None

    if resp.status != 200:
        text = await resp.text()
        _LOGGER.warning("GET %s returned %s: %s", url, resp.status, text)
        return None

    try:
        data = await resp.json()
    except (json.JSONDecodeError, ClientError) as err:
        _LOGGER.warning("Invalid JSON from %s: %s", url, err)
        return None

    if isinstance(data, dict) and "serviceLocations" in data:
        items = data["serviceLocations"] or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    match = None

    if sl_id is not None:
        for it in items:
            try:
                if int(it.get("serviceLocationId")) == int(sl_id):
                    match = it
                    break
            except (TypeError, ValueError) as err:
                _LOGGER.debug("Skip serviceLocation (bad id): %r (err=%s)", it, err)
                continue

    if match is None and serial:
        for it in items:
            if str(it.get("deviceSerialNumber", "")).strip() == serial:
                match = it
                break

    slu = (match or {}).get("serviceLocationUuid")
    if not slu:
        _LOGGER.warning("Could not determine service_location_uuid from /servicelocation response")
        return None

    new_data = dict(entry.data)
    new_data[CONF_SERVICE_LOCATION_UUID] = slu
    hass.config_entries.async_update_entry(entry, data=new_data)
    _LOGGER.info("Filled missing service_location_uuid in config entry: %s", slu)
    return slu


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Smappee EV component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Smappee EV config entry with support for multiple connectors."""
    _LOGGER.debug("Setting up entry for Smappee EV. Serial: %s", entry.data.get(CONF_SERIAL))

    hass.data.setdefault(DOMAIN, {})

    # Ensure connectors list is present
    if "carchargers" not in entry.data or "station" not in entry.data:
        _LOGGER.error("Config entry missing connectors or station: %s", entry.data)
        return False

    # Use HA's aiohttp session
    session: ClientSession = async_get_clientsession(hass)

    serial = entry.data[CONF_SERIAL]
    service_location_id = entry.data[CONF_SERVICE_LOCATION_ID]
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_DEFAULT)

    oauth_client = OAuth2Client(entry.data, session=session)
    slu = entry.data.get(CONF_SERVICE_LOCATION_UUID)
    if not slu:
        slu = await _ensure_service_location_uuid_in_entry(hass, entry, oauth_client, session)

    # Station-level client (for LED, availability, etc.)
    st = entry.data["station"]
    station_client = SmappeeApiClient(
        oauth_client,
        serial,
        st["uuid"],  # real station smart_device_uuid
        st["id"],  # real station smart_device_id
        service_location_id,
        session=session,
        is_station=True,
    )

    # await station_client.enable()

    # Connector-level clients (keyed by UUID)
    connector_clients = {}
    for device in entry.data["carchargers"]:
        client = SmappeeApiClient(
            oauth_client,
            serial,
            device["uuid"],
            device["id"],
            service_location_id,
            session=session,
            connector_number=device.get("connector_number"),  # pass through
        )

        # await client.enable()
        connector_clients[device["uuid"]] = client

    # --- Create and refresh coordinator ---
    coordinator = SmappeeCoordinator(
        hass,
        station_client=station_client,
        connector_clients=connector_clients,
        update_interval=update_interval,
    )
    # 1) First REST-snapshot (once)
    await coordinator.async_config_entry_first_refresh()

    # --- Store in hass.data for platforms to use ---
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "station_client": station_client,
        "connector_clients": connector_clients,
    }
    # 2) Live MQTT (if UUID is present)
    slu = entry.data.get(CONF_SERVICE_LOCATION_UUID)

    def _on_props(topic: str, payload: dict) -> None:
        coordinator.apply_mqtt_properties(topic, payload)
        # bump last RX (diagnostic)
        if coordinator.data and coordinator.data.station:
            coordinator.data.station.last_mqtt_rx = time.time()
            coordinator.async_set_updated_data(coordinator.data)

    def _on_conn(up: bool) -> None:
        # store connected/vitals for binary_sensor
        if coordinator.data and coordinator.data.station:
            coordinator.data.station.mqtt_connected = up
            coordinator.data.station.last_mqtt_rx = time.time()
            coordinator.async_set_updated_data(coordinator.data)

    if slu:
        # Serial voor tracking; directly from station_client
        serial_for_tracking = str(getattr(station_client, "serial", serial))

        mqtt = SmappeeMqtt(
            service_location_uuid=slu,
            client_id=f"ha-{entry.entry_id}",
            serial_number=serial_for_tracking,
            on_properties=_on_props,
            service_location_id=service_location_id,
            on_connection_change=_on_conn,
        )
        hass.data[DOMAIN][entry.entry_id]["mqtt"] = mqtt
        # Start async; don't block
        hass.async_create_task(mqtt.start())
    else:
        _LOGGER.warning("No service_location_uuid in config entry; MQTT disabled")

    # 3) disable REST-polling
    coordinator.update_interval = None
    _LOGGER.info("Smappee: REST polling disabled; MQTT will drive updates.")

    # 4) Start platforms

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.data[DOMAIN].get("services_registered", False):
        register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    for _svc in ("set_available", "set_unavailable", "set_brightness"):
        try:
            if hass.services.has_service(DOMAIN, _svc):
                hass.services.async_remove(DOMAIN, _svc)
                _LOGGER.info("Removed deprecated service %s.%s", DOMAIN, _svc)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("While removing deprecated service %s: %s", _svc, err)

    entry.async_on_unload(entry.add_update_listener(async_entry_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Smappee EV config entry."""
    _LOGGER.debug("Unloading Smappee EV config entry: %s", entry.entry_id)

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    mqtt = data.get("mqtt") if data else None
    if mqtt:
        await mqtt.stop()

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
    _LOGGER.debug("Config entry updated: %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
