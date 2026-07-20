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
    SmappeeSiteRuntime,
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
                own_config = highlevel_configs.get(bucket.control_location_id)
                kwargs["highlevel_configs"] = (
                    {bucket.control_location_id: own_config} if own_config is not None else {}
                )
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


def _topology_sort_key(topology: SmappeeLocationTopology) -> tuple[str, str]:
    """Return a stable order independent of Dashboard response timing."""
    return str(topology.control_location_id), topology.charging_station_serial or ""


def _station_identity(station_key: str, bucket: SmappeeStationRuntime) -> str:
    """Return the best stable physical identity available for a station."""
    serial = str(bucket.charging_station_serial or "").strip().casefold()
    return f"serial:{serial}" if serial else f"uuid:{station_key.casefold()}"


def _merge_station_metadata(
    preferred: SmappeeStationRuntime,
    fallback: SmappeeStationRuntime,
) -> None:
    """Fill missing metadata without changing the preferred station identity."""
    for attr in (
        "site_name",
        "gateway_serial",
        "gateway_type",
        "control_name",
        "control_uuid",
        "control_function_type",
        "station_name",
        "charging_station_model",
    ):
        if getattr(preferred, attr) is None:
            setattr(preferred, attr, getattr(fallback, attr))
    for connector_key, connector in fallback.connectors.items():
        preferred.connectors.setdefault(connector_key, connector)
    for led_key, led in fallback.led_devices.items():
        preferred.led_devices.setdefault(led_key, led)


def _deduplicate_stations(
    candidates: list[tuple[SmappeeLocationTopology, dict[str, SmappeeStationRuntime]]],
) -> dict[str, SmappeeStationRuntime]:
    """Choose one deterministic runtime bucket for every physical station."""
    selected: dict[str, tuple[str, SmappeeStationRuntime, bool]] = {}
    for topology, stations in candidates:
        direct_serial = str(topology.charging_station_serial or "").strip().casefold()
        for station_key, bucket in sorted(stations.items()):
            identity = _station_identity(station_key, bucket)
            is_direct = bool(
                direct_serial
                and str(bucket.charging_station_serial or "").strip().casefold() == direct_serial
            )
            current = selected.get(identity)
            if current is None:
                selected[identity] = (station_key, bucket, is_direct)
                continue

            current_key, current_bucket, current_is_direct = current
            if is_direct and not current_is_direct:
                _merge_station_metadata(bucket, current_bucket)
                selected[identity] = (station_key, bucket, True)
                continue

            _merge_station_metadata(current_bucket, bucket)
            if (
                is_direct
                and current_is_direct
                and bucket.control_location_id != (current_bucket.control_location_id)
            ):
                _LOGGER.warning(
                    "Station was advertised as direct control by multiple service locations; "
                    "using the deterministic first match"
                )
            selected[identity] = (current_key, current_bucket, current_is_direct)

    return {
        station_key: bucket
        for station_key, bucket, _is_direct in sorted(
            selected.values(), key=lambda item: (item[0], item[1].control_location_id)
        )
    }


async def _prepare_control_stations(
    topology: SmappeeLocationTopology,
    dashboard_client: SmappeeDashboardClient | None,
    station_highlevel_configs: HighLevelConfigMap,
) -> tuple[dict[str, SmappeeStationRuntime], str]:
    """Prepare station candidates for one control location without coordinators."""
    site_sid = topology.site_location_id
    control_sid = topology.control_location_id

    devices = await _dashboard_fetch_devices(dashboard_client, control_sid)
    if devices is None:
        return {}, f"smappee-{site_sid}"

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
        return {}, serial_str

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
    return stations, serial_str


async def _prepare_site_topologies(
    hass: HomeAssistant,
    topologies: list[SmappeeLocationTopology],
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
    dashboard_client: SmappeeDashboardClient | None = None,
    background_tasks: set[asyncio.Task] | None = None,
    start_runtime: bool = True,
) -> tuple[SmappeeSiteRuntime | None, MqttRuntimeValue]:
    """Prepare one complete physical site from all of its control topologies."""
    if not topologies:
        return None, None

    ordered = sorted(topologies, key=_topology_sort_key)
    primary = ordered[0]
    site_sid = primary.site_location_id
    if any(topology.site_location_id != site_sid for topology in ordered):
        raise ValueError("Cannot prepare topologies from different physical sites together")

    measurement_sids = list(
        dict.fromkeys(
            measurement_sid
            for topology in ordered
            for measurement_sid in topology.measurement_location_ids
        )
    )
    highlevel_configs = await _dashboard_fetch_highlevel_configs(dashboard_client, measurement_sids)
    site_highlevel_configs, station_highlevel_configs = _split_highlevel_configs_by_scope(
        highlevel_configs
    )

    station_candidates: list[tuple[SmappeeLocationTopology, dict[str, SmappeeStationRuntime]]] = []
    serials: list[str] = []
    for topology in ordered:
        stations, serial_str = await _prepare_control_stations(
            topology,
            dashboard_client,
            station_highlevel_configs,
        )
        if stations:
            station_candidates.append((topology, stations))
        serials.append(serial_str)

    stations = _deduplicate_stations(station_candidates)
    if not stations:
        return None, None
    _log_station_runtime_shape("merged-site", stations)

    site = SmappeeSiteRuntime(
        site_location_id=site_sid,
        site_name=primary.site_name,
        site_function_type=primary.site_function_type,
        site_uuid=primary.site_location_uuid,
        gateway_serial=primary.site_gateway_serial,
        gateway_type=primary.site_gateway_type,
        control_location_ids=list(dict.fromkeys(t.control_location_id for t in ordered)),
        measurement_location_ids=measurement_sids,
        highlevel_configs=highlevel_configs,
        stations=stations,
    )

    site_coordinator = await _create_site_coordinator(
        hass,
        topology=primary,
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
        mqtt_specs = _mqtt_specs_from_highlevel_configs(highlevel_configs)
        serial_str = (
            primary.site_gateway_serial
            or next((serial for serial in serials if serial), None)
            or f"smappee-{site_sid}"
        )
        mqtt = _setup_mqtt(
            hass,
            primary.site_location_uuid or primary.control_location_uuid,
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
        own_config = station_highlevel_configs.get(bucket.control_location_id)
        bucket.highlevel_configs = (
            {bucket.control_location_id: own_config} if own_config is not None else {}
        )

    site.site_coordinator = site_coordinator
    site.mqtt_clients = mqtt
    return site, mqtt


async def _prepare_topology(
    hass: HomeAssistant,
    topology: SmappeeLocationTopology,
    update_interval: int,
    client_id_prefix: str,
    config_entry: SmappeeEvConfigEntry | None = None,
    dashboard_client: SmappeeDashboardClient | None = None,
    background_tasks: set[asyncio.Task] | None = None,
    start_runtime: bool = True,
) -> tuple[dict[str, SmappeeStationRuntime] | None, MqttRuntimeValue]:
    """Compatibility wrapper for preparing one Dashboard topology."""
    site, mqtt = await _prepare_site_topologies(
        hass,
        [topology],
        update_interval,
        client_id_prefix,
        config_entry=config_entry,
        dashboard_client=dashboard_client,
        background_tasks=background_tasks,
        start_runtime=start_runtime,
    )
    return (site.stations if site is not None else None), mqtt
