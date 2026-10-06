"""Freshness of accepted measurements, independently of MQTT traffic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..const import MQTT_REAL_POWER_FRESHNESS_TIMEOUT


@dataclass
class MqttApplyResult:
    """Fields actually accepted, including unchanged values and explicit zeros."""

    site_fields: set[str] = field(default_factory=set)
    connector_fields: dict[str, set[str]] = field(default_factory=dict)

    def connector(self, uuid: str) -> set[str]:
        return self.connector_fields.setdefault(uuid, set())


class MeasurementFreshness:
    """Store the last valid receive time for each site/connector measurement."""

    def __init__(self) -> None:
        self.received: dict[tuple[str | None, str], datetime] = {}

    def is_fresh(self, name: str, connector_uuid: str | None = None) -> bool:
        last = self.received.get((connector_uuid, name))
        age = datetime.now(UTC) - last if last is not None else None
        return age is not None and 0 <= age.total_seconds() <= (
            MQTT_REAL_POWER_FRESHNESS_TIMEOUT.total_seconds()
        )

    def record(self, result: MqttApplyResult) -> bool:
        """Return whether a previously unavailable measurement recovered."""
        recovered = False
        now = datetime.now(UTC)
        groups = [(None, result.site_fields), *result.connector_fields.items()]
        for uuid, names in groups:
            for name in names:
                recovered |= not self.is_fresh(name, uuid)
                self.received[uuid, name] = now
        return recovered
