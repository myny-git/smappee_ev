"""Runtime assembly helpers for Smappee EV setup."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from inspect import isawaitable
import logging

from homeassistant.core import HomeAssistant

from .api.dashboard_client import SmappeeDashboardClient
from .api.discovery import SmappeeLocationTopology
from .coordinator import SmappeeCoordinator, SmappeeSiteCoordinator
from .dashboard_discovery import (
    _dashboard_client_configured,
    _dashboard_fetch_devices,
    _dashboard_fetch_highlevel_configs,
    _fallback_dashboard_connector_mapping,
    _fetch_dashboard_connector_mapping,
)
from .models.runtime_data import (
    MqttRuntimeValue,
    RuntimeData,
    SmappeeEvConfigEntry,
    SmappeeStationRuntime,
)
from .models.state import HighLevelConfigMap
from .mqtt_setup import _setup_mqtt
from .mqtt_specs import _mqtt_specs_from_highlevel_configs, _split_highlevel_configs_by_scope
from .topology import (
    _assign_connectors,
    _connector_uuid,
    _fallback_assign,
    _fallback_highlevel_connector_mapping,
    _make_station_clients_with_mapping_fallback,
    _normalize_connector_mapping_station_keys,
    _safe_str,
    _split_devices,
    _station_devices_from_connector_mapping,
    _station_serial,
)

_LOGGER = logging.getLogger(__name__)


def _log_station_runtime_shape(context: str, stations: Mapping[str, object]) -> None:
    """Log the station runtime container shape without exposing raw identifiers."""
    station_count = len(stations)
    connector_count = sum(
        len(station.connectors)
        for station in stations.values()
        if isinstance(station, SmappeeStationRuntime)
    )
    runtime_type = type(next(iter(stations.values()))).__name__ if stations else "none"
    _LOGGER.debug(
        "Prepared Smappee EV %s runtime: station_runtime=%s, stations=%d, connectors=%d",
        context,
        runtime_type,
        station_count,
        connector_count,
    )


def _log_stored_runtime_shape(runtime: RuntimeData) -> None:
    """Log the stored RuntimeData shape without exposing raw identifiers."""
    site_count = len(runtime.sites or {})
    site_types = sorted({type(site).__name__ for site in (runtime.sites or {}).values()}) or [
        "none"
    ]
    stations = [
        bucket for site in (runtime.sites or {}).values() for bucket in site.stations.values()
    ]
    station_types = sorted({type(bucket).__name__ for bucket in stations}) or ["none"]
    connector_count = sum(len(bucket.connectors) for bucket in stations)
    _LOGGER.debug(
        "Stored Smappee EV runtime_data: sites=%d, site_runtime_types=%s, "
        "station_runtime_types=%s, stations=%d, connectors=%d",
        site_count,
        ",".join(site_types),
        ",".join(station_types),
        len(stations),
        connector_count,
    )


async def _create_coordinators(
    hass,
    stations: dict[str, SmappeeStationRuntime],
    update_interval,
    config_entry=None,
    dashboard_client=None,
    highlevel_configs: HighLevelConfigMap | None = None,
    start_tracking: bool = True,
):
    created: list[SmappeeCoordinator] = []
    try:
        for bucket in stations.values():
            kwargs = {
                "station_client": bucket.station_client,
                "connector_clients": {
                    key: connector.connector_client for key, connector in bucket.connectors.items()
                },
                "update_interval": update_interval,
                "config_entry": config_entry,
            }
            if dashboard_client is not None:
                kwargs["dashboard_client"] = dashboard_client
            if highlevel_configs is not None:
                kwargs["highlevel_configs"] = highlevel_configs
            for key in (
                "site_name",
                "gateway_serial",
                "gateway_type",
                "station_name",
                "station_model",
            ):
                value = (
                    bucket.charging_station_model
                    if key == "station_model"
                    else getattr(bucket, key)
                )
                if value is not None:
                    kwargs[key] = value
            coord = SmappeeCoordinator(hass, **kwargs)
            created.append(coord)
            await coord.async_config_entry_first_refresh()
            bucket.station_coordinator = coord
    except BaseException:
        for coord in created:
            shutdown = getattr(coord, "async_shutdown", None)
            if callable(shutdown):
                try:
                    result = shutdown()
                    if isawaitable(result):
                        await result
                except Exception:  # noqa: BLE001 - rollback must continue
                    _LOGGER.debug("Station coordinator rollback failed", exc_info=True)
        raise

    if start_tracking:
        for coord in created:
            coord.async_start_session_tracking()


async def _create_site_coordinator(
    hass,
    *,
    topology: SmappeeLocationTopology,
    update_interval: int,
    config_entry=None,
    highlevel_configs: HighLevelConfigMap | None = None,
):
    """Create the site-scoped coordinator for grid/PV/house data."""
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=topology.site_location_id,
        site_name=topology.site_name,
        site_uuid=topology.site_location_uuid,
        gateway_serial=topology.site_gateway_serial,
        gateway_type=topology.site_gateway_type,
        update_interval=update_interval,
        config_entry=config_entry,
        highlevel_configs=highlevel_configs,
    )
    await coord.async_config_entry_first_refresh()
    return coord


async def _prepare_topology(  # noqa: C901 - topology validation is intentionally linear
    hass: HomeAssistant,
    topology: SmappeeLocationTopology,
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
    dashboard_client: SmappeeDashboardClient | None = None,
    background_tasks: set[asyncio.Task] | None = None,
    start_runtime: bool = True,
) -> tuple[dict[str, SmappeeStationRuntime] | None, MqttRuntimeValue]:
    """Prepare one site-first Dashboard topology."""
    site_sid = topology.site_location_id
    control_sid = topology.control_location_id
    measurement_sids = topology.measurement_location_ids

    highlevel_configs = await _dashboard_fetch_highlevel_configs(dashboard_client, measurement_sids)
    site_highlevel_configs, station_highlevel_configs = _split_highlevel_configs_by_scope(
        highlevel_configs
    )
    mqtt_specs = _mqtt_specs_from_highlevel_configs(highlevel_configs)

    devices = await _dashboard_fetch_devices(dashboard_client, control_sid)
    if devices is None:
        return None, None

    station_devs, car_devs = _split_devices(devices)
    station_serial = topology.charging_station_serial
    serial_str = (
        topology.site_gateway_serial
        or topology.control_gateway_serial
        or station_serial
        or f"smappee-{site_sid}"
    )

    if station_serial and not station_devs:
        station_devs = [
            {
                "uuid": station_serial,
                "id": station_serial,
                "serialNumber": station_serial,
                "type": "CHARGINGSTATION",
            }
        ]
        _LOGGER.debug(
            "No CHARGINGSTATION smartdevice at control %s; using topology station serial %s",
            control_sid,
            station_serial,
        )

    if not station_devs and not car_devs and not station_serial:
        _LOGGER.info("No chargers at %s (%s); skipping", topology.control_name, control_sid)
        return None, None

    station_serial_to_connectors = await _fetch_dashboard_connector_mapping(
        dashboard_client, station_devs
    )
    if station_serial_to_connectors == {} and _dashboard_client_configured(dashboard_client):
        station_serial_to_connectors = _fallback_dashboard_connector_mapping(station_devs, car_devs)
    if station_serial_to_connectors == {}:
        station_serial_to_connectors = _fallback_highlevel_connector_mapping(
            station_serial,
            station_highlevel_configs,
        )
    if station_serial_to_connectors is None:
        station_serial_to_connectors = {}
    else:
        station_serial_to_connectors = _normalize_connector_mapping_station_keys(
            station_serial_to_connectors,
            station_serial or serial_str,
        )

    has_connector_mapping = any(
        (mapping.get("connectors") or {}) for mapping in station_serial_to_connectors.values()
    )

    allowed_station_serials = set(station_serial_to_connectors.keys())
    if allowed_station_serials:
        filtered_station_devs = [
            station
            for station in station_devs
            if (_station_serial(station) or _safe_str(station.get("uuid")))
            in allowed_station_serials
        ]
        if filtered_station_devs:
            station_devs = filtered_station_devs
        elif has_connector_mapping:
            station_devs = _station_devices_from_connector_mapping(station_serial_to_connectors)

    allowed_connector_uuids = {
        connector_uuid
        for mapping in station_serial_to_connectors.values()
        for connector_uuid in (mapping.get("connectors") or {})
    }
    if allowed_connector_uuids:
        car_devs = [
            connector
            for connector in car_devs
            if _connector_uuid(connector) in allowed_connector_uuids
        ]

    stations = _make_station_clients_with_mapping_fallback(
        serial_str,
        control_sid,
        station_devs,
        station_serial_to_connectors,
        has_connector_mapping,
        site_location_id=site_sid,
        site_name=topology.site_name,
        gateway_serial=topology.site_gateway_serial,
        gateway_type=topology.site_gateway_type,
        control_name=topology.control_name,
        control_uuid=topology.control_location_uuid,
        control_function_type=topology.control_function_type,
        station_metadata=station_serial_to_connectors,
    )

    if has_connector_mapping:
        _assign_connectors(
            stations,
            car_devs,
            station_serial_to_connectors,
            serial_str,
            control_sid,
        )
        total_assigned = sum(len(bucket.connectors) for bucket in stations.values())
        if total_assigned == 0 and len(stations) == 1:
            _fallback_assign(stations, car_devs, serial_str, control_sid)
        elif total_assigned == 0:
            _LOGGER.warning(
                "Connector mapping exists at control %s, but no connectors could be "
                "assigned across %d station buckets",
                control_sid,
                len(stations),
            )
    _log_station_runtime_shape("site-topology", stations)

    site_coordinator = await _create_site_coordinator(
        hass,
        topology=topology,
        update_interval=update_interval,
        config_entry=config_entry,
        highlevel_configs=site_highlevel_configs,
    )

    coordinators_ready = False
    try:
        await _create_coordinators(
            hass,
            stations,
            update_interval,
            config_entry=config_entry,
            dashboard_client=dashboard_client,
            highlevel_configs=station_highlevel_configs,
            start_tracking=start_runtime,
        )
        coordinators_ready = True
        mqtt = _setup_mqtt(
            hass,
            topology.site_location_uuid or topology.control_location_uuid,
            serial_str,
            site_sid,
            stations,
            client_id_prefix,
            update_interval,
            mqtt_specs=mqtt_specs,
            site_coordinator=site_coordinator,
            background_tasks=background_tasks,
            start_clients=start_runtime,
        )
    except BaseException:
        if coordinators_ready:
            for bucket in stations.values():
                coordinator = bucket.station_coordinator
                shutdown = getattr(coordinator, "async_shutdown", None)
                if callable(shutdown):
                    try:
                        result = shutdown()
                        if isawaitable(result):
                            await result
                    except Exception:  # noqa: BLE001 - rollback must continue
                        _LOGGER.debug("Station coordinator rollback failed", exc_info=True)
        shutdown = getattr(site_coordinator, "async_shutdown", None)
        if callable(shutdown):
            try:
                result = shutdown()
                if isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 - rollback must preserve original failure
                _LOGGER.debug("Site coordinator rollback failed", exc_info=True)
        raise

    for bucket in stations.values():
        bucket.mqtt = mqtt
        bucket.site_coordinator = site_coordinator
        bucket.highlevel_configs = highlevel_configs

    return stations, mqtt
