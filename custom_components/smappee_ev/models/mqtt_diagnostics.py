"""Import-neutral runtime models for MQTT routing diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MqttRouteDiagnosticTarget:
    """Identity-only snapshot of one configured MQTT route target."""

    target_type: str
    coordinator_id: int
    station_key: str | None = None


@dataclass(slots=True)
class MqttRoutingDiagnostics:
    """Runtime counters and route snapshots exposed through HA diagnostics."""

    site_location_id: int | str
    control_location_ids: tuple[int, ...]
    site_coordinator_id: int | None
    station_coordinator_ids: dict[str, int]
    configured_routes: dict[str, list[MqttRouteDiagnosticTarget]] = field(default_factory=dict)
    observed_routes: dict[str, list[MqttRouteDiagnosticTarget]] = field(default_factory=dict)
    client_count: int = 0
    started: bool = False
    messages_received: int = 0
    heartbeat_messages: int = 0
    routed_messages: int = 0
    unrouted_messages: int = 0
    target_deliveries: int = 0
    delivery_failures: int = 0
    messages_received_by_topic: dict[str, int] = field(default_factory=dict)
    last_message_rx: float | None = None
    last_routed_rx: float | None = None
    last_unrouted_rx: float | None = None
