"""Diagnostics support for Smappee EV integration.

Provides redacted runtime information for troubleshooting via HA UI.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .data import RuntimeData

REDACT_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "client_id",
    "password",
    "username",
    "token_type",
    "scope",
    "expires_in",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    out: dict[str, Any] = {}

    rt: RuntimeData | None = getattr(entry, "runtime_data", None)
    sites = getattr(rt, "sites", {}) if rt else {}
    # Stable ordering for diffs / logs
    out["sites"] = sorted(sites.keys())

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
        "title": entry.title,
        "state": state_name,
        "domain": entry.domain,
        "version_manifest": manifest_version,
        "stations_total": 0,  # filled later
        "connectors_total": 0,  # filled later
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
        # Defensive: RuntimeData.mqtt should be dict[int, SmappeeMqtt], but fall back gracefully
        mqtt_obj = (getattr(rt, "mqtt", {}) or {}).get(site_id) if rt else None
        # Aggregate counts
        station_count = len(stations)
        connector_count = sum(
            len((b or {}).get("connector_clients", {})) for b in stations.values()
        )
        # derive mqtt_connected aggregate (any station shows connected)
        mqtt_connected_any = any(_station_connected(b) for b in (stations or {}).values())

        def _mask(v):
            return "**REDACTED**" if v else None

        sites_detail.append(
            {
                "service_location_id": site_id,
                "name": _mask(site_obj.get("name")),
                "uuid": _mask(site_obj.get("serviceLocationUuid")),
                "serial": _mask(site_obj.get("deviceSerialNumber")),
                "station_count": station_count,
                "connector_count": connector_count,
                "mqtt_configured": mqtt_obj is not None,
                "mqtt_connected_any": mqtt_connected_any,
            }
        )
        # per-station and connectors inside same site loop
        for st_uuid, bucket in (stations or {}).items():
            coord = bucket.get("coordinator")
            st_client = bucket.get("station_client")
            if not coord or not getattr(coord, "data", None):
                continue
            data = coord.data
            st = data.station if data else None
            stations_out.append({
                "station_uuid": st_uuid,
                "serial": getattr(st_client, "serial_id", None)
                or getattr(st_client, "serial", None),
                "available": getattr(st, "available", None) if st else None,
                "led_brightness": getattr(st, "led_brightness", None) if st else None,
                "grid_power_total": getattr(st, "grid_power_total", None) if st else None,
                "pv_power_total": getattr(st, "pv_power_total", None) if st else None,
                "house_consumption_power": getattr(st, "house_consumption_power", None)
                if st
                else None,
                "mqtt_connected": getattr(st, "mqtt_connected", None) if st else None,
                "last_mqtt_rx": getattr(st, "last_mqtt_rx", None) if st else None,
                "connector_count": len(data.connectors or {}) if data else 0,
            })
            for cuuid, cstate in ((data.connectors or {}) if data else {}).items():
                connectors_out.append({
                    "connector_uuid": cuuid,
                    "station_uuid": st_uuid,
                    "connector_number": getattr(cstate, "connector_number", None),
                    "available": getattr(cstate, "available", None),
                    "session_state": getattr(cstate, "session_state", None),
                    "session_cause": getattr(cstate, "session_cause", None),
                    "stopped_by_cloud": getattr(cstate, "stopped_by_cloud", None),
                    "raw_charging_mode": getattr(cstate, "raw_charging_mode", None),
                    "optimization_strategy": getattr(cstate, "optimization_strategy", None),
                    "ui_mode_base": getattr(cstate, "ui_mode_base", None),
                    "paused": getattr(cstate, "paused", None),
                    "selected_current_limit": getattr(cstate, "selected_current_limit", None),
                    "selected_percentage_limit": getattr(cstate, "selected_percentage_limit", None),
                    "min_current": getattr(cstate, "min_current", None),
                    "max_current": getattr(cstate, "max_current", None),
                    "min_surpluspct": getattr(cstate, "min_surpluspct", None),
                    "status_current": getattr(cstate, "status_current", None),
                    "evcc_state": getattr(cstate, "evcc_state", None),
                    "evcc_state_code": getattr(cstate, "evcc_state_code", None),
                    "power_total": getattr(cstate, "power_total", None),
                    "energy_import_kwh": getattr(cstate, "energy_import_kwh", None),
                    "power_phases": getattr(cstate, "power_phases", None),
                    "current_phases": getattr(cstate, "current_phases", None),
                })

    # Fill totals
    out["meta"]["stations_total"] = len(stations_out)
    out["meta"]["connectors_total"] = len(connectors_out)

    out["sites_detail"] = sites_detail
    out["stations"] = stations_out
    out["connectors"] = connectors_out
    return out
