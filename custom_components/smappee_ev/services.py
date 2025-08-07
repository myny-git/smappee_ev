import logging
import asyncio

from homeassistant.core import HomeAssistant, ServiceCall
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

def get_station_client(hass: HomeAssistant):
    for entry_id, data in hass.data[DOMAIN].items():
        if entry_id != "services_registered":
            return data.get("station")
    return None


def get_connector_client(hass: HomeAssistant, connector_id: int | None):
    for entry_id, data in hass.data[DOMAIN].items():
        if entry_id == "services_registered":
            continue
        connectors = data.get("connectors", {})
        if connector_id is not None:
            for uuid, client in connectors.items():
                if getattr(client, "connector_number", None) == connector_id:
                    return client
        elif len(connectors) == 1:
            return next(iter(connectors.values()))
    return None


async def async_handle_station_service(hass: HomeAssistant, call: ServiceCall, method_name: str) -> None:
    client = get_station_client(hass)
    if not client:
        _LOGGER.error("No station client found")
        return
    if method := getattr(client, method_name, None):
        await method()


async def async_handle_connector_service(hass: HomeAssistant, call: ServiceCall, method_name: str, extra_args=None) -> None:
    connector_id = call.data.get("connector_id")
    client = get_connector_client(hass, connector_id)
    if not client:
        _LOGGER.error("No matching connector client found for ID: %s", connector_id)
        return
    if method := getattr(client, method_name, None):
        await method(**(extra_args or {}))


# Registering all services centrally

async def handle_set_available(call: ServiceCall) -> None:
    await async_handle_station_service(call.hass, call, "set_available")


async def handle_set_unavailable(call: ServiceCall) -> None:
    await async_handle_station_service(call.hass, call, "set_unavailable")


async def handle_set_brightness(call: ServiceCall) -> None:
    await async_handle_station_service(call.hass, call, "set_brightness")


async def handle_start_charging(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "start_charging_current", {"current": call.data.get("current")})


async def handle_pause_charging(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "pause_charging")


async def handle_stop_charging(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "stop_charging")


async def handle_set_charging_mode(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "set_charging_mode", {
        "mode": call.data.get("mode"),
        "limit": call.data.get("limit")
    })


async def handle_set_min_surpluspct(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "set_min_surpluspct", {
        "min_surpluspct": call.data.get("min_surpluspct")
    })


async def handle_set_percentage_limit(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "set_percentage_limit", {
        "percentage": call.data.get("percentage")
    })


async def handle_start_charging_current(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "start_charging_current", {
        "current": call.data.get("current")
    })


async def handle_reload(call: ServiceCall) -> None:
    _LOGGER.info("Service: reload – reloading all Smappee EV entries")
    hass = call.hass
    entries = hass.config_entries.async_entries(DOMAIN)
    await asyncio.gather(*(hass.config_entries.async_reload(e.entry_id) for e in entries))


def register_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Registering Smappee EV services")

    hass.services.async_register(DOMAIN, "set_available", handle_set_available)
    hass.services.async_register(DOMAIN, "set_unavailable", handle_set_unavailable)
    hass.services.async_register(DOMAIN, "set_brightness", handle_set_brightness)
    hass.services.async_register(DOMAIN, "start_charging", handle_start_charging)
    hass.services.async_register(DOMAIN, "pause_charging", handle_pause_charging)
    hass.services.async_register(DOMAIN, "stop_charging", handle_stop_charging)
    hass.services.async_register(DOMAIN, "set_charging_mode", handle_set_charging_mode)
    hass.services.async_register(DOMAIN, "set_min_surpluspct", handle_set_min_surpluspct)
    hass.services.async_register(DOMAIN, "set_percentage_limit", handle_set_percentage_limit)
    hass.services.async_register(DOMAIN, "start_charging_current", handle_start_charging_current)
    hass.services.async_register(DOMAIN, "reload", handle_reload)    


def unregister_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Unregistering Smappee EV services")
    hass.services.async_remove(DOMAIN, "set_available")
    hass.services.async_remove(DOMAIN, "set_unavailable")
    hass.services.async_remove(DOMAIN, "set_brightness")
    hass.services.async_remove(DOMAIN, "start_charging")
    hass.services.async_remove(DOMAIN, "pause_charging")
    hass.services.async_remove(DOMAIN, "stop_charging")
    hass.services.async_remove(DOMAIN, "set_charging_mode")
    hass.services.async_remove(DOMAIN, "set_min_surpluspct")
    hass.services.async_remove(DOMAIN, "set_percentage_limit")
    hass.services.async_remove(DOMAIN, "start_charging_current")
    hass.services.async_remove(DOMAIN, "reload")    
