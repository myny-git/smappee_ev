"""Private, versioned MQTT bootstrap data; never stores live measurements."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
import voluptuous as vol

from .api.dashboard_client import SmappeeDashboardClient
from .api.device_handle import SmappeeDeviceHandle
from .api.discovery import MqttChannelSpec
from .const import CONF_USERNAME, DOMAIN, UPDATE_INTERVAL_DEFAULT
from .coordinator import SmappeeSiteCoordinator, SmappeeStationCoordinator
from .models.runtime_data import (
    RuntimeData,
    RuntimeMode,
    SmappeeConnectorRuntime,
    SmappeeEvConfigEntry,
    SmappeeLedRuntime,
    SmappeeSiteRuntime,
    SmappeeStationRuntime,
)
from .models.state import ConnectorState, IntegrationData, SiteData, SiteState, StationState
from .mqtt_setup import _setup_mqtt
from .mqtt_specs import _mqtt_specs_from_highlevel_configs

_LOGGER = logging.getLogger(__name__)
_TEXT = vol.Any(None, str)
_SITE_FIELDS = {
    "site_location_id": int,
    "site_name": _TEXT,
    "site_function_type": _TEXT,
    "site_uuid": _TEXT,
    "gateway_serial": _TEXT,
    "gateway_type": _TEXT,
    "control_location_ids": [int],
    "measurement_location_ids": [int],
}
_STATION_FIELDS = {
    "site_location_id": int,
    "control_location_id": int,
    "site_name": _TEXT,
    "gateway_serial": _TEXT,
    "gateway_type": _TEXT,
    "control_name": _TEXT,
    "control_uuid": _TEXT,
    "control_function_type": _TEXT,
    "station_name": _TEXT,
    "charging_station_serial": str,
    "charging_station_model": _TEXT,
}
_CLIENT_FIELDS = {
    "serial": str,
    "smart_device_uuid": str,
    "smart_device_id": str,
    "service_location_id": int,
    "connector_number": vol.Any(None, int),
    "is_station": bool,
    "charging_station_serial": _TEXT,
    "site_location_id": int,
    "charging_station_model": _TEXT,
}


def _fields(obj: object, schema: dict[str, Any]) -> dict[str, Any]:
    return {key: getattr(obj, key) for key in schema}


def _validate_fields(value: Any, schema: dict[str, Any]) -> None:
    vol.Schema({vol.Required(key): validator for key, validator in schema.items()})(value)


def bootstrap_store(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> Store[dict[str, Any]]:
    """Use HA atomic storage with private filesystem permissions."""
    return Store(
        hass, 1, f"{DOMAIN}.{entry.entry_id}.mqtt_bootstrap", private=True, atomic_writes=True
    )


def _validate_maps(maps: Any, connector_keys: set[str]) -> None:
    """Reject malformed index maps before MQTT callbacks can consume them."""
    if not isinstance(maps, dict):
        raise TypeError("Invalid power maps")
    for topic, mapping in maps.items():
        if not isinstance(topic, str) or not isinstance(mapping, dict):
            raise TypeError("Invalid power topic")
        cars = mapping.get("cars", {})
        if not isinstance(cars, dict) or not set(cars).issubset(connector_keys):
            raise ValueError("Invalid connector routing")
        for group in [mapping.get("grid", {}), mapping.get("pv", {}), *cars.values()]:
            if not isinstance(group, dict):
                raise TypeError("Invalid measurement group")
            for key, value in group.items():
                if key == "position":
                    if value is not None and type(value) is not int:
                        raise ValueError("Invalid connector position")
                elif key == "serial":
                    if value is not None and not isinstance(value, str):
                        raise ValueError("Invalid connector serial")
                elif key == "power_field":
                    if value not in (None, "activePowerData", "channelData"):
                        raise ValueError("Invalid power field")
                elif (
                    key not in ("power", "current", "energy", "cons")
                    or not isinstance(value, list)
                    or any(type(i) is not int or i < 0 for i in value)
                ):
                    raise ValueError("Invalid measurement indexes")


def validate_snapshot(value: Any, entry: SmappeeEvConfigEntry) -> dict[str, Any]:
    """Validate identity, metadata, credentials and routes as one complete snapshot."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Unsupported MQTT snapshot")
    if value.get("entry_id") != entry.entry_id or value.get("account") != entry.data.get(
        CONF_USERNAME
    ):
        raise ValueError("MQTT snapshot belongs to another account")
    fetched = datetime.fromisoformat(value["fetched_at"])
    if fetched.tzinfo is None:
        raise ValueError("Missing snapshot timezone")
    sites = value.get("sites")
    if not isinstance(sites, list) or not sites:
        raise ValueError("Empty MQTT snapshot")
    seen_sites: set[int] = set()
    for site in sites:
        _validate_fields(site["metadata"], _SITE_FIELDS)
        sid = site["metadata"]["site_location_id"]
        if sid in seen_sites:
            raise ValueError("Duplicate site")
        seen_sites.add(sid)
        specs = site["specs"]
        if not isinstance(specs, list) or not specs:
            raise ValueError("Missing MQTT channels")
        for spec in specs:
            _validate_fields(
                spec,
                {
                    "service_location_id": int,
                    "role": str,
                    "metric": str,
                    "topic": vol.All(str, vol.Length(min=1)),
                    "username": _TEXT,
                    "password": _TEXT,
                    "aspect_paths": [dict],
                },
            )
            if not (spec["username"] and spec["password"]) and not spec["topic"].startswith(
                "servicelocation/"
            ):
                raise ValueError("Missing MQTT credentials")
        _validate_maps(site["power_maps"], set())
        stations = site["stations"]
        if not isinstance(stations, dict) or not stations:
            raise ValueError("Missing stations")
        _validate_stations(stations, sid)
    return value


def _validate_stations(stations: dict[str, Any], sid: int) -> None:
    seen_connectors: set[str] = set()
    for key, station in stations.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Missing station identity")
        _validate_fields(station["metadata"], _STATION_FIELDS)
        if station["metadata"]["site_location_id"] != sid:
            raise ValueError("Station belongs to another site")
        _validate_fields(station["client"], _CLIENT_FIELDS)
        connectors = station["connectors"]
        if not isinstance(connectors, dict):
            raise TypeError("Invalid connectors")
        for ckey, connector in connectors.items():
            if not isinstance(ckey, str) or not ckey or ckey in seen_connectors:
                raise ValueError("Duplicate or missing connector identity")
            seen_connectors.add(ckey)
            _validate_fields(
                connector["metadata"],
                {
                    "connector_key": str,
                    "connector_uuid": _TEXT,
                    "connector_position": vol.Any(None, int),
                },
            )
            if connector["metadata"]["connector_key"] != ckey:
                raise ValueError("Connector key mismatch")
            _validate_fields(connector["client"], _CLIENT_FIELDS)
        _validate_maps(station["power_maps"], set(connectors))
        if not isinstance(station["leds"], dict):
            raise TypeError("Invalid LED metadata")
        for led in station["leds"].values():
            _validate_fields(
                led,
                {
                    "led_key": str,
                    "led_device_id": str,
                    "led_device_uuid": _TEXT,
                    "led_device_name": _TEXT,
                },
            )


def snapshot_from_runtime(runtime: RuntimeData, entry: SmappeeEvConfigEntry) -> dict[str, Any]:
    """Export only the metadata needed to recreate existing MQTT entities."""
    sites = []
    for site in runtime.sites.values():
        # Discovery completeness is independent of the later live REST refresh.
        expected = set(site.measurement_location_ids)
        if not expected or not expected.issubset(site.highlevel_configs):
            raise ValueError("Missing measurement location configuration")
        if any(
            not isinstance(site.highlevel_configs[sid], dict) or not site.highlevel_configs[sid]
            for sid in expected
        ):
            raise ValueError("Invalid measurement location configuration")
        stations = {}
        for key, bucket in site.stations.items():
            coord = bucket.station_coordinator
            if coord is None or coord.data is None:
                raise ValueError("Incomplete station bootstrap")
            stations[key] = {
                "metadata": _fields(bucket, _STATION_FIELDS),
                "client": _fields(bucket.station_client, _CLIENT_FIELDS),
                "connectors": {
                    ckey: {
                        "metadata": {
                            "connector_key": conn.connector_key,
                            "connector_uuid": conn.connector_uuid,
                            "connector_position": conn.connector_position,
                        },
                        "client": _fields(conn.connector_client, _CLIENT_FIELDS),
                    }
                    for ckey, conn in bucket.connectors.items()
                },
                "leds": {key: asdict(led) for key, led in bucket.led_devices.items()},
                "power_maps": coord._power_index_maps_by_topic or {},
            }
        if site.site_coordinator is None:
            raise ValueError("Missing site coordinator")
        sites.append(
            {
                "metadata": _fields(site, _SITE_FIELDS),
                "stations": stations,
                "specs": [
                    asdict(spec)
                    for spec in _mqtt_specs_from_highlevel_configs(site.highlevel_configs)
                ],
                "power_maps": site.site_coordinator._power_index_maps_by_topic or {},
            }
        )
    return validate_snapshot(
        {
            "schema_version": 1,
            "entry_id": entry.entry_id,
            "account": entry.data.get(CONF_USERNAME),
            "fetched_at": datetime.now(UTC).isoformat(),
            "sites": sites,
        },
        entry,
    )


async def async_load_snapshot(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry
) -> dict[str, Any] | None:
    try:
        value = await bootstrap_store(hass, entry).async_load()
        return validate_snapshot(value, entry) if value is not None else None
    except OSError, ValueError, TypeError, KeyError, vol.Invalid:
        _LOGGER.warning("Ignoring unavailable or invalid MQTT bootstrap snapshot")
        return None


async def async_save_snapshot(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry, runtime: RuntimeData
) -> None:
    """A cache failure must never break an otherwise working online setup."""
    try:
        snapshot = snapshot_from_runtime(runtime, entry)
        await bootstrap_store(hass, entry).async_save(snapshot)
    except OSError, ValueError, TypeError, KeyError, AttributeError, vol.Invalid:
        _LOGGER.warning("MQTT bootstrap snapshot could not be saved; retaining previous snapshot")


def build_cached_runtime(
    hass: HomeAssistant,
    entry: SmappeeEvConfigEntry,
    dashboard: SmappeeDashboardClient,
    snapshot: dict[str, Any],
) -> RuntimeData:
    """Construct a monitoring runtime without calling any Dashboard endpoint."""
    validate_snapshot(snapshot, entry)
    dashboard.monitoring_only = True
    runtime = RuntimeData(
        api=dashboard, dashboard=dashboard, sites={}, mqtt={}, mode=RuntimeMode.MQTT_ONLY
    )
    for saved_site in snapshot["sites"]:
        site = SmappeeSiteRuntime(**saved_site["metadata"])
        sid = site.site_location_id
        runtime.sites[sid] = site
        site_coord = SmappeeSiteCoordinator(
            hass,
            site_location_id=sid,
            site_name=site.site_name or "",
            site_uuid=site.site_uuid,
            gateway_serial=site.gateway_serial,
            gateway_type=site.gateway_type,
            update_interval=UPDATE_INTERVAL_DEFAULT,
            config_entry=entry,
        )
        site_coord.monitoring_only = True
        object.__setattr__(site_coord, "update_interval", None)
        site_coord._power_index_maps_by_topic = saved_site["power_maps"]
        site_coord.async_set_updated_data(SiteData(site=SiteState(mqtt_connected=False)))
        site.site_coordinator = site_coord
        for key, saved in saved_site["stations"].items():
            client = SmappeeDeviceHandle(**saved["client"])
            bucket = SmappeeStationRuntime(
                **saved["metadata"],
                station_client=client,
                station_coordinator=None,
                site_coordinator=site_coord,
            )
            site.stations[key] = bucket
            bucket.led_devices = {
                key: SmappeeLedRuntime(**led) for key, led in saved["leds"].items()
            }
            bucket.connectors = {
                ckey: SmappeeConnectorRuntime(
                    **conn["metadata"], connector_client=SmappeeDeviceHandle(**conn["client"])
                )
                for ckey, conn in saved["connectors"].items()
            }
            coord = SmappeeStationCoordinator(
                hass,
                client,
                {key: conn.connector_client for key, conn in bucket.connectors.items()},
                UPDATE_INTERVAL_DEFAULT,
                config_entry=entry,
                dashboard_client=dashboard,
                site_name=bucket.site_name,
                gateway_serial=bucket.gateway_serial,
                gateway_type=bucket.gateway_type,
                station_name=bucket.station_name,
                station_model=bucket.charging_station_model,
            )
            coord.monitoring_only = True
            object.__setattr__(coord, "update_interval", None)
            coord._power_index_maps_by_topic = saved["power_maps"]
            coord.async_set_updated_data(
                IntegrationData(
                    station=StationState(api_available=False, mqtt_connected=False),
                    connectors={
                        key: ConnectorState(
                            connector_number=conn.connector_position or 1, api_available=False
                        )
                        for key, conn in bucket.connectors.items()
                    },
                )
            )
            bucket.station_coordinator = coord
        specs = [MqttChannelSpec(**spec) for spec in saved_site["specs"]]
        mqtt = _setup_mqtt(
            hass,
            site.site_uuid,
            site.gateway_serial or f"smappee-{sid}",
            sid,
            site.stations,
            f"ha-{entry.entry_id[-6:]}",
            UPDATE_INTERVAL_DEFAULT,
            mqtt_specs=specs,
            site_coordinator=site_coord,
            background_tasks=runtime.background_tasks,
            start_clients=False,
        )
        runtime.mqtt[sid] = mqtt
        site.mqtt_clients = mqtt
        for bucket in site.stations.values():
            bucket.mqtt = mqtt
    return runtime
