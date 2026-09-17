"""Site battery measurements, independent of existing grid/PV/charger mappings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite

from ..api.discovery import MqttChannelSpec, parse_mqtt_channel_specs_from_highlevel
from ..const import MQTT_REAL_POWER_FRESHNESS_TIMEOUT
from ..models.state import HighLevelConfigMap, MqttPayload, SiteState
from .power import _MQTT_PATH_RE, _mqtt_channel_topic


@dataclass(frozen=True)
class StoragePath:
    """One configured, signed MQTT measurement."""

    topic: str
    field: str
    index: int
    multiplier: float


def _storage_paths(channel: object, fields: set[str]) -> list[StoragePath]:
    """Read usable paths without assuming fixed array positions or directions."""
    if not isinstance(channel, dict) or not (topic := _mqtt_channel_topic(channel)):
        return []
    paths = []
    for aspect in channel.get("aspectPaths") or []:
        if not isinstance(aspect, dict):
            continue
        match = _MQTT_PATH_RE.fullmatch(str(aspect.get("path") or ""))
        if not match or match[1] not in fields:
            continue
        # Power may omit its identity multiplier. Energy direction must be
        # explicit; never infer charging/discharging from the array name alone.
        default = 1 if match[1] in {"activePowerData", "channelData"} else None
        multiplier = aspect.get("multiplier", default)
        if (
            isinstance(multiplier, bool)
            or not isinstance(multiplier, int | float)
            or not isfinite(multiplier)
            or multiplier == 0
        ):
            continue
        paths.append(StoragePath(topic, match[1], int(match[2]), float(multiplier)))
    return paths


class StorageMeasurements:
    """Aggregate configured batteries/phases, keeping unrelated sensors untouched."""

    def __init__(self, configs: HighLevelConfigMap) -> None:
        self.paths: dict[str, list[StoragePath]] = {}
        self._values: dict[tuple[str, str], float] = {}
        self.last_power_rx_by_topic: dict[str, datetime] = {}
        for sid, config in configs.items():
            for spec in parse_mqtt_channel_specs_from_highlevel(sid, config):
                self._add_spec(spec)

    @classmethod
    def from_specs(cls, specs: list[MqttChannelSpec]) -> StorageMeasurements:
        """Restore mapping from existing bootstrap specs, never from live values."""
        measurements = cls({})
        for spec in specs:
            measurements._add_spec(spec)
        return measurements

    @property
    def metrics(self) -> frozenset[str]:
        """Metrics that can be exposed, even before the first MQTT message."""
        return frozenset(self.paths)

    @property
    def power_available(self) -> bool:
        """Require a recent complete power measurement from every battery topic."""
        topics = {path.topic for path in self.paths.get("power", [])}
        now = datetime.now(UTC)
        return bool(topics) and all(
            topic in self.last_power_rx_by_topic
            and now - self.last_power_rx_by_topic[topic] <= MQTT_REAL_POWER_FRESHNESS_TIMEOUT
            for topic in topics
        )

    def _add_spec(self, spec: MqttChannelSpec) -> None:
        if spec.role != "storage":
            return
        channel = {"protocol": "MQTT", "name": spec.topic, "aspectPaths": spec.aspect_paths}
        if spec.metric == "activePower":
            for path in _storage_paths(channel, {"activePowerData", "channelData"}):
                self._add_path("power", path)
        if spec.metric != "meterReadings":
            return
        for path in _storage_paths(channel, {"importActiveEnergyData", "exportActiveEnergyData"}):
            # Smappee STORAGE: negative = charging, positive = discharging.
            metric = "charged_energy" if path.multiplier < 0 else "discharged_energy"
            self._add_path(metric, path)

    def _add_path(self, metric: str, path: StoragePath) -> None:
        paths = self.paths.setdefault(metric, [])
        # Configurations from multiple control locations may repeat a site path.
        if path not in paths:
            paths.append(path)

    def apply(self, site: SiteState, topic: str, payload: MqttPayload) -> bool:
        """Apply complete groups only; missing/invalid values never become zero."""
        changed = False
        was_available = self.power_available
        for metric, paths in self.paths.items():
            group = [path for path in paths if path.topic == topic]
            if not group:
                continue
            total = self._group_total(group, payload, energy=metric != "power")
            if total is None:
                continue
            if metric == "power":
                self.last_power_rx_by_topic[topic] = datetime.now(UTC)
            self._values[metric, topic] = total
            topics = {path.topic for path in paths}
            if any((metric, source) not in self._values for source in topics):
                continue
            value = sum(self._values[metric, source] for source in topics)
            attr = "storage_power_total" if metric == "power" else f"storage_{metric}_kwh"
            if metric != "power":
                value = round(value / 1000, 3)
            if getattr(site, attr) != value:
                setattr(site, attr, value)
                changed = True
        # Recovery must notify entities even if the measured value is unchanged.
        return changed or was_available != self.power_available

    @staticmethod
    def _group_total(
        paths: list[StoragePath], payload: MqttPayload, *, energy: bool
    ) -> float | None:
        total = 0.0
        for path in paths:
            values = payload.get(path.field)
            if not isinstance(values, list) or path.index >= len(values):
                return None
            value = values[path.index]
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not isfinite(value)
                or (energy and value < 0)
            ):
                return None
            total += value * (abs(path.multiplier) if energy else path.multiplier)
        return total if isfinite(total) else None
