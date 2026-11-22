"""Diagnostics support for Smappee EV integration.

Provides redacted runtime information for troubleshooting via HA UI.
"""

from __future__ import annotations

from collections.abc import Sized
from contextlib import suppress
from datetime import datetime, timedelta
from hashlib import sha256
import re
import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .api.dashboard_client import DashboardRequestStats
from .api.discovery import measurement_role
from .models.mqtt_diagnostics import MqttRouteDiagnosticTarget, MqttRoutingDiagnostics
from .models.runtime_data import (
    DashboardRecoveryDiagnostics,
    RuntimeData,
    SmappeeEvConfigEntry,
    SmappeeStationRuntime,
)
from .models.state import IntegrationData, StationState
from .mqtt_setup import mqtt_routing_diagnostics

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


_SERVICE_LOCATION_TOPIC_RE = re.compile(r"(?<=servicelocation/)[^/]+")
_DEVICE_TOPIC_RE = re.compile(r"(?<=devices/)[^/]+")


def _topic_alias(prefix: str, value: str) -> str:
    """Return the stable hash alias used for identifiers inside MQTT topics."""
    digest = sha256(value.encode()).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _diagnostic_topic(topic: str) -> str:
    """Return a correlatable MQTT topic without retaining identifier fragments."""
    redacted, location_count = _SERVICE_LOCATION_TOPIC_RE.subn(
        lambda match: _topic_alias("location", match.group(0)), topic
    )
    redacted, device_count = _DEVICE_TOPIC_RE.subn(
        lambda match: _topic_alias("device", match.group(0)), redacted
    )
    if location_count or device_count:
        return redacted
    return _topic_alias("topic", topic)


def _age_seconds(value: object, now: float) -> int | None:
    """Return the whole seconds since an epoch float or aware datetime, if known."""
    if isinstance(value, datetime):
        stamp = value.timestamp()
    elif isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        stamp = float(value)
    else:
        return None
    return round(now - stamp)


# Dashboard payload paths whose values may describe connectivity or faults.
_STATUS_PATH_RE = re.compile(
    r"online|offline|connect|status|state|seen|contact|heartbeat|communicat|error|fault"
    r"|alarm|health|alive|reach|avail|emergency|fail|problem|update|timestamp",
    re.IGNORECASE,
)
# Leaf keys that hold identifiers or free text, never status.
_FREE_TEXT_KEY_RE = re.compile(r"uuid|serial|name|label|description|email|address", re.I)
_MAX_STATUS_TEXT = 64


def _is_identifier_key(key: str) -> bool:
    """Return True for ``id``/``deviceId``-style keys and free-text fields."""
    return key.lower() == "id" or key.endswith("Id") or bool(_FREE_TEXT_KEY_RE.search(key))


_MAX_STATUS_FIELDS = 200


def _status_value(value: object) -> tuple[bool, object]:
    """Return whether a payload value is a short scalar status, and that value."""
    if value is None or isinstance(value, bool | int | float):
        return True, value
    if isinstance(value, str):
        # Topics, URLs and long text can carry identifiers; status codes cannot.
        return len(value) <= _MAX_STATUS_TEXT and "/" not in value, value
    if isinstance(value, list) and len(value) <= 10:
        items = [_status_value(item) for item in value]
        if all(ok and not isinstance(item, list) for ok, item in items):
            return True, [item for _ok, item in items]
    return False, None


def _walk_dashboard_payload(
    value: object,
    path: str,
    key_paths: set[str],
    status_fields: dict[str, object],
) -> None:
    """Collect every key path, plus short status-like leaf values."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            key_paths.add(re.sub(r"\[\d+\]", "[]", child_path))
            if isinstance(child, dict | list) and not (
                isinstance(child, list) and _status_value(child)[0]
            ):
                _walk_dashboard_payload(child, child_path, key_paths, status_fields)
                continue
            if (
                len(status_fields) < _MAX_STATUS_FIELDS
                and _STATUS_PATH_RE.search(child_path)
                and not _is_identifier_key(str(key))
            ):
                ok, status = _status_value(child)
                if ok:
                    status_fields[child_path] = status
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_dashboard_payload(item, f"{path}[{index}]", key_paths, status_fields)


def _payload_schema_info(details: object, sensitive_values: list[object]) -> dict[str, Any]:
    """Describe a cached Dashboard payload without exporting identifiers.

    Key names are schema, not personal data. Values are exported only for short
    scalars under connectivity/fault-like paths, so a field such as an online
    flag or a last-contact time becomes visible without dumping the payload.
    """
    if not isinstance(details, dict):
        return {"present": False}
    key_paths: set[str] = set()
    status_fields: dict[str, object] = {}
    _walk_dashboard_payload(details, "", key_paths, status_fields)
    return {
        "present": True,
        "key_paths": sorted(key_paths),
        "status_fields": _redact_nested_text_values(status_fields, sensitive_values),
    }


def _named_properties(props: object, value_key: str) -> dict[str, object] | object:
    """Turn ``[{spec: {name}, <value_key>: v}]`` into ``{name: v}`` so names form paths."""
    if not isinstance(props, list):
        return props
    named: dict[str, object] = {}
    for prop in props:
        spec = prop.get("spec") if isinstance(prop, dict) else None
        name = spec.get("name") if isinstance(spec, dict) else None
        if isinstance(name, str) and name:
            named[name] = prop.get(value_key)
    return named


def _smartdevice_payload_info(
    coord: object | None, connector_uuid: str, sensitive_values: list[object]
) -> dict[str, Any]:
    """Describe the last REST smartdevice payload of one connector."""
    payloads = getattr(coord, "last_smartdevice_payloads", None)
    payload = payloads.get(connector_uuid) if isinstance(payloads, dict) else None
    if not isinstance(payload, dict):
        return {"present": False}
    flattened = dict(payload)
    for key, value_key in (("properties", "value"), ("configurationProperties", "values")):
        if key in payload:
            flattened[key] = _named_properties(payload[key], value_key)
    return _payload_schema_info(flattened, sensitive_values)


def _station_connectivity_info(
    coord: object | None, station: StationState | None, now: float
) -> dict[str, Any]:
    """Return the clocks that separate transport, telemetry and REST freshness."""
    poll_interval = None
    poll_fn = getattr(coord, "_smartdevice_poll_interval", None)
    if callable(poll_fn):
        with suppress(Exception):
            interval = poll_fn()
            if isinstance(interval, timedelta):
                poll_interval = round(interval.total_seconds())
    transport = getattr(coord, "mqtt_transport_connected", None)
    monitoring_only = getattr(coord, "monitoring_only", None)
    return {
        "mqtt_transport_connected": transport if isinstance(transport, bool) else None,
        "monitoring_only": monitoring_only if isinstance(monitoring_only, bool) else None,
        "last_mqtt_rx_age_s": _age_seconds(station.last_mqtt_rx if station else None, now),
        "heartbeat_age_s": _age_seconds(getattr(coord, "last_heartbeat_rx", None), now),
        "charger_telemetry_age_s": _age_seconds(
            getattr(coord, "last_valid_charger_telemetry_rx", None), now
        ),
        "real_power_age_s": _age_seconds(getattr(coord, "last_real_power_rx", None), now),
        "dashboard_refresh_age_s": _age_seconds(
            getattr(coord, "_last_dashboard_refresh", None), now
        ),
        "rest_poll_interval_s": poll_interval,
    }


def _connector_telemetry_age(coord: object | None, connector_uuid: str, now: float) -> int | None:
    last_rx = getattr(coord, "last_connector_rx", None)
    if not isinstance(last_rx, dict):
        return None
    return _age_seconds(last_rx.get(connector_uuid), now)


def _safe_len(value: object) -> int:
    """Return len(value) for containers, otherwise 0."""
    if isinstance(value, Sized):
        return len(value)
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


def _redact_nested_text_values(value: object, values: list[object]) -> object:
    """Redact known sensitive values in nested JSON-ish diagnostics data."""
    if isinstance(value, str):
        return _redact_text_values(value, values)
    if isinstance(value, list):
        return [_redact_nested_text_values(item, values) for item in value]
    if isinstance(value, tuple):
        return [_redact_nested_text_values(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _redact_nested_text_values(item, values) for key, item in value.items()}
    return value


def _entry_sensitive_values(entry: SmappeeEvConfigEntry) -> list[object]:
    values: list[object] = []
    for source in (getattr(entry, "data", {}), getattr(entry, "options", {})):
        for key, value in dict(source).items():
            if key in REDACT_KEYS:
                values.append(value)
    return values


def _runtime_sensitive_values(rt: RuntimeData | None) -> list[object]:
    """Collect runtime identifiers that may be embedded in free-text fields."""
    values: list[object] = []
    if rt is None:
        return values
    for site_id, site in rt.sites.items():
        values.append(site_id)
        values.extend(
            [
                site.site_uuid,
                site.gateway_serial,
            ]
        )
        for station_uuid, bucket in site.stations.items():
            values.append(station_uuid)
            connector_clients = {
                key: connector.connector_client for key, connector in bucket.connectors.items()
            }
            clients = [bucket.station_client, *connector_clients.values()]
            for client in clients:
                values.extend(
                    getattr(client, attr, None)
                    for attr in (
                        "serial",
                        "serial_id",
                        "charging_station_serial",
                        "smart_device_uuid",
                        "smart_device_id",
                        "dashboard_device_id",
                    )
                )
            values.extend(connector_clients.keys())
    return values


def _sort_as_text(item: object) -> str:
    """Return a stable text key for mixed JSON-ish values."""
    return str(item)


def _handle_info(client: object | None) -> dict[str, Any]:
    """Return redacted SmappeeDeviceHandle-like metadata."""
    if client is None:
        return {}
    return {
        "service_location_id": _obfuscate(getattr(client, "service_location_id", None)),
        "serial": _obfuscate(getattr(client, "serial", None)),
        "serial_id": _obfuscate(getattr(client, "serial_id", None)),
        "charging_station_serial": _obfuscate(getattr(client, "charging_station_serial", None)),
        "smart_device_uuid": _obfuscate(getattr(client, "smart_device_uuid", None)),
        "smart_device_id": _obfuscate(getattr(client, "smart_device_id", None)),
        "dashboard_device_id": _obfuscate(getattr(client, "dashboard_device_id", None)),
        "connector_number": getattr(client, "connector_number", None),
        "is_station": getattr(client, "is_station", None),
    }


def _mqtt_info(
    mqtt_obj: object | None,
    sensitive_values: list[object] | None = None,
) -> dict[str, Any]:
    """Return redacted MQTT client configuration details."""
    sensitive_values = sensitive_values or []
    if mqtt_obj is None:
        return {"configured": False}
    if isinstance(mqtt_obj, list | tuple):
        clients = list(mqtt_obj)
        return {
            "configured": bool(clients),
            "client_count": len(clients),
            "clients": [_mqtt_info(client, sensitive_values) for client in clients],
        }
    specs = getattr(mqtt_obj, "_mqtt_specs", None) or []
    return {
        "configured": True,
        "service_location_uuid": _obfuscate(getattr(mqtt_obj, "_slu", None)),
        "service_location_id": _obfuscate(getattr(mqtt_obj, "_slu_id", None)),
        "client_id": _obfuscate(getattr(mqtt_obj, "_client_id", None)),
        "serial_number": _obfuscate(getattr(mqtt_obj, "_serial", None)),
        "spec_count": _safe_len(specs),
        "service_location_uuids": [_obfuscate(slu) for slu in getattr(mqtt_obj, "_slus", ()) or ()],
        "specs": [
            {
                "service_location_id": _obfuscate(getattr(spec, "service_location_id", None)),
                "role": getattr(spec, "role", None),
                "metric": getattr(spec, "metric", None),
                "topic": _diagnostic_topic(str(getattr(spec, "topic", ""))),
                "username_present": bool(getattr(spec, "username", None)),
                "password_present": bool(getattr(spec, "password", None)),
                "aspect_path_count": _safe_len(getattr(spec, "aspect_paths", None)),
                "aspect_paths": _redact_nested_text_values(
                    getattr(spec, "aspect_paths", None) or [],
                    sensitive_values,
                ),
            }
            for spec in specs
        ],
    }


def _request_stats_info(stats: object, now: float) -> dict[str, Any] | None:
    """Return Dashboard request volume per endpoint since the client was created."""
    if not isinstance(stats, DashboardRequestStats):
        return None
    window_s = max(now - stats.started_at, 0.0)
    total = sum(stats.requests_by_endpoint.values())
    return {
        "window_s": round(window_s),
        "requests_total": total,
        "requests_per_hour": round(total * 3600 / window_s, 1) if window_s >= 60 else None,
        "rate_limited": stats.rate_limited,
        "requests_by_endpoint": dict(
            sorted(stats.requests_by_endpoint.items(), key=lambda item: (-item[1], item[0]))
        ),
    }


def _dashboard_info(rt: RuntimeData | None, now: float | None = None) -> dict[str, Any]:
    """Return redacted Dashboard client availability metadata."""
    dashboard = getattr(rt, "dashboard", None) if rt else None
    if dashboard is None:
        dashboard = getattr(rt, "api", None) if rt else None
    if dashboard is None:
        return {"configured": False}
    info: dict[str, Any] = {
        "configured": True,
        "client_type": type(dashboard).__name__,
        "username_present": bool(getattr(dashboard, "username", None)),
        "password_present": bool(getattr(dashboard, "password", None)),
        "refresh_token_present": bool(getattr(dashboard, "refresh_token", None)),
        "access_token_present": bool(getattr(dashboard, "_token", None)),
        "token_expires_at_present": bool(getattr(dashboard, "_token_expires_at_ms", None)),
    }
    stats = _request_stats_info(
        getattr(dashboard, "request_stats", None), time.time() if now is None else now
    )
    if stats is not None:
        info["request_stats"] = stats
    return info


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


def _routing_target_info(
    target: MqttRouteDiagnosticTarget,
    *,
    station_aliases: dict[str, str],
    current_station_ids: dict[str, int],
    current_site_id: int | None,
) -> dict[str, Any]:
    """Describe whether a captured route target belongs to the final runtime."""
    if target.target_type == "site":
        status = "current" if target.coordinator_id == current_site_id else "replaced"
        return {
            "target_type": "site",
            "runtime_status": status,
            "target_in_final_runtime": status == "current",
        }

    station_key = target.station_key
    current_id = current_station_ids.get(station_key) if station_key is not None else None
    if current_id is None:
        status = "missing"
    elif current_id == target.coordinator_id:
        status = "current"
    else:
        status = "replaced"
    return {
        "target_type": "station",
        "station_alias": station_aliases.get(station_key) if station_key else None,
        "runtime_status": status,
        "target_in_final_runtime": status == "current",
    }


def _routing_topics_info(
    routes: dict[str, list[MqttRouteDiagnosticTarget]],
    *,
    message_counts: dict[str, int],
    station_aliases: dict[str, str],
    current_station_ids: dict[str, int],
    current_site_id: int | None,
) -> list[dict[str, Any]]:
    """Return redacted route topics and final-runtime membership details."""
    return [
        {
            "topic": _diagnostic_topic(topic),
            "messages_received": message_counts.get(topic, 0),
            "target_count": len(targets),
            "targets": [
                _routing_target_info(
                    target,
                    station_aliases=station_aliases,
                    current_station_ids=current_station_ids,
                    current_site_id=current_site_id,
                )
                for target in targets
            ],
        }
        for topic, targets in sorted(routes.items())
    ]


def _mqtt_routing_info(
    states: list[MqttRoutingDiagnostics],
    site: object,
) -> dict[str, Any]:
    """Return route snapshots and counters for every setup attempted for one site."""
    stations = getattr(site, "stations", {}) or {}
    station_aliases = {
        station_key: f"station_{index}"
        for index, station_key in enumerate(sorted(stations, key=_sort_as_text), start=1)
    }
    current_station_ids = {
        station_key: id(bucket.station_coordinator)
        for station_key, bucket in stations.items()
        if bucket.station_coordinator is not None
    }
    site_coordinator = getattr(site, "site_coordinator", None)
    current_site_id = id(site_coordinator) if site_coordinator is not None else None

    setups: list[dict[str, Any]] = []
    for index, state in enumerate(states, start=1):
        captured_station_keys = set(state.station_coordinator_ids)
        runtime_stations = []
        for station_key, alias in station_aliases.items():
            captured_id = state.station_coordinator_ids.get(station_key)
            current_id = current_station_ids.get(station_key)
            if captured_id is None:
                status = "not_captured"
            elif captured_id == current_id:
                status = "current"
            else:
                status = "replaced"
            runtime_stations.append(
                {
                    "station_alias": alias,
                    "captured_by_setup": captured_id is not None,
                    "coordinator_status": status,
                }
            )

        orphaned_station_targets = sum(
            1
            for station_key, coordinator_id in state.station_coordinator_ids.items()
            if current_station_ids.get(station_key) != coordinator_id
        )
        setups.append(
            {
                "setup_index": index,
                "started": state.started,
                "client_count": state.client_count,
                "control_location_count": len(state.control_location_ids),
                "captured_station_count": len(captured_station_keys),
                "final_runtime_station_count": len(current_station_ids),
                "missing_runtime_station_count": len(
                    set(current_station_ids) - captured_station_keys
                ),
                "orphaned_station_target_count": orphaned_station_targets,
                "site_coordinator_in_final_runtime": (
                    state.site_coordinator_id is not None
                    and state.site_coordinator_id == current_site_id
                ),
                "runtime_stations": runtime_stations,
                "configured_routes": _routing_topics_info(
                    state.configured_routes,
                    message_counts=state.messages_received_by_topic,
                    station_aliases=station_aliases,
                    current_station_ids=current_station_ids,
                    current_site_id=current_site_id,
                ),
                "observed_routes": _routing_topics_info(
                    state.observed_routes,
                    message_counts=state.messages_received_by_topic,
                    station_aliases=station_aliases,
                    current_station_ids=current_station_ids,
                    current_site_id=current_site_id,
                ),
                "traffic": {
                    "messages_received": state.messages_received,
                    "observed_topic_count": len(state.messages_received_by_topic),
                    "heartbeat_messages": state.heartbeat_messages,
                    "routed_messages": state.routed_messages,
                    "unrouted_messages": state.unrouted_messages,
                    "target_deliveries": state.target_deliveries,
                    "delivery_failures": state.delivery_failures,
                    "last_message_rx": state.last_message_rx,
                    "last_routed_rx": state.last_routed_rx,
                    "last_unrouted_rx": state.last_unrouted_rx,
                },
            }
        )

    return {
        "available": bool(states),
        "setup_count": len(states),
        "started_setup_count": sum(state.started for state in states),
        "duplicate_setup_detected": len(states) > 1,
        "setups": setups,
    }


def _classify_appliance_measurement(meas: dict[str, Any]) -> dict[str, Any]:
    """Return the shared production classification for diagnostics."""
    category = meas.get("category")
    appliance_raw = meas.get("appliance")
    appliance = appliance_raw if isinstance(appliance_raw, dict) else {}
    appliance_type = appliance.get("type") if isinstance(appliance_raw, dict) else None

    classification = measurement_role(meas)

    return {
        "category": category,
        "appliance_type": appliance_type,
        # Preserve existing diagnostics keys while both production paths now
        # use the same classifier.
        "discovery_classification": classification,
        "power_classification": classification,
        "would_enter_power_car_branch": classification == "car_charger",
    }


def _measurement_identifiers(meas: dict[str, Any]) -> dict[str, Any]:
    """Return redacted identifier fields present on one highlevel measurement.

    Includes fields beyond the ones connector routing
    currently matches on, so a future/alternate identifier field (e.g. a
    ``smartDeviceId`` instead of a ``smartDeviceUuid``) becomes visible instead
    of silently resulting in an unresolved connector.
    """
    appliance_raw = meas.get("appliance")
    appliance = appliance_raw if isinstance(appliance_raw, dict) else {}

    raw_fields = {
        "uuid": meas.get("uuid"),
        "smart_device_uuid": meas.get("smartDeviceUuid"),
        "device_uuid": meas.get("deviceUuid"),
        "smart_device_id": meas.get("smartDeviceId"),
        "device_id": meas.get("deviceId"),
        "appliance_uuid": appliance.get("uuid"),
        "appliance_smart_device_uuid": appliance.get("smartDeviceUuid"),
        "appliance_device_uuid": appliance.get("deviceUuid"),
        "appliance_smart_device_id": appliance.get("smartDeviceId"),
        "appliance_device_id": appliance.get("deviceId"),
    }
    identifier_fields_present = [
        key for key, value in raw_fields.items() if value not in (None, "")
    ]
    return {
        "identifiers": {key: _obfuscate(value) for key, value in raw_fields.items()},
        "identifier_fields_present": identifier_fields_present,
        # Explicit summary flag so a reader doesn't have to scan the (mostly
        # null) `identifiers` dict just to know whether ANY direct identifier
        # was present on this measurement.
        "direct_identifier_available": bool(identifier_fields_present),
    }


def _measurement_field_inventory(meas: dict[str, Any]) -> dict[str, Any]:
    """Return only the *key names* present on a measurement and its appliance dict.

    Field names (unlike values) are schema information, not personal data, so
    this is safe to expose in full. It reveals whether an unrecognized
    identifier/position field exists under a name the resolver does not
    currently check (see #251 follow-up).
    """
    appliance_raw = meas.get("appliance")
    appliance = appliance_raw if isinstance(appliance_raw, dict) else {}
    return {
        "measurement_keys": sorted(str(key) for key in meas),
        "appliance_keys": sorted(str(key) for key in appliance) if appliance_raw else [],
    }


def _connector_aliases(connector_clients: dict[str, Any]) -> dict[str, str]:
    """Return a stable local alias (connector_1, connector_2, ...) per connector uuid."""

    def _sort_key(item: tuple[str, Any]) -> tuple[bool, int, str]:
        uuid, client = item
        number = getattr(client, "connector_number", None)
        return (number is None, number or 0, uuid)

    ordered = sorted(connector_clients.items(), key=_sort_key)
    return {uuid: f"connector_{index}" for index, (uuid, _client) in enumerate(ordered, start=1)}


def _site_aliases(sites: dict[Any, Any]) -> dict[Any, str]:
    """Return a stable local alias (site_1, site_2, ...) per service-location id.

    Service-location/site ids are otherwise stable, low-entropy numeric
    identifiers that are fully identifying for a specific Smappee account
    when shared verbatim in a public GitHub issue - so diagnostics should
    never expose them raw (#251 follow-up).
    """
    ordered = sorted(sites.keys(), key=_sort_as_text)
    return {site_id: f"site_{index}" for index, site_id in enumerate(ordered, start=1)}


def _resolve_measurement_for_diagnostics(coord: object, meas: dict[str, Any]) -> dict[str, Any]:
    """Resolve position/connector info for one candidate measurement via the live resolver.

    These private resolver hooks are looked up dynamically (``coord`` may be
    any coordinator-like object, including test doubles) instead of via direct
    attribute access. Reusing the *same* resolver that builds the live power
    map means diagnostics can never disagree with the actual mapping outcome
    (#251).
    """
    info: dict[str, Any] = {
        "explicit_position": None,
        "name_trailing_position": None,
        "effective_position": None,
        "resolved_uuid": None,
        "resolution_method": None,
        "name_match_count": 0,
        "name_match_evaluated": False,
    }
    explicit_fn = getattr(coord, "_explicit_position_from_measurement", None)
    if callable(explicit_fn):
        with suppress(Exception):
            info["explicit_position"] = explicit_fn(meas)
    trailing_fn = getattr(coord, "_name_trailing_position_from_measurement", None)
    if callable(trailing_fn):
        with suppress(Exception):
            info["name_trailing_position"] = trailing_fn(meas)

    resolve_fn = getattr(coord, "_resolve_highlevel_measurement", None)
    if callable(resolve_fn):
        with suppress(Exception):
            resolution = resolve_fn(meas)
            info["effective_position"] = getattr(resolution, "position", None)
            info["resolved_uuid"] = getattr(resolution, "connector_uuid", None)
            info["resolution_method"] = getattr(resolution, "method", None)
            info["name_match_count"] = getattr(resolution, "name_match_count", 0)
            info["name_match_evaluated"] = getattr(resolution, "name_match_evaluated", False)
    return info


def _build_power_mapping_measurements(
    coord: object,
    highlevel_configs: dict[Any, Any],
    aliases: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the per-measurement diagnostics list and its summary counters.

    Split out of ``_power_mapping_info`` purely to keep cyclomatic complexity
    manageable; behavior is unchanged.
    """
    measurements_out: list[dict[str, Any]] = []
    car_charger_measurement_count = 0
    candidate_count = 0
    resolved_count = 0
    ambiguous_name_count = 0
    classification_mismatch_count = 0
    resolution_method_counts: dict[str, int] = {}
    index = 0
    for cfg in highlevel_configs.values():
        if not isinstance(cfg, dict):
            continue
        for meas in cfg.get("measurements") or []:
            if not isinstance(meas, dict):
                continue
            if str(meas.get("type") or "").upper() != "APPLIANCE":
                continue
            index += 1
            classification = _classify_appliance_measurement(meas)
            if classification["discovery_classification"] == "car_charger":
                car_charger_measurement_count += 1
                if not classification["would_enter_power_car_branch"]:
                    classification_mismatch_count += 1

            is_candidate = (
                classification["discovery_classification"] == "car_charger"
                or classification["power_classification"] == "car_charger"
            )
            if is_candidate:
                resolution_info = _resolve_measurement_for_diagnostics(coord, meas)
                candidate_count += 1
                if resolution_info["resolved_uuid"] is not None:
                    resolved_count += 1
                if (
                    resolution_info["name_match_evaluated"]
                    and resolution_info["name_match_count"] > 1
                ):
                    ambiguous_name_count += 1
                method = resolution_info["resolution_method"]
                if method:
                    resolution_method_counts[method] = resolution_method_counts.get(method, 0) + 1
            else:
                resolution_info = {
                    "explicit_position": None,
                    "name_trailing_position": None,
                    "effective_position": None,
                    "resolved_uuid": None,
                    "resolution_method": None,
                    "name_match_count": 0,
                    "name_match_evaluated": False,
                }

            resolved_uuid = resolution_info["resolved_uuid"]
            measurements_out.append(
                {
                    "measurement_index": index,
                    "type": "APPLIANCE",
                    **classification,
                    "name_present": bool(meas.get("name")),
                    "explicit_position": resolution_info["explicit_position"],
                    "name_trailing_position": resolution_info["name_trailing_position"],
                    "effective_position": resolution_info["effective_position"],
                    **_measurement_identifiers(meas),
                    **_measurement_field_inventory(meas),
                    "resolution_method": resolution_info["resolution_method"],
                    "name_match_count": resolution_info["name_match_count"],
                    "name_match_evaluated": resolution_info["name_match_evaluated"],
                    "resolved": resolved_uuid is not None,
                    "resolved_connector": aliases.get(resolved_uuid) if resolved_uuid else None,
                }
            )

    summary = {
        "car_charger_measurement_count": car_charger_measurement_count,
        "mapping_summary": {
            "resolution_method_counts": resolution_method_counts,
            "resolved_measurement_count": resolved_count,
            "unresolved_measurement_count": candidate_count - resolved_count,
            "ambiguous_name_measurement_count": ambiguous_name_count,
            "classification_mismatch_count": classification_mismatch_count,
        },
    }
    return measurements_out, summary


def _build_power_mapping_topics(
    idx_maps: dict[str, Any],
    aliases: dict[str, str],
) -> tuple[list[dict[str, Any]], int, int]:
    """Build the per-topic diagnostics list plus car/unique-connector counts."""
    topics_out: list[dict[str, Any]] = []
    car_mapping_count = 0
    unique_mapped_connectors: set[str] = set()
    for topic, topic_map in idx_maps.items():
        if not isinstance(topic_map, dict):
            continue
        cars = topic_map.get("cars") or {}
        grid = topic_map.get("grid") or {}
        pv = topic_map.get("pv") or {}
        car_mapping_count += len(cars)
        unique_mapped_connectors.update(cars.keys())
        topics_out.append(
            {
                "topic": _diagnostic_topic(topic),
                "grid_present": bool(any((grid or {}).values())),
                "pv_present": bool(any((pv or {}).values())),
                "car_mapping_count": len(cars),
                "mapped_connectors": sorted(aliases.get(uuid, _obfuscate(uuid)) for uuid in cars),
            }
        )
    return topics_out, car_mapping_count, len(unique_mapped_connectors)


def _power_mapping_info(coord: object | None) -> dict[str, Any]:
    """Return redacted diagnostics for the car-charger MQTT power mapping (#251).

    Deliberately looks at every ``APPLIANCE`` measurement instead of reusing
    the ``coordinators.power`` car-charger filter, so a classification
    mismatch stays visible in diagnostics instead of being filtered out again
    by the same logic that may be causing the bug.
    """
    if coord is None:
        return {"available": False}

    connector_clients = getattr(coord, "connector_clients", None)
    if not isinstance(connector_clients, dict):
        connector_clients = {}
    aliases = _connector_aliases(connector_clients)

    known_connectors = [
        {
            "alias": alias,
            "connector_number": getattr(connector_clients[uuid], "connector_number", None),
            "connector_uuid": _obfuscate(uuid),
        }
        for uuid, alias in aliases.items()
    ]

    highlevel_configs = getattr(coord, "_highlevel_configs", None)
    if not isinstance(highlevel_configs, dict):
        highlevel_configs = {}
    measurements_out, measurement_summary = _build_power_mapping_measurements(
        coord, highlevel_configs, aliases
    )

    idx_maps = getattr(coord, "_power_index_maps_by_topic", None)
    if idx_maps is None:
        mapping_cache_state = "not_initialized"
        idx_maps = {}
    elif isinstance(idx_maps, dict):
        mapping_cache_state = "mapped" if idx_maps else "empty"
    else:
        mapping_cache_state = "not_initialized"
        idx_maps = {}
    topics_out, car_mapping_count, unique_mapped_connector_count = _build_power_mapping_topics(
        idx_maps, aliases
    )

    return {
        "available": True,
        "mapping_cache_state": mapping_cache_state,
        "known_connector_count": len(connector_clients),
        "known_connectors": known_connectors,
        "car_charger_measurement_count": measurement_summary["car_charger_measurement_count"],
        "car_mapping_count": car_mapping_count,
        # Unique connector count across all topics - `car_mapping_count` sums
        # per-topic entries, so a connector whose power/current/energy ever
        # end up split across different topics could otherwise be counted
        # more than once here.
        "unique_mapped_connector_count": unique_mapped_connector_count,
        "mapping_summary": measurement_summary["mapping_summary"],
        "measurements": measurements_out,
        "topics": topics_out,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    out: dict[str, Any] = {}
    # Reference point for every *_age_s field and the epoch *_rx fields.
    now = time.time()

    rt: RuntimeData | None = getattr(entry, "runtime_data", None)
    out["runtime_mode"] = rt.mode.value if rt is not None else None
    recovery = getattr(rt, "recovery", None)
    if not isinstance(recovery, DashboardRecoveryDiagnostics):
        recovery = DashboardRecoveryDiagnostics()
    out["recovery"] = {
        "status": recovery.status,
        "attempts": recovery.attempts,
        "last_attempt": recovery.last_attempt.isoformat() if recovery.last_attempt else None,
        "next_attempt": recovery.next_attempt.isoformat() if recovery.next_attempt else None,
        "last_error_category": recovery.last_error_category,
    }
    sites = rt.sites if rt else {}
    sensitive_values = _entry_sensitive_values(entry) + _runtime_sensitive_values(rt)
    # Stable local aliases instead of raw service-location ids (#251 follow-up):
    # those numeric ids are otherwise stable, low-entropy identifiers that fully
    # identify a specific Smappee account when a diagnostics dump is attached to
    # a public GitHub issue.
    site_aliases = _site_aliases(sites)
    # Stable ordering for diffs / logs
    out["sites"] = [site_aliases[s] for s in sorted(sites.keys(), key=_sort_as_text)]

    out["config_entry_data"] = async_redact_data(dict(entry.data), REDACT_KEYS)
    out["options"] = async_redact_data(dict(entry.options), REDACT_KEYS)

    # Meta
    manifest_version: str | None = None
    try:  # best effort
        integration = await async_get_integration(hass, entry.domain)
        manifest_version = getattr(integration, "version", None)
    except Exception:
        manifest_version = None

    state_name = None
    try:
        st = getattr(entry, "state", None)
        state_name = getattr(st, "name", None) or str(st) if st is not None else None
    except Exception:
        state_name = None

    out["meta"] = {
        "entry_id": entry.entry_id,
        "title": _redact_text_values(entry.title, sensitive_values),
        "state": state_name,
        "domain": entry.domain,
        "generated_at": round(now),
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

    def _station_connected(bucket: SmappeeStationRuntime) -> bool:
        coord = bucket.station_coordinator
        data: IntegrationData | None = coord.data if coord else None
        return bool(data and data.station.mqtt_connected)

    for site_id, site in (sites or {}).items():
        stations = site.stations
        # Defensive: RuntimeData.mqtt may contain one or more SmappeeMqtt clients per site.
        mqtt_obj = rt.mqtt.get(site_id) if rt else None
        routing_states = list(getattr(rt, "mqtt_diagnostics", {}).get(site_id, [])) if rt else []
        if not routing_states:
            routing_states = mqtt_routing_diagnostics(mqtt_obj)
        site_alias = site_aliases.get(site_id, _obfuscate(site_id))
        # Aggregate counts
        station_count = len(stations)
        connector_count = sum(len(bucket.connectors) for bucket in stations.values())
        # derive mqtt_connected aggregate (any station shows connected)
        mqtt_connected_any = any(_station_connected(b) for b in (stations or {}).values())

        sites_detail.append(
            {
                "site_alias": site_alias,
                "service_location_id": _obfuscate(site_id),
                "name_present": site.site_name is not None,
                "uuid": _obfuscate(site.site_uuid),
                "serial": _obfuscate(site.gateway_serial),
                "control_location_ids": [
                    _obfuscate(v) for v in _safe_sorted(site.control_location_ids)
                ],
                "measurement_location_ids": [
                    _obfuscate(v) for v in _safe_sorted(site.measurement_location_ids)
                ],
                "station_count": station_count,
                "connector_count": connector_count,
                "mqtt_configured": mqtt_obj is not None,
                "mqtt_connected_any": mqtt_connected_any,
                "mqtt": _mqtt_info(mqtt_obj, sensitive_values),
                "mqtt_routing": _mqtt_routing_info(routing_states, site),
            }
        )
        # per-station and connectors inside same site loop
        for st_uuid, bucket in (stations or {}).items():
            coord = bucket.station_coordinator
            st_client = bucket.station_client
            connector_clients = {
                key: connector.connector_client for key, connector in bucket.connectors.items()
            }
            # Coordinator data is None until the first refresh, whatever its type says.
            data: IntegrationData | None = coord.data if coord else None
            st = data.station if data else None
            stations_out.append(
                {
                    "site_alias": site_alias,
                    "service_location_id": _obfuscate(site_id),
                    "station_uuid": _obfuscate(st_uuid),
                    "station_handle": _handle_info(st_client),
                    "available": st.available if st else None,
                    "api_available": st.api_available if st else None,
                    "dashboard_available": st.dashboard_available if st else None,
                    "led_brightness": st.led_brightness if st else None,
                    "dashboard_led_device_id": _obfuscate(
                        st.dashboard_led_device_id if st else None
                    ),
                    "grid_power_total": st.grid_power_total if st else None,
                    "pv_power_total": st.pv_power_total if st else None,
                    "house_consumption_power": st.house_consumption_power if st else None,
                    "mqtt_connected": st.mqtt_connected if st else None,
                    "last_mqtt_rx": st.last_mqtt_rx if st else None,
                    "connectivity": _station_connectivity_info(coord, st, now),
                    "dashboard_details": _payload_schema_info(
                        st.dashboard_charging_station_details if st else None,
                        sensitive_values,
                    ),
                    "connector_client_count": len(connector_clients),
                    "connector_state_count": len((data.connectors or {}) if data else {}),
                    "power_mapping": _power_mapping_info(coord),
                }
            )
            state_by_uuid = (data.connectors or {}) if data else {}
            for cuuid, client in connector_clients.items():
                cstate = state_by_uuid.get(cuuid)
                connectors_out.append(
                    {
                        "site_alias": site_alias,
                        "service_location_id": _obfuscate(site_id),
                        "station_uuid": _obfuscate(st_uuid),
                        "connector_uuid": _obfuscate(cuuid),
                        "connector_handle": _handle_info(client),
                        "has_state": cstate is not None,
                        # Same alias as the device segment in mqtt_routing topics.
                        "mqtt_device_alias": _topic_alias("device", cuuid),
                        "connection_status": cstate.connection_status if cstate else None,
                        "configuration_errors": _redact_nested_text_values(
                            cstate.configuration_errors, sensitive_values
                        )
                        if cstate
                        else None,
                        "iec_status": cstate.iec_status if cstate else None,
                        "rest_session_state": cstate.rest_session_state if cstate else None,
                        "telemetry_age_s": _connector_telemetry_age(coord, cuuid, now),
                        "smartdevice_payload": _smartdevice_payload_info(
                            coord, cuuid, sensitive_values
                        ),
                        "connector_number": cstate.connector_number
                        if cstate
                        else getattr(client, "connector_number", None),
                        "available": cstate.available if cstate else None,
                        "api_available": cstate.api_available if cstate else None,
                        "session_state": cstate.session_state if cstate else None,
                        "session_cause": cstate.session_cause if cstate else None,
                        "stopped_by_cloud": cstate.stopped_by_cloud if cstate else None,
                        "raw_charging_mode": cstate.raw_charging_mode if cstate else None,
                        "optimization_strategy": cstate.optimization_strategy if cstate else None,
                        "ui_mode_base": cstate.ui_mode_base if cstate else None,
                        "paused": cstate.paused if cstate else None,
                        "selected_current_limit": cstate.selected_current_limit if cstate else None,
                        "selected_percentage_limit": cstate.selected_percentage_limit
                        if cstate
                        else None,
                        "min_current": cstate.min_current if cstate else None,
                        "max_current": cstate.max_current if cstate else None,
                        "min_surpluspct": cstate.min_surpluspct if cstate else None,
                        "dashboard_device_id": _obfuscate(
                            cstate.dashboard_device_id if cstate else None
                        ),
                        "dashboard_device_uuid": _obfuscate(
                            cstate.dashboard_device_uuid if cstate else None
                        ),
                        "dashboard_device_name_present": bool(
                            cstate.dashboard_device_name if cstate else None
                        ),
                        "status_current": cstate.status_current if cstate else None,
                        "evcc_state": cstate.evcc_state if cstate else None,
                        "evcc_state_code": cstate.evcc_state_code if cstate else None,
                        "power_total": cstate.power_total if cstate else None,
                        "energy_import_kwh": cstate.energy_import_kwh if cstate else None,
                        "power_phases": cstate.power_phases if cstate else None,
                        "current_phases": cstate.current_phases if cstate else None,
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
        "service_location_ids": [site_aliases[s] for s in sorted(sites.keys(), key=_sort_as_text)]
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
            for bucket in site.stations.values():
                coord = bucket.station_coordinator
                session_data: IntegrationData | None = coord.data if coord else None
                if session_data and isinstance(session_data.recent_sessions, list):
                    recent_sessions.extend(session_data.recent_sessions)
        out["meta"]["recent_sessions_total"] = len(recent_sessions)

    out["dashboard"] = _dashboard_info(rt, now)

    out["sites_detail"] = sites_detail
    out["stations"] = stations_out
    out["connectors"] = connectors_out
    return out
