"""MQTT channel spec helpers for Smappee EV runtime setup."""

from __future__ import annotations

import logging
import re

from .api.discovery import MqttChannelSpec, parse_mqtt_channel_specs_from_highlevel
from .api.mqtt_gateway import redact_mqtt_topic
from .models.state import HighLevelConfigMap
from .topology import safe_str

_LOGGER = logging.getLogger(__name__)


def mqtt_specs_from_highlevel_configs(
    configs: HighLevelConfigMap, *, log: bool = True
) -> list[MqttChannelSpec]:
    """Return all highlevel MQTT specs; ``log=False`` skips the per-spec debug dump."""
    specs: list[MqttChannelSpec] = []
    for sid, cfg in configs.items():
        parsed = parse_mqtt_channel_specs_from_highlevel(sid, cfg)
        for spec in parsed if log else ():
            _LOGGER.debug(
                "Smappee highlevel mapping: sid=%s role=%s metric=%s topic=%s paths=%s",
                spec.service_location_id,
                spec.role,
                spec.metric,
                redact_mqtt_topic(spec.topic),
                spec.aspect_paths,
            )
        specs.extend(parsed)
    return specs


def split_highlevel_configs_by_scope(
    configs: HighLevelConfigMap,
) -> tuple[HighLevelConfigMap, HighLevelConfigMap]:
    """Return ``(site_configs, station_configs)`` based on highlevel MQTT roles."""
    site_configs: HighLevelConfigMap = {}
    station_configs: HighLevelConfigMap = {}
    for sid, cfg in configs.items():
        specs = parse_mqtt_channel_specs_from_highlevel(sid, cfg)
        if any(
            spec.role
            in {"grid", "production", "storage", "consumption", "production_total", "always_on"}
            for spec in specs
        ):
            site_configs[sid] = cfg
        if any(spec.role == "car_charger" for spec in specs):
            station_configs[sid] = cfg
    return site_configs, station_configs


def _service_location_uuid_from_mqtt_topic(topic: str | None) -> str | None:
    """Return the service-location UUID embedded in a Dashboard MQTT topic."""
    if not topic:
        return None
    match = re.match(r"^servicelocation/([^/]+)/", topic)
    return safe_str(match.group(1)) if match else None


def group_mqtt_specs_by_credentials(
    suuid: str | None,
    mqtt_specs: list[MqttChannelSpec],
) -> list[
    tuple[
        list[MqttChannelSpec],
        list[str],
        dict[str, int | str],
    ]
]:
    """Group MQTT specs so every MQTT connection uses one credential set."""
    grouped_specs: dict[tuple[str | None, str | None], list[MqttChannelSpec]] = {}
    service_location_ids_by_group: dict[tuple[str | None, str | None], dict[str, int | str]] = {}

    for spec in mqtt_specs:
        spec_suuid = _service_location_uuid_from_mqtt_topic(spec.topic) or suuid
        username = spec.username or spec_suuid or suuid
        password = spec.password or spec_suuid or suuid
        key = (username, password)
        grouped_specs.setdefault(key, []).append(spec)
        if spec_suuid:
            service_location_ids_by_group.setdefault(key, {})[spec_suuid] = spec.service_location_id

    groups = []
    for key, specs in grouped_specs.items():
        service_location_ids_by_uuid = service_location_ids_by_group.get(key, {})
        service_location_uuids = list(service_location_ids_by_uuid)
        groups.append((specs, service_location_uuids, service_location_ids_by_uuid))
    return groups
