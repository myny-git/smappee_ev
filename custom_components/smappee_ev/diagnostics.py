"""Diagnostics support for Smappee EV integration.

Provides redacted runtime information for troubleshooting via HA UI.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .data import RuntimeData, SmappeeEvConfigEntry

REDACT_KEYS = {
    "access_token",
    "charging_station_serial",
    "client_id",
    "client_secret",
    "connector_uuid",
    "dashboard_refresh_token",
    "dashboard_device_id",
    "dashboard_device_uuid",
    "deviceSerialNumber",
    "gateway_serial",
    "password",
    "refresh_token",
    "serial",
    "serial_id",
    "serial_number",
    "serviceLocationUuid",
    "service_location_uuid",
    "site_serial_number",
    "site_uuid",
    "smart_device_id",
    "smart_device_uuid",
    "station_serial",
    "station_uuid",
    "token_type",
    "scope",
    "username",
    "expires_in",
}


def _obfuscate(value: object, *, keep: int = 4) -> str | None:
    """Obfuscate stable identifiers while preserving enough shape for debugging."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= keep * 2:
        return f"{text[:1]}***{text[-1:]}" if len(text) > 1 else "***"
    return f"{text[:keep]}...{text[-keep:]}"


def _safe_len(value: object) -> int:
    """Return len(value) for containers, otherwise 0."""
    try:
        return len(value) if value is not None else 0  # type: ignore[arg-type]
    except TypeError:
        return 0


def _safe_sorted(values: object) -> list[Any]:
    """Return stable sorted list for JSON diagnostics."""
    if not isinstance(values, list | tuple | set):
        return []
    return sorted(values, key=_sort_as_text)


def _redact_text_values(text: object, values: list[object]) -> object:
    """Redact known sensitive values that may be embedded in free text."""
    if not isinstance(text, str):
        return text
    redacted = text
    for value in values:
        if value is None:
            continue
        secret = str(value)
        if secret:
            redacted = redacted.replace(secret, "**REDACTED**")
    return redacted


def _entry_sensitive_values(entry: SmappeeEvConfigEntry) -> list[object]:
    values: list[object] = []
    for source in (getattr(entry, "data", {}), getattr(entry, "options", {})):
        for key, value in dict(source).items():
            if key in REDACT_KEYS:
                values.append(value)
    return values


def _sort_as_text(item: object) -> str:
    """Return a stable text key for mixed JSON-ish values."""
    return str(item)


def _handle_info(client: object | None) -> dict[str, Any]:
    """Return redacted SmappeeDeviceHandle-like metadata."""
    if client is None:
        return {}
    return {
        "service_location_id": getattr(client, "service_location_id", None),
        "serial": _obfuscate(getattr(client, "serial", None)),
        "serial_id": _obfuscate(getattr(client, "serial_id", None)),
        "charging_station_serial": _obfuscate(getattr(client, "charging_station_serial", None)),
        "smart_device_uuid": _obfuscate(getattr(client, "smart_device_uuid", None)),
        "smart_device_id": _obfuscate(getattr(client, "smart_device_id", None)),
        "dashboard_device_id": _obfuscate(getattr(client, "dashboard_device_id", None)),
        "connector_number": getattr(client, "connector_number", None),
        "is_station": getattr(client, "is_station", None),
    }


def _mqtt_info(mqtt_obj: object | None) -> dict[str, Any]:
    """Return redacted MQTT client configuration details."""
    if mqtt_obj is None:
        return {"configured": False}
    if isinstance(mqtt_obj, list | tuple):
        clients = list(mqtt_obj)
        return {
            "configured": bool(clients),
            "client_count": len(clients),
            "clients": [_mqtt_info(client) for client in clients],
        }
    specs = getattr(mqtt_obj, "_mqtt_specs", None) or []
    return {
        "configured": True,
        "service_location_uuid": _obfuscate(getattr(mqtt_obj, "_slu", None)),
        "service_location_id": getattr(mqtt_obj, "_slu_id", None),
        "client_id": _obfuscate(getattr(mqtt_obj, "_client_id", None)),
        "serial_number": _obfuscate(getattr(mqtt_obj, "_serial", None)),
        "spec_count": _safe_len(specs),
        "service_location_uuids": [_obfuscate(slu) for slu in getattr(mqtt_obj, "_slus", ()) or ()],
        "specs": [
            {
                "service_location_id": getattr(spec, "service_location_id", None),
                "role": getattr(spec, "role", None),
                "metric": getattr(spec, "metric", None),
                "topic": _obfuscate(getattr(spec, "topic", None), keep=18),
                "username_present": bool(getattr(spec, "username", None)),
                "password_present": bool(getattr(spec, "password", None)),
                "aspect_path_count": _safe_len(getattr(spec, "aspect_paths", None)),
                "aspect_paths": getattr(spec, "aspect_paths", None) or [],
            }
            for spec in specs
        ],
    }


def _dashboard_info(rt: RuntimeData | None) -> dict[str, Any]:
    """Return redacted Dashboard client availability metadata."""
    dashboard = getattr(rt, "dashboard", None) if rt else None
    if dashboard is None:
        dashboard = getattr(rt, "api", None) if rt else None
    if dashboard is None:
        return {"configured": False}
    return {
        "configured": True,
        "client_type": type(dashboard).__name__,
        "username_present": bool(getattr(dashboard, "username", None)),
        "password_present": bool(getattr(dashboard, "password", None)),
        "refresh_token_present": bool(getattr(dashboard, "refresh_token", None)),
        "access_token_present": bool(getattr(dashboard, "_token", None)),
        "token_expires_at_present": bool(getattr(dashboard, "_token_expires_at_ms", None)),
    }


def _mqtt_client_count(mqtt_by_site: object) -> int:
    """Return total MQTT client count across all runtime site buckets."""
    if not isinstance(mqtt_by_site, dict):
        return 0
    count = 0
    for value in mqtt_by_site.values():
        if isinstance(value, list | tuple):
            count += len(value)
        elif value is not None:
            count += 1
    return count


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    out: dict[str, Any] = {}

    rt: RuntimeData | None = getattr(entry, "runtime_data", None)
    sites = getattr(rt, "sites", {}) if rt else {}
    # Stable ordering for diffs / logs
    out["sites"] = sorted(sites.keys(), key=_sort_as_text)

    out["config_entry_data"] = async_redact_data(dict(entry.data), REDACT_KEYS)
    out["options"] = async_redact_data(dict(entry.options), REDACT_KEYS)

    # Meta
    manifest_version: str | None = None
    try:  # best effort
        integration = await async_get_integration(hass, entry.domain)
        manifest_version = getattr(integration, "version", None)
    except Exception:  # noqa: BLE001
        manifest_version = None

    state_name = None
    try:
        st = getattr(entry, "state", None)
        state_name = getattr(st, "name", None) or str(st) if st is not None else None
    except Exception:  # noqa: BLE001
        state_name = None

    out["meta"] = {
        "entry_id": entry.entry_id,
        "title": _redact_text_values(entry.title, _entry_sensitive_values(entry)),
        "state": state_name,
        "domain": entry.domain,
        "version_manifest": manifest_version,
        "service_locations_total": len(sites or {}),
        "mqtt_clients_total": _mqtt_client_count(getattr(rt, "mqtt", {}) if rt else {}),
        "stations_total": 0,  # filled later
        "connectors_total": 0,  # filled later
        "connector_states_total": 0,  # filled later
        "recent_sessions_total": 0,  # filled later
    }

    stations_out: list[dict[str, Any]] = []
    connectors_out: list[dict[str, Any]] = []
    sites_detail: list[dict[str, Any]] = []

    def _station_connected(bucket: dict) -> bool:
        coord = bucket.get("coordinator")
        if not coord or not getattr(coord, "data", None):
            return False
        st = getattr(coord.data, "station", None)
        return bool(getattr(st, "mqtt_connected", False))

    for site_id, site in (sites or {}).items():
        site_obj = site or {}
        stations = site_obj.get("stations", {})
        # Defensive: RuntimeData.mqtt may contain one or more SmappeeMqtt clients per site.
        mqtt_obj = (getattr(rt, "mqtt", {}) or {}).get(site_id) if rt else None
        # Aggregate counts
        station_count = len(stations)
        connector_count = sum(
            len((b or {}).get("connector_clients", {})) for b in stations.values()
        )
        # derive mqtt_connected aggregate (any station shows connected)
        mqtt_connected_any = any(_station_connected(b) for b in (stations or {}).values())

        sites_detail.append(
            {
                "service_location_id": site_id,
                "service_location_id_obfuscated": _obfuscate(site_id),
                "name_present": site_obj.get("name") is not None,
                "uuid": _obfuscate(site_obj.get("serviceLocationUuid")),
                "serial": _obfuscate(site_obj.get("deviceSerialNumber")),
                "control_location_ids": _safe_sorted(site_obj.get("controlLocationIds")),
                "measurement_location_ids": _safe_sorted(site_obj.get("measurementLocationIds")),
                "station_count": station_count,
                "connector_count": connector_count,
                "mqtt_configured": mqtt_obj is not None,
                "mqtt_connected_any": mqtt_connected_any,
                "mqtt": _mqtt_info(mqtt_obj),
            }
        )
        # per-station and connectors inside same site loop
        for st_uuid, bucket in (stations or {}).items():
            coord = bucket.get("coordinator")
            st_client = bucket.get("station_client")
            connector_clients = bucket.get("connector_clients") or {}
            data = getattr(coord, "data", None) if coord else None
            st = data.station if data else None
            stations_out.append(
                {
                    "service_location_id": site_id,
                    "station_uuid": _obfuscate(st_uuid),
                    "station_handle": _handle_info(st_client),
                    "available": getattr(st, "available", None) if st else None,
                    "api_available": getattr(st, "api_available", None) if st else None,
                    "dashboard_available": getattr(st, "dashboard_available", None) if st else None,
                    "led_brightness": getattr(st, "led_brightness", None) if st else None,
                    "dashboard_led_device_id": _obfuscate(
                        getattr(st, "dashboard_led_device_id", None) if st else None
                    ),
                    "grid_power_total": getattr(st, "grid_power_total", None) if st else None,
                    "pv_power_total": getattr(st, "pv_power_total", None) if st else None,
                    "house_consumption_power": getattr(st, "house_consumption_power", None)
                    if st
                    else None,
                    "mqtt_connected": getattr(st, "mqtt_connected", None) if st else None,
                    "last_mqtt_rx": getattr(st, "last_mqtt_rx", None) if st else None,
                    "connector_client_count": len(connector_clients),
                    "connector_state_count": len((data.connectors or {}) if data else {}),
                }
            )
            state_by_uuid = (data.connectors or {}) if data else {}
            for cuuid, client in connector_clients.items():
                cstate = state_by_uuid.get(cuuid)
                connectors_out.append(
                    {
                        "service_location_id": site_id,
                        "station_uuid": _obfuscate(st_uuid),
                        "connector_uuid": _obfuscate(cuuid),
                        "connector_handle": _handle_info(client),
                        "has_state": cstate is not None,
                        "connector_number": getattr(cstate, "connector_number", None)
                        if cstate
                        else getattr(client, "connector_number", None),
                        "available": getattr(cstate, "available", None) if cstate else None,
                        "api_available": getattr(cstate, "api_available", None) if cstate else None,
                        "session_state": getattr(cstate, "session_state", None) if cstate else None,
                        "session_cause": getattr(cstate, "session_cause", None) if cstate else None,
                        "stopped_by_cloud": getattr(cstate, "stopped_by_cloud", None)
                        if cstate
                        else None,
                        "raw_charging_mode": getattr(cstate, "raw_charging_mode", None)
                        if cstate
                        else None,
                        "optimization_strategy": getattr(cstate, "optimization_strategy", None)
                        if cstate
                        else None,
                        "ui_mode_base": getattr(cstate, "ui_mode_base", None) if cstate else None,
                        "paused": getattr(cstate, "paused", None) if cstate else None,
                        "selected_current_limit": getattr(cstate, "selected_current_limit", None)
                        if cstate
                        else None,
                        "selected_percentage_limit": getattr(
                            cstate, "selected_percentage_limit", None
                        )
                        if cstate
                        else None,
                        "min_current": getattr(cstate, "min_current", None) if cstate else None,
                        "max_current": getattr(cstate, "max_current", None) if cstate else None,
                        "min_surpluspct": getattr(cstate, "min_surpluspct", None)
                        if cstate
                        else None,
                        "dashboard_device_id": _obfuscate(
                            getattr(cstate, "dashboard_device_id", None) if cstate else None
                        ),
                        "dashboard_device_uuid": _obfuscate(
                            getattr(cstate, "dashboard_device_uuid", None) if cstate else None
                        ),
                        "dashboard_device_name_present": bool(
                            getattr(cstate, "dashboard_device_name", None) if cstate else None
                        ),
                        "status_current": getattr(cstate, "status_current", None)
                        if cstate
                        else None,
                        "evcc_state": getattr(cstate, "evcc_state", None) if cstate else None,
                        "evcc_state_code": getattr(cstate, "evcc_state_code", None)
                        if cstate
                        else None,
                        "power_total": getattr(cstate, "power_total", None) if cstate else None,
                        "energy_import_kwh": getattr(cstate, "energy_import_kwh", None)
                        if cstate
                        else None,
                        "power_phases": getattr(cstate, "power_phases", None) if cstate else None,
                        "current_phases": getattr(cstate, "current_phases", None)
                        if cstate
                        else None,
                    }
                )

    # Fill totals
    out["meta"]["stations_total"] = len(stations_out)
    out["meta"]["connectors_total"] = len(connectors_out)
    out["meta"]["connector_states_total"] = sum(
        1 for connector in connectors_out if connector["has_state"]
    )
    out["summary"] = {
        "service_location_ids_count": len(sites or {}),
        "service_location_ids": sorted(sites.keys(), key=_sort_as_text)
        if isinstance(sites, dict)
        else [],
        "station_buckets_count": len(stations_out),
        "carcharger_clients_count": len(connectors_out),
        "connector_states_count": out["meta"]["connector_states_total"],
        "mqtt_clients_count": out["meta"]["mqtt_clients_total"],
    }
    if rt:
        recent_sessions = []
        for site in (sites or {}).values():
            for bucket in ((site or {}).get("stations") or {}).values():
                coord = (bucket or {}).get("coordinator")
                data = getattr(coord, "data", None) if coord else None
                if data and isinstance(getattr(data, "recent_sessions", None), list):
                    recent_sessions.extend(data.recent_sessions)
        out["meta"]["recent_sessions_total"] = len(recent_sessions)

    out["dashboard"] = _dashboard_info(rt)

    out["sites_detail"] = sites_detail
    out["stations"] = stations_out
    out["connectors"] = connectors_out
    return out
