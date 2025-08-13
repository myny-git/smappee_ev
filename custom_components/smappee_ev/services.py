from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator

_LOGGER = logging.getLogger(__name__)

# ----------------------------
# Helpers to find the right clients
# ----------------------------

def _first_entry_data(hass: HomeAssistant) -> dict | None:
    """
    Get the first active config entry's data from hass.data.
    We support multiple entries, but services will operate on the first one found.
    """
    domain_data = hass.data.get(DOMAIN, {})
    for entry_id, data in domain_data.items():
        if entry_id != "services_registered":  # Skip the marker
            return data
    return None


def get_station_client(hass: HomeAssistant) -> SmappeeApiClient | None:
    """Return the SmappeeApiClient for the station (global actions)."""
    data = _first_entry_data(hass)
    if not data:
        return None
    return data.get("station_client")


def get_connector_client(hass: HomeAssistant, connector_id: int | None) -> SmappeeApiClient | None:
    """
    Return the SmappeeApiClient for a specific connector.
    If connector_id is None and there is only one connector, return that one.
    """
    data = _first_entry_data(hass)
    if not data:
        return None
    connectors: dict[str, SmappeeApiClient] = data.get("connector_clients", {})

    # Match by connector_number if provided
    if connector_id is not None:
        for client in connectors.values():
            if getattr(client, "connector_number", None) == connector_id:
                return client
        return None

    # If no connector_id given and only one connector exists, return it
    if len(connectors) == 1:
        return next(iter(connectors.values()))

    return None

def get_coordinator(hass: HomeAssistant) -> SmappeeCoordinator | None:
    """Return the DataUpdateCoordinator instance for this integration."""
    data = _first_entry_data(hass)
    if not data:
        return None
    return data.get("coordinator")

# ----------------------------
# Generic async service handlers
# ----------------------------

async def async_handle_station_service(
    hass: HomeAssistant,
    call: ServiceCall,
    method_name: str,
    extra_args: dict | None = None,
) -> None:

    client = get_station_client(hass)
    if not client:
        _LOGGER.error("No station client found")
        return

    method = getattr(client, method_name, None)
    if not method:
        _LOGGER.error("Station method '%s' not found", method_name)
        return

    # Execute the station API method
    await method(**(extra_args or {}))

    # Immediately refresh the coordinator so the UI updates without waiting
    coordinator = get_coordinator(hass)
    if coordinator:
        await coordinator.async_request_refresh()


async def async_handle_connector_service(
    hass: HomeAssistant,
    call: ServiceCall,
    method_name: str,
    extra_args: dict | None = None,
) -> None:

    connector_id = call.data.get("connector_id")
    client = get_connector_client(hass, connector_id)
    if not client:
        _LOGGER.error("No matching connector client found for ID: %s", connector_id)
        return

    method = getattr(client, method_name, None)
    if not method:
        _LOGGER.error("Connector method '%s' not found", method_name)
        return

    # Execute the connector API method
    await method(**(extra_args or {}))

    if method_name in ("start_charging", "pause_charging", "stop_charging"):
        client.selected_mode = "NORMAL"

    coordinator = get_coordinator(hass)
    if coordinator:
        await coordinator.async_request_refresh()

# ----------------------------
# Service function wrappers
# ----------------------------

async def handle_set_available(call: ServiceCall) -> None:
    await async_handle_station_service(call.hass, call, "set_available")


async def handle_set_unavailable(call: ServiceCall) -> None:
    await async_handle_station_service(call.hass, call, "set_unavailable")


async def handle_set_brightness(call: ServiceCall) -> None:
    brightness = call.data.get("brightness")
    await async_handle_station_service(
        call.hass, call, "set_brightness", {"brightness": brightness}
    )

async def handle_start_charging(call: ServiceCall) -> None:
    await async_handle_connector_service(
        call.hass, call, "start_charging", {"current": call.data.get("current")}
    )

async def handle_pause_charging(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "pause_charging")


async def handle_stop_charging(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "stop_charging")


async def handle_set_charging_mode(call: ServiceCall) -> None:
    await async_handle_connector_service(
        call.hass,
        call,
        "set_charging_mode",
        {"mode": call.data.get("mode"), "limit": call.data.get("limit")},
    )


async def handle_set_min_surpluspct(call: ServiceCall) -> None:
    await async_handle_connector_service(
        call.hass,
        call,
        "set_min_surpluspct",
        {"min_surpluspct": call.data.get("min_surpluspct")},
    )


# ----------------------------
# Service registration
# ----------------------------

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
