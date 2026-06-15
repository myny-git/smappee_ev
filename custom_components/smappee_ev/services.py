from __future__ import annotations

import logging
from typing import cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import CHARGING_MODES, DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT, DOMAIN
from .data import ConnectorState, RuntimeData, SmappeeEvConfigEntry
from .device_handle import SmappeeDeviceHandle

_LOGGER = logging.getLogger(__name__)

# ----------------------------
# Exception helpers
# ----------------------------


def _placeholder(value: object) -> str:
    return "" if value is None else str(value)


def _translation_placeholders(**values: object) -> dict[str, str]:
    return {key: _placeholder(value) for key, value in values.items()}


def _service_validation_error(
    message: str, translation_key: str, **placeholders: object
) -> ServiceValidationError:
    return ServiceValidationError(
        message,
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=_translation_placeholders(**placeholders),
    )


def _home_assistant_error(
    message: str, translation_key: str, **placeholders: object
) -> HomeAssistantError:
    return HomeAssistantError(
        message,
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=_translation_placeholders(**placeholders),
    )


# ----------------------------
# Helpers to find the right clients
# ----------------------------


def _iter_loaded_entries(hass: HomeAssistant) -> list[SmappeeEvConfigEntry]:
    return [
        cast(SmappeeEvConfigEntry, entry)
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _first_runtime(hass: HomeAssistant) -> RuntimeData | None:
    for entry in _iter_loaded_entries(hass):
        return entry.runtime_data
    return None


def _runtime_by_entry_id(hass: HomeAssistant, entry_id: str | None) -> RuntimeData | None:
    if not entry_id:
        return None
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry or entry.state is not ConfigEntryState.LOADED:
        return None
    return cast(SmappeeEvConfigEntry, entry).runtime_data


def _find_runtime_for_sid(hass: HomeAssistant, sid: int) -> RuntimeData | None:
    """Return the runtime_data whose sites contains sid (first match)."""
    for entry in _iter_loaded_entries(hass):
        rd = entry.runtime_data
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
    if entry_id and explicit_rt is None:
        raise _service_validation_error(
            f"Config entry {entry_id} is not loaded or does not exist",
            "config_entry_not_loaded",
            config_entry_id=entry_id,
        )
    if explicit_rt:
        if isinstance(sid, int):
            if sid not in explicit_rt.sites:
                raise _service_validation_error(
                    f"service_location_id {sid} does not belong to config_entry_id {entry_id}",
                    "service_location_not_in_entry",
                    service_location_id=sid,
                    config_entry_id=entry_id,
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


def get_station_client(rt: RuntimeData | None, sid: int | None) -> SmappeeDeviceHandle | None:
    if not rt or sid is None:
        return None
    site = rt.sites.get(sid) or {}
    # site {"stations": {st_uuid: {"station_client":..., ...}}}
    stations = (site.get("stations") or {}).values()
    first = next(iter(stations), None)
    return first.get("station_client") if first else None


def _connector_clients_for_site(site: dict) -> list[SmappeeDeviceHandle]:
    conns: list[SmappeeDeviceHandle] = []
    for bucket in (site.get("stations") or {}).values():
        conns.extend(list((bucket.get("connector_clients") or {}).values()))
    return conns


def get_connector_client(
    rt: RuntimeData | None, sid: int | None, connector_id: int | None
) -> SmappeeDeviceHandle | None:
    if not rt or sid is None:
        return None
    site = rt.sites.get(sid) or {}
    conns = _connector_clients_for_site(site)
    if connector_id is not None:
        for client in conns:
            if getattr(client, "connector_number", None) == connector_id:
                return client
        return None
    if len(conns) == 1:
        return conns[0]
    return None


def _client_station_serial(client: SmappeeDeviceHandle) -> str:
    return str(
        getattr(client, "charging_station_serial", None) or getattr(client, "serial", "") or ""
    ).strip()


def get_api2_connector_client(hass: HomeAssistant, call: ServiceCall) -> SmappeeDeviceHandle | None:
    """Resolve an API 2 connector using charging station serial and connector position.

    API 2 uses /chargingstations/{serial}/connectors/{position}/mode, so service
    location id is not used for target selection.
    """

    entry_id = call.data.get("config_entry_id")
    connector_id = call.data.get("connector_id")
    station_serial = str(call.data.get("charging_station_serial") or "").strip().casefold()
    if connector_id is None:
        return None

    explicit_rt = _runtime_by_entry_id(hass, entry_id)
    if explicit_rt:
        runtimes = [explicit_rt]
    else:
        runtimes = []
        for entry in _iter_loaded_entries(hass):
            runtimes.append(entry.runtime_data)

    matches: list[SmappeeDeviceHandle] = []
    seen: set[int] = set()
    for rt in runtimes:
        if not rt:
            continue
        for site in rt.sites.values():
            if not site:
                continue
            for client in _connector_clients_for_site(site):
                if station_serial and _client_station_serial(client).casefold() != station_serial:
                    continue
                if (
                    connector_id is not None
                    and getattr(client, "connector_number", None) != connector_id
                ):
                    continue
                marker = id(client)
                if marker in seen:
                    continue
                seen.add(marker)
                matches.append(client)

    if len(matches) == 1:
        return matches[0]
    return None


def _get_connector_state(
    rt: RuntimeData | None, client: SmappeeDeviceHandle
) -> ConnectorState | None:
    """Return the live ConnectorState for *client* from its coordinator, or None."""
    if not rt:
        return None
    uuid = getattr(client, "smart_device_uuid", None)
    if not uuid:
        return None
    for site in rt.sites.values():
        for bucket in (site.get("stations") or {}).values():
            coord = bucket.get("coordinator")
            if coord and coord.data:
                conn = coord.data.connectors.get(uuid)
                if conn is not None:
                    return conn
    return None


def _schedule_dashboard_refresh_for_client(
    hass: HomeAssistant, client: SmappeeDeviceHandle
) -> None:
    """Schedule the owning coordinator to refresh slow Dashboard data after a write."""
    client_uuid = getattr(client, "smart_device_uuid", None)
    for entry in _iter_loaded_entries(hass):
        for site in entry.runtime_data.sites.values():
            for bucket in (site.get("stations") or {}).values():
                coord = bucket.get("coordinator")
                if coord is None:
                    continue
                if bucket.get("station_client") is client:
                    coord.async_schedule_dashboard_refresh()
                    return
                if client in (bucket.get("connector_clients") or {}).values():
                    coord.async_schedule_dashboard_refresh()
                    return
                if client_uuid and client_uuid in getattr(coord.data, "connectors", {}):
                    coord.async_schedule_dashboard_refresh()
                    return


def _connector_current_range(
    rt: RuntimeData | None,
    client: SmappeeDeviceHandle,
) -> tuple[int, int]:
    """Return (min_current, max_current) from live ConnectorState, or defaults."""
    conn_state = _get_connector_state(rt, client)
    min_c = conn_state.min_current if conn_state else DEFAULT_MIN_CURRENT
    max_c = conn_state.max_current if conn_state else DEFAULT_MAX_CURRENT
    if max_c < min_c:
        max_c = min_c
    return min_c, max_c


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
        raise _service_validation_error(
            "Multiple service locations detected. Provide 'service_location_id'.",
            "multiple_service_locations",
        )
    client = get_station_client(rt, sid)
    if not client:
        raise _service_validation_error(
            f"No station client (config_entry_id={call.data.get('config_entry_id')}, sid={call.data.get('service_location_id')})",
            "no_station_client",
            config_entry_id=call.data.get("config_entry_id"),
            service_location_id=call.data.get("service_location_id"),
        )

    method = getattr(client, method_name, None)
    if not method:
        raise _service_validation_error(
            f"Station method '{method_name}' not found",
            "station_method_not_found",
            method_name=method_name,
        )
    try:
        await method(**(extra_args or {}))
    except Exception as err:
        raise _home_assistant_error(
            f"Station service '{method_name}' failed: {err}",
            "station_service_failed",
            method_name=method_name,
            error=err,
        ) from err
    _schedule_dashboard_refresh_for_client(hass, client)


async def async_handle_connector_service(
    hass: HomeAssistant,
    call: ServiceCall,
    method_name: str,
    extra_args: dict | None = None,
) -> None:
    rt, sid = _resolve_sid(hass, call)
    if rt and sid is None and len(rt.sites) > 1:
        raise _service_validation_error(
            "Multiple service locations detected. Provide 'service_location_id'.",
            "multiple_service_locations",
        )
    connector_id = call.data.get("connector_id")
    client = get_connector_client(rt, sid, connector_id)
    if not client:
        raise _service_validation_error(
            f"No matching connector client (config_entry_id={call.data.get('config_entry_id')}, sid={call.data.get('service_location_id')}, connector_id={connector_id})",
            "no_connector_client",
            config_entry_id=call.data.get("config_entry_id"),
            service_location_id=call.data.get("service_location_id"),
            connector_id=connector_id,
        )

    method = getattr(client, method_name, None)
    if not method:
        raise _service_validation_error(
            f"Connector method '{method_name}' not found",
            "connector_method_not_found",
            method_name=method_name,
        )
    try:
        await method(**(extra_args or {}))
    except Exception as err:
        raise _home_assistant_error(
            f"Connector service '{method_name}' failed: {err}",
            "connector_service_failed",
            method_name=method_name,
            error=err,
        ) from err
    _schedule_dashboard_refresh_for_client(hass, client)

    # Mode reset handled by coordinator state logic; client is stateless now.


async def async_handle_connector_service_api2(
    hass: HomeAssistant,
    call: ServiceCall,
    method_name: str,
    extra_args: dict | None = None,
) -> None:
    client = get_api2_connector_client(hass, call)
    if not client:
        message = (
            "No matching API 2 connector client "
            f"(config_entry_id={call.data.get('config_entry_id')}, "
            f"charging_station_serial={call.data.get('charging_station_serial')}, "
            f"connector_id={call.data.get('connector_id')}). "
            "Provide 'charging_station_serial' if multiple charging stations share the same connector position."
        )
        raise _service_validation_error(
            message,
            "no_api2_connector_client",
            config_entry_id=call.data.get("config_entry_id"),
            charging_station_serial=call.data.get("charging_station_serial"),
            connector_id=call.data.get("connector_id"),
        )

    method = getattr(client, method_name, None)
    if not method:
        raise _service_validation_error(
            f"Connector method '{method_name}' not found",
            "connector_method_not_found",
            method_name=method_name,
        )
    try:
        await method(**(extra_args or {}))
    except Exception as err:
        raise _home_assistant_error(
            f"Connector service '{method_name}' failed: {err}",
            "connector_service_failed",
            method_name=method_name,
            error=err,
        ) from err
    _schedule_dashboard_refresh_for_client(hass, client)


# ----------------------------
# Service function wrappers
# ----------------------------


async def handle_start_charging(call: ServiceCall) -> None:
    current = call.data.get("current")
    connector_id = call.data.get("connector_id")
    rt, sid = _resolve_sid(call.hass, call)
    client = get_connector_client(rt, sid, connector_id)

    if not client:
        raise _service_validation_error(
            "Cannot resolve connector client (provide connector_id if ambiguous)",
            "cannot_resolve_connector_client",
        )

    min_c, max_c = _connector_current_range(rt, client)

    if current is None:
        # Default to the connector's configured minimum
        current = min_c
    elif current < min_c or current > max_c:
        raise _service_validation_error(
            f"current {current} A out of range {min_c}-{max_c} A for this connector",
            "current_out_of_range",
            current=current,
            min_current=min_c,
            max_current=max_c,
        )

    await async_handle_connector_service(
        call.hass,
        call,
        "start_charging",
        {"current": current, "min_current": min_c, "max_current": max_c},
    )


async def handle_pause_charging(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "pause_charging")


async def handle_pause_charging_chargingstations(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "pause_charging_chargingstations")


async def handle_stop_charging(call: ServiceCall) -> None:
    await async_handle_connector_service(call.hass, call, "stop_charging")


async def handle_set_charging_mode(call: ServiceCall) -> None:
    await async_handle_connector_service(
        call.hass,
        call,
        "set_charging_mode",
        {"mode": call.data.get("mode")},
    )


async def handle_set_charging_mode_chargingstations(call: ServiceCall) -> None:
    await async_handle_connector_service_api2(
        call.hass,
        call,
        "set_charging_mode_chargingstations",
        {
            "mode": call.data.get("mode"),
            "limit": call.data.get("limit"),
            "limit_unit": call.data.get("limit_unit"),
            "connector": call.data.get("connector_id"),
        },
    )


async def handle_set_current(call: ServiceCall) -> None:
    current = round(float(call.data["current"]), 1)
    connector_id = call.data.get("connector_id")
    rt, sid = _resolve_sid(call.hass, call)
    client = get_connector_client(rt, sid, connector_id)
    if not client:
        raise _service_validation_error(
            f"No matching connector client (config_entry_id={call.data.get('config_entry_id')}, "
            f"sid={call.data.get('service_location_id')}, connector_id={connector_id})",
            "no_connector_client",
            config_entry_id=call.data.get("config_entry_id"),
            service_location_id=call.data.get("service_location_id"),
            connector_id=connector_id,
        )
    min_c, max_c = _connector_current_range(rt, client)
    if current < float(min_c) or current > float(max_c):
        raise _service_validation_error(
            f"current {current} A out of range {min_c}-{max_c} A for this connector",
            "current_out_of_range",
            current=current,
            min_current=min_c,
            max_current=max_c,
        )
    await async_handle_connector_service(
        call.hass,
        call,
        "set_current",
        {"current": current, "min_current": min_c, "max_current": max_c},
    )


# ----------------------------
# Service registration
# ----------------------------


START_CHARGING_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("service_location_id"): cv.positive_int,
        vol.Optional("connector_id"): cv.positive_int,
        # Only enforce a sane minimum; device specific max validated dynamically in handler
        vol.Optional("current"): vol.All(vol.Coerce(int), vol.Range(min=6)),
    }
)

PAUSE_STOP_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("service_location_id"): cv.positive_int,
        vol.Optional("connector_id"): cv.positive_int,
    }
)

SET_MODE_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("service_location_id"): cv.positive_int,
        vol.Optional("connector_id"): cv.positive_int,
        vol.Required("mode"): vol.All(
            str,
            str.upper,  # normalize to uppercase
            vol.In(CHARGING_MODES),
        ),
    }
)


SET_CURRENT_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("service_location_id"): cv.positive_int,
        vol.Optional("connector_id"): cv.positive_int,
        # Sane lower bound; upper bound validated dynamically against connector limits
        vol.Required("current"): vol.All(vol.Coerce(float), vol.Range(min=1.0)),
    }
)

SET_MODE_API2_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("charging_station_serial"): vol.Any(
            vol.All(str, str.strip),
            vol.All(vol.Coerce(int), vol.Coerce(str)),
        ),
        vol.Required("connector_id"): cv.positive_int,
        vol.Required("mode"): vol.All(
            str,
            str.upper,
            vol.In({"NORMAL", "SMART", "PAUSED"}),
        ),
        vol.Optional("limit"): vol.Coerce(int),
        vol.Optional("limit_unit", default="AMPERE"): vol.All(
            str,
            str.upper,
            vol.In({"AMPERE", "PERCENTAGE"}),
        ),
    }
)


async def register_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Registering Smappee EV services")
    hass.services.async_register(
        DOMAIN, "start_charging", handle_start_charging, START_CHARGING_SCHEMA
    )
    hass.services.async_register(DOMAIN, "pause_charging", handle_pause_charging, PAUSE_STOP_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        "pause_charging_chargingstations",
        handle_pause_charging_chargingstations,
        PAUSE_STOP_SCHEMA,
    )
    hass.services.async_register(DOMAIN, "stop_charging", handle_stop_charging, PAUSE_STOP_SCHEMA)
    hass.services.async_register(
        DOMAIN, "set_charging_mode", handle_set_charging_mode, SET_MODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "set_charging_mode_chargingstations",
        handle_set_charging_mode_chargingstations,
        SET_MODE_API2_SCHEMA,
    )
    hass.services.async_register(DOMAIN, "set_current", handle_set_current, SET_CURRENT_SCHEMA)


async def unregister_services(hass: HomeAssistant) -> None:
    _LOGGER.info("Unregistering Smappee EV services")
    hass.services.async_remove(DOMAIN, "start_charging")
    hass.services.async_remove(DOMAIN, "pause_charging")
    hass.services.async_remove(DOMAIN, "pause_charging_chargingstations")
    hass.services.async_remove(DOMAIN, "stop_charging")
    hass.services.async_remove(DOMAIN, "set_charging_mode")
    hass.services.async_remove(DOMAIN, "set_charging_mode_chargingstations")
    hass.services.async_remove(DOMAIN, "set_current")
