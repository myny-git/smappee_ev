from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .data import RuntimeData

_LOGGER = logging.getLogger(__name__)

# ----------------------------
# Helpers to find the right clients
# ----------------------------


def _iter_loaded_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    return [e for e in hass.config_entries.async_entries(DOMAIN) if e.state.name == "LOADED"]


def _first_runtime(hass: HomeAssistant) -> RuntimeData | None:
    for entry in _iter_loaded_entries(hass):
        try:
            return entry.runtime_data  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover - defensive
            continue
    return None


def _runtime_by_entry_id(hass: HomeAssistant, entry_id: str | None) -> RuntimeData | None:
    if not entry_id:
        return None
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry or entry.state.name != "LOADED":
        return None
    try:
        return entry.runtime_data  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover
        return None


def _find_runtime_for_sid(hass: HomeAssistant, sid: int) -> RuntimeData | None:
    """Return the runtime_data whose sites contains sid (first match)."""
    for entry in _iter_loaded_entries(hass):
        try:
            rd: RuntimeData = entry.runtime_data  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover
            continue
        if sid in rd.sites:
            return rd
    return None


def _only_or_single_sid(sites: dict[int, dict]) -> int | None:
    sids = list(sites.keys())
    return sids[0] if len(sids) == 1 else None


def _resolve_sid(hass: HomeAssistant, call: ServiceCall) -> tuple[RuntimeData | None, int | None]:
    """Return (runtime, sid) based on optional config_entry_id + service_location_id.

    Precedence:
    1. If config_entry_id provided and valid, use that runtime.
    2. Else if service_location_id provided, find runtime containing that sid.
    3. Else fall back to first runtime and single-site inference.
    """
    entry_id = call.data.get("config_entry_id")
    explicit_rt = _runtime_by_entry_id(hass, entry_id)
    sid = call.data.get("service_location_id")
    if explicit_rt:
        if isinstance(sid, int):
            return explicit_rt, sid if sid in explicit_rt.sites else None
        return explicit_rt, _only_or_single_sid(explicit_rt.sites)

    # No explicit entry: if sid given, try locate its runtime
    if isinstance(sid, int):
        rt_for_sid = _find_runtime_for_sid(hass, sid)
        if rt_for_sid:
            return rt_for_sid, sid
        # sid unknown -> fall back to first runtime (invalid sid will be handled later)

    rt = _first_runtime(hass)
    if not rt:
        return None, None
    if isinstance(sid, int):
        return (rt, sid if sid in rt.sites else None)
    return rt, _only_or_single_sid(rt.sites)


def get_station_client(rt: RuntimeData | None, sid: int | None) -> SmappeeApiClient | None:
    if not rt or sid is None:
        return None
    site = rt.sites.get(sid) or {}
    # site {"stations": {st_uuid: {"station_client":..., ...}}}
    stations = (site.get("stations") or {}).values()
    first = next(iter(stations), None)
    return first.get("station_client") if first else None


def get_connector_client(
    rt: RuntimeData | None, sid: int | None, connector_id: int | None
) -> SmappeeApiClient | None:
    if not rt or sid is None:
        return None
    site = rt.sites.get(sid) or {}
    stations = (site.get("stations") or {}).values()
    # Aggregate all connector clients
    conns: list[SmappeeApiClient] = []
    for bucket in stations:
        conns.extend(list((bucket.get("connector_clients") or {}).values()))
    if connector_id is not None:
        for client in conns:
            if getattr(client, "connector_number", None) == connector_id:
                return client
        return None
    if len(conns) == 1:
        return conns[0]
    return None


def _all_coordinators(rt: RuntimeData | None) -> list:
    if not rt:
        return []
    out = []
    for site in rt.sites.values():
        for bucket in (site.get("stations") or {}).values():
            coord = bucket.get("coordinator")
            if coord:
                out.append(coord)
    return out


# ----------------------------
# Generic async service handlers
# ----------------------------


async def async_handle_station_service(
    hass: HomeAssistant,
    call: ServiceCall,
    method_name: str,
    extra_args: dict | None = None,
) -> None:
    rt, sid = _resolve_sid(hass, call)
    client = get_station_client(rt, sid)
    if not client:
        _LOGGER.error(
            "No station client found (config_entry_id=%s, service_location_id=%s)",
            call.data.get("config_entry_id"),
            call.data.get("service_location_id"),
        )
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
    rt, sid = _resolve_sid(hass, call)
    connector_id = call.data.get("connector_id")
    client = get_connector_client(rt, sid, connector_id)
    if not client:
        _LOGGER.error(
            "No matching connector client (config_entry_id=%s, service_location_id=%s, connector_id=%s)",
            call.data.get("config_entry_id"),
            call.data.get("service_location_id"),
            connector_id,
        )
        return

    method = getattr(client, method_name, None)
    if not method:
        _LOGGER.error("Connector method '%s' not found", method_name)
        return

    # Execute the connector API method
    await method(**(extra_args or {}))

    # Mode reset handled by coordinator state logic; client is stateless now.


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


async def register_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Registering Smappee EV services")
    hass.services.async_register(DOMAIN, "start_charging", handle_start_charging)
    hass.services.async_register(DOMAIN, "pause_charging", handle_pause_charging)
    hass.services.async_register(DOMAIN, "stop_charging", handle_stop_charging)
    hass.services.async_register(DOMAIN, "set_charging_mode", handle_set_charging_mode)


async def unregister_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Unregistering Smappee EV services")
    hass.services.async_remove(DOMAIN, "start_charging")
    hass.services.async_remove(DOMAIN, "pause_charging")
    hass.services.async_remove(DOMAIN, "stop_charging")
    hass.services.async_remove(DOMAIN, "set_charging_mode")
