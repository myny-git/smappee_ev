"""MQTT runtime wiring for Smappee EV setup."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from inspect import isawaitable
import logging

from .api.discovery import MqttChannelSpec
from .api.mqtt_gateway import SmappeeMqtt, redact_mqtt_topic
from .coordinator import SmappeeSiteCoordinator, SmappeeStationCoordinator
from .models.runtime_data import MqttRuntimeValue, SmappeeStationRuntime
from .models.state import MqttPayload
from .mqtt_specs import _group_mqtt_specs_by_credentials

_LOGGER = logging.getLogger(__name__)

MqttRouteTarget = SmappeeSiteCoordinator | SmappeeStationCoordinator


def _build_mqtt_routes(
    mqtt_specs: list[MqttChannelSpec] | None,
    site_coordinator: SmappeeSiteCoordinator | None,
    stations: dict[str, SmappeeStationRuntime],
) -> dict[str, list[MqttRouteTarget]]:
    """Build explicit MQTT topic routes for site and station coordinators."""
    routes: dict[str, list[MqttRouteTarget]] = {}
    station_coordinators: list[SmappeeStationCoordinator] = []
    for bucket in stations.values():
        coordinator = bucket.station_coordinator
        if coordinator is not None:
            station_coordinators.append(coordinator)
    for spec in mqtt_specs or []:
        if spec.role in {"grid", "production", "consumption", "always_on"}:
            if site_coordinator is not None:
                routes.setdefault(spec.topic, []).append(site_coordinator)
            continue
        if spec.role == "car_charger":
            for coord in station_coordinators:
                routes.setdefault(spec.topic, []).append(coord)

    deduped_routes: dict[str, list[MqttRouteTarget]] = {}
    for topic, targets in routes.items():
        seen: set[int] = set()
        deduped_targets: list[MqttRouteTarget] = []
        for target in targets:
            target_id = id(target)
            if target_id in seen:
                continue
            seen.add(target_id)
            deduped_targets.append(target)
        deduped_routes[topic] = deduped_targets
    return deduped_routes


def _mqtt_runtime_value(mqtt_clients: list[SmappeeMqtt]) -> MqttRuntimeValue:
    """Return a backwards-compatible MQTT runtime value."""
    if not mqtt_clients:
        return None
    if len(mqtt_clients) == 1:
        return mqtt_clients[0]
    return mqtt_clients


def _build_mqtt_clients(
    *,
    suuid: str | None,
    serial_str: str,
    sid: int | str,
    client_id_prefix: str,
    on_properties,
    on_connection_change,
    mqtt_specs: list[MqttChannelSpec] | None,
) -> list[SmappeeMqtt]:
    """Create one or more MQTT clients for a site based on credential sets."""
    if mqtt_specs is None:
        return [
            SmappeeMqtt(
                service_location_uuid=suuid,
                client_id=f"{client_id_prefix}-{sid}",
                serial_number=serial_str,
                on_properties=on_properties,
                service_location_id=sid,
                on_connection_change=on_connection_change,
            )
        ]

    spec_groups = _group_mqtt_specs_by_credentials(suuid, sid, mqtt_specs)
    mqtt_clients: list[SmappeeMqtt] = []
    for index, (specs, service_location_uuids, service_location_ids_by_uuid) in enumerate(
        spec_groups, start=1
    ):
        mqtt_clients.append(
            SmappeeMqtt(
                service_location_uuid=service_location_uuids[0]
                if service_location_uuids
                else suuid,
                service_location_uuids=service_location_uuids,
                service_location_ids_by_uuid=service_location_ids_by_uuid,
                client_id=f"{client_id_prefix}-{sid}"
                if len(spec_groups) == 1
                else f"{client_id_prefix}-{sid}-{index}",
                serial_number=serial_str,
                on_properties=on_properties,
                service_location_id=sid,
                on_connection_change=on_connection_change,
                mqtt_specs=specs,
            )
        )
    return mqtt_clients


def _log_mqtt_subscriptions(
    sid: int | str,
    suuid: str | None,
    mqtt_specs: list[MqttChannelSpec] | None,
) -> None:
    """Log MQTT subscriptions without leaking credentials."""
    seen_topics: set[str] = set()
    for spec in mqtt_specs or []:
        if spec.topic in seen_topics:
            continue
        seen_topics.add(spec.topic)
        _LOGGER.debug(
            "Smappee MQTT subscription: sid=%s topic=%s username_present=%s source=highlevel",
            spec.service_location_id,
            redact_mqtt_topic(spec.topic),
            bool(spec.username),
        )
    if suuid and not mqtt_specs:
        _LOGGER.debug(
            "Smappee MQTT subscription: sid=%s topic=%s username_present=True source=legacy",
            sid,
            redact_mqtt_topic(f"servicelocation/{suuid}/power"),
        )


def _handle_mqtt_refresh_done(
    refresh_tasks: dict[int, asyncio.Task], done_task: asyncio.Task, key: int
) -> None:
    """Drop a finished MQTT fallback refresh task and consume its exception."""
    refresh_tasks.pop(key, None)
    if done_task.cancelled():
        return
    try:
        exc = done_task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _LOGGER.debug("MQTT fallback refresh failed: %s", exc)


def _handle_mqtt_connection_change(
    up: bool,
    site_coordinator: SmappeeSiteCoordinator | None,
    stations: dict[str, SmappeeStationRuntime],
    update_interval: int,
    schedule_refresh,
) -> None:
    """Apply MQTT connection state to site and station coordinators."""
    if site_coordinator is not None:
        site_coordinator.apply_mqtt_connection_change(up)
    for bucket in stations.values():
        coord = bucket.station_coordinator
        if coord:
            if up:
                object.__setattr__(coord, "update_interval", None)
            elif coord.update_interval is None:
                object.__setattr__(coord, "update_interval", timedelta(seconds=update_interval))
                schedule_refresh(coord)
            coord.apply_mqtt_connection_change(up)


def _setup_mqtt(
    hass,
    suuid,
    serial_str,
    sid,
    stations,
    client_id_prefix: str,
    update_interval: int,
    mqtt_specs: list[MqttChannelSpec] | None = None,
    site_coordinator: SmappeeSiteCoordinator | None = None,
    background_tasks: set[asyncio.Task] | None = None,
) -> MqttRuntimeValue:
    if not suuid and not mqtt_specs:
        _LOGGER.warning("No serviceLocationUuid for %s; MQTT disabled for this site", sid)
        return None

    def _on_props(topic: str, payload: MqttPayload) -> None:
        targets = mqtt_routes.get(topic)
        if targets is None:
            targets = []
            if site_coordinator is not None and topic.endswith("/power") and not mqtt_specs:
                targets.append(site_coordinator)
            targets.extend(
                bucket.station_coordinator
                for bucket in stations.values()
                if bucket.station_coordinator is not None
            )
        for coord in targets:
            if coord:
                try:
                    coord.apply_mqtt_properties(topic, payload)
                except Exception:
                    _LOGGER.exception("Failed to apply MQTT properties from %s", topic)

    refresh_tasks: dict[int, asyncio.Task] = {}

    def _schedule_refresh(coord) -> None:
        if getattr(coord, "_shutting_down", False):
            return
        task_key = id(coord)
        existing = refresh_tasks.get(task_key)
        if existing is not None and not existing.done():
            return

        refresh = getattr(coord, "async_request_refresh", None)
        if not callable(refresh):
            return

        refresh_result = refresh()
        if not isawaitable(refresh_result):
            return

        task = hass.async_create_task(refresh_result)
        refresh_tasks[task_key] = task
        if background_tasks is not None:
            background_tasks.add(task)
        task.add_done_callback(
            lambda done_task, key=task_key: _handle_mqtt_refresh_done(refresh_tasks, done_task, key)
        )
        if background_tasks is not None:
            task.add_done_callback(background_tasks.discard)

    def _on_conn(up: bool) -> None:
        _handle_mqtt_connection_change(
            up,
            site_coordinator,
            stations,
            update_interval,
            _schedule_refresh,
        )

    mqtt_routes = _build_mqtt_routes(mqtt_specs, site_coordinator, stations)

    mqtt_clients = _build_mqtt_clients(
        suuid=suuid,
        serial_str=serial_str,
        sid=sid,
        client_id_prefix=client_id_prefix,
        on_properties=_on_props,
        on_connection_change=_on_conn,
        mqtt_specs=mqtt_specs,
    )
    for mqtt in mqtt_clients:
        mqtt.track_start_task(hass.async_create_task(mqtt.start()))

    _log_mqtt_subscriptions(sid, suuid, mqtt_specs)
    return _mqtt_runtime_value(mqtt_clients)


def _iter_mqtt_clients(value: object) -> list[object]:
    """Return MQTT clients from legacy single-client or grouped-client runtime values."""
    if isinstance(value, list | tuple):
        return list(value)
    return [value] if value is not None else []


def _mqtt_client_count(mqtt_by_site: dict[int, MqttRuntimeValue]) -> int:
    """Return the total number of MQTT clients across all sites."""
    return sum(len(_iter_mqtt_clients(mqtt)) for mqtt in mqtt_by_site.values())
