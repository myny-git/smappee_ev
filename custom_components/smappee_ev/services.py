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


def _only_or_single_sid(data: dict) -> int | None:
    """Return the only available service_location_id if exactly one, else None."""
    s = (data.get("coordinators") or {}).keys()
    sids = list(s)
    return sids[0] if len(sids) == 1 else None


def _resolve_sid(hass: HomeAssistant, call: ServiceCall) -> int | None:
    """Decide which site to act on, using call.data['service_location_id'] or single-site fallback."""
    data = _first_entry_data(hass)
    if not data:
        return None
    sid = call.data.get("service_location_id")
    if isinstance(sid, int):
        return sid
    return _only_or_single_sid(data)


def get_station_client(hass: HomeAssistant, sid: int | None) -> SmappeeApiClient | None:
    """Return the station client for a given site."""
    data = _first_entry_data(hass)
    if not data:
        return None
    if sid is None:
        return None
    stations: dict[int, SmappeeApiClient] = data.get("station_clients", {}) or {}
    return stations.get(sid)


def get_connector_client(
    hass: HomeAssistant, sid: int | None, connector_id: int | None
) -> SmappeeApiClient | None:
    """
    Return the connector client within a site. If connector_id is None and there is only
    one connector in that site, return that one.
    """
    data = _first_entry_data(hass)
    if not data or sid is None:
        return None
    all_connectors: dict[int, dict[str, SmappeeApiClient]] = data.get("connector_clients", {}) or {}
    site_conns = all_connectors.get(sid) or {}

    if connector_id is not None:
        for client in site_conns.values():
            if getattr(client, "connector_number", None) == connector_id:
                return client
        return None

    if len(site_conns) == 1:
        return next(iter(site_conns.values()))
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
    sid = _resolve_sid(hass, call)
    client = get_station_client(hass, sid)
    if not client:
        _LOGGER.error("No station client found (service_location_id missing or invalid)")
        return

    method = getattr(client, method_name, None)
    if not method:
        _LOGGER.error("Station method '%s' not found", method_name)
        return

    # Execute the station API method
    await method(**(extra_args or {}))


async def async_handle_connector_service(
    hass: HomeAssistant,
    call: ServiceCall,
    method_name: str,
    extra_args: dict | None = None,
) -> None:
    sid = _resolve_sid(hass, call)
    connector_id = call.data.get("connector_id")
    client = get_connector_client(hass, sid, connector_id)
    if not client:
        _LOGGER.error(
            "No matching connector client found (service_location_id=%s, connector_id=%s)",
            sid,
            connector_id,
        )
        return

    method = getattr(client, method_name, None)
    if not method:
        _LOGGER.error("Connector method '%s' not found", method_name)
        return

    # Execute the connector API method
    await method(**(extra_args or {}))

    if method_name in ("start_charging", "pause_charging", "stop_charging"):
        client.selected_mode = "NORMAL"


# ----------------------------
# Service function wrappers
# ----------------------------


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
        {"mode": call.data.get("mode")},
    )


# ----------------------------
# Service registration
# ----------------------------


def register_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Registering Smappee EV services")

    hass.services.async_register(DOMAIN, "start_charging", handle_start_charging)
    hass.services.async_register(DOMAIN, "pause_charging", handle_pause_charging)
    hass.services.async_register(DOMAIN, "stop_charging", handle_stop_charging)
    hass.services.async_register(DOMAIN, "set_charging_mode", handle_set_charging_mode)


def unregister_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Unregistering Smappee EV services")
    hass.services.async_remove(DOMAIN, "start_charging")
    hass.services.async_remove(DOMAIN, "pause_charging")
    hass.services.async_remove(DOMAIN, "stop_charging")
    hass.services.async_remove(DOMAIN, "set_charging_mode")
