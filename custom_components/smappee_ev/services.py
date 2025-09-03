from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .api_client import SmappeeApiClient
from .const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT, DOMAIN
from .data import RuntimeData

_LOGGER = logging.getLogger(__name__)

# ----------------------------
# Helpers to find the right clients
# ----------------------------


def _iter_loaded_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    return [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]


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
    if not entry or entry.state is not ConfigEntryState.LOADED:
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
            if sid not in explicit_rt.sites:
                raise ServiceValidationError(
                    f"service_location_id {sid} does not belong to config_entry_id {entry_id}"
                )
            return explicit_rt, sid
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
    if rt and sid is None and len(rt.sites) > 1:
        raise ServiceValidationError(
            "Multiple service locations detected. Provide 'service_location_id'."
        )
    client = get_station_client(rt, sid)
    if not client:
        raise ServiceValidationError(
            f"No station client (config_entry_id={call.data.get('config_entry_id')}, sid={call.data.get('service_location_id')})"
        )

    method = getattr(client, method_name, None)
    if not method:
        raise ServiceValidationError(f"Station method '{method_name}' not found")
    try:
        await method(**(extra_args or {}))
    except Exception as err:
        raise HomeAssistantError(f"Station service '{method_name}' failed: {err}") from err


async def async_handle_connector_service(
    hass: HomeAssistant,
    call: ServiceCall,
    method_name: str,
    extra_args: dict | None = None,
) -> None:
    rt, sid = _resolve_sid(hass, call)
    if rt and sid is None and len(rt.sites) > 1:
        raise ServiceValidationError(
            "Multiple service locations detected. Provide 'service_location_id'."
        )
    connector_id = call.data.get("connector_id")
    client = get_connector_client(rt, sid, connector_id)
    if not client:
        raise ServiceValidationError(
            f"No matching connector client (config_entry_id={call.data.get('config_entry_id')}, sid={call.data.get('service_location_id')}, connector_id={connector_id})"
        )

    method = getattr(client, method_name, None)
    if not method:
        raise ServiceValidationError(f"Connector method '{method_name}' not found")
    try:
        await method(**(extra_args or {}))
    except Exception as err:
        raise HomeAssistantError(f"Connector service '{method_name}' failed: {err}") from err

    # Mode reset handled by coordinator state logic; client is stateless now.


# ----------------------------
# Service function wrappers
# ----------------------------


async def handle_start_charging(call: ServiceCall) -> None:
    current = call.data.get("current")
    if current is not None:
        # Dynamic validation against connector limits
        rt, sid = _resolve_sid(call.hass, call)
        connector_id = call.data.get("connector_id")
        client = get_connector_client(rt, sid, connector_id)
        if not client:
            raise ServiceValidationError(
                "Cannot validate current: connector client not found (provide connector_id if ambiguous)"
            )
        try:
            min_c = int(getattr(client, "min_current", DEFAULT_MIN_CURRENT))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            min_c = DEFAULT_MIN_CURRENT
        try:
            max_c = int(getattr(client, "max_current", DEFAULT_MAX_CURRENT))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            max_c = DEFAULT_MAX_CURRENT
        if max_c < min_c:
            max_c = min_c
        if current < min_c or current > max_c:
            raise ServiceValidationError(
                f"current {current} A out of range {min_c}-{max_c} A for this connector"
            )
    await async_handle_connector_service(call.hass, call, "start_charging", {"current": current})


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


START_CHARGING_SCHEMA = vol.Schema({
    vol.Optional("config_entry_id"): cv.string,
    vol.Optional("service_location_id"): cv.positive_int,
    vol.Optional("connector_id"): cv.positive_int,
    # Only enforce a sane minimum; device specific max validated dynamically in handler
    vol.Optional("current"): vol.All(vol.Coerce(int), vol.Range(min=6)),
})

PAUSE_STOP_SCHEMA = vol.Schema({
    vol.Optional("config_entry_id"): cv.string,
    vol.Optional("service_location_id"): cv.positive_int,
    vol.Optional("connector_id"): cv.positive_int,
})

SET_MODE_SCHEMA = vol.Schema({
    vol.Optional("config_entry_id"): cv.string,
    vol.Optional("service_location_id"): cv.positive_int,
    vol.Optional("connector_id"): cv.positive_int,
    vol.Required("mode"): vol.All(
        str,
        lambda s: s.upper(),  # normalize to uppercase
        vol.In({"NORMAL", "STANDARD", "SMART", "SOLAR"}),
    ),
})


async def register_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Registering Smappee EV services")
    hass.services.async_register(
        DOMAIN, "start_charging", handle_start_charging, START_CHARGING_SCHEMA
    )
    hass.services.async_register(DOMAIN, "pause_charging", handle_pause_charging, PAUSE_STOP_SCHEMA)
    hass.services.async_register(DOMAIN, "stop_charging", handle_stop_charging, PAUSE_STOP_SCHEMA)
    hass.services.async_register(
        DOMAIN, "set_charging_mode", handle_set_charging_mode, SET_MODE_SCHEMA
    )


async def unregister_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Unregistering Smappee EV services")
    hass.services.async_remove(DOMAIN, "start_charging")
    hass.services.async_remove(DOMAIN, "pause_charging")
    hass.services.async_remove(DOMAIN, "stop_charging")
    hass.services.async_remove(DOMAIN, "set_charging_mode")
