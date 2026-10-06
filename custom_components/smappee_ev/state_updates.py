"""Publish changes on current state, never on a snapshot held across an await."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, fields
from typing import Any, Protocol

from homeassistant.exceptions import ConfigEntryAuthFailed

from .models.state import ConnectorState, IntegrationData, StationState


class StateCoordinator(Protocol):
    """State publishing contract, independent of the concrete coordinator."""

    @property
    def data(self) -> IntegrationData | None: ...

    def async_set_updated_data(self, data: IntegrationData) -> None: ...

    def record_confirmed_write(
        self, connector_uuid: str | None, changes: dict[str, Any]
    ) -> None: ...


async def async_write[T](action: Awaitable[T], refresh: Callable[[], None]) -> T:
    """Reconcile successful or uncertain writes; preserve auth and cancellation."""
    try:
        result = await action
    except ConfigEntryAuthFailed:
        raise
    except (Exception, asyncio.CancelledError):
        # A failed response does not prove that the server rejected the write.
        refresh()
        raise
    refresh()
    return result


def update_station(coordinator: StateCoordinator, **changes: Any) -> None:
    """Apply only these fields to the current station, without yielding."""
    current = coordinator.data
    if not current:
        return
    for name, value in changes.items():
        setattr(current.station, name, value)
    coordinator.record_confirmed_write(None, changes)
    coordinator.async_set_updated_data(current)


def update_connector(coordinator: StateCoordinator, connector_uuid: str, **changes: Any) -> None:
    """Apply only these fields to the current connector, without yielding."""
    current = coordinator.data
    if current is None or (connector := current.connectors.get(connector_uuid)) is None:
        return
    for name, value in changes.items():
        setattr(connector, name, value)
    coordinator.record_confirmed_write(connector_uuid, changes)
    coordinator.async_set_updated_data(current)


@dataclass
class StateChanges:
    """Field changes between isolated station/connector state snapshots."""

    station: dict[str, Any]
    connectors: dict[str, dict[str, Any]]

    @classmethod
    def between(cls, before: IntegrationData, after: IntegrationData) -> StateChanges:
        def changed_fields(
            old: StationState | ConnectorState, new: StationState | ConnectorState
        ) -> dict[str, Any]:
            return {
                field.name: getattr(new, field.name)
                for field in fields(new)
                if getattr(old, field.name) != getattr(new, field.name)
            }

        return cls(
            changed_fields(before.station, after.station),
            {
                uuid: changed_fields(before.connectors[uuid], connector)
                for uuid, connector in after.connectors.items()
                if uuid in before.connectors
            },
        )

    def apply(self, current: IntegrationData) -> bool:
        """Apply changed fields synchronously, preserving all untouched state."""
        changed = False
        targets: list[tuple[StationState | ConnectorState, dict[str, Any]]] = [
            (current.station, self.station)
        ]
        targets.extend(
            (current.connectors[uuid], changes)
            for uuid, changes in self.connectors.items()
            if uuid in current.connectors
        )
        for target, changes in targets:
            for name, value in changes.items():
                if getattr(target, name) != value:
                    setattr(target, name, value)
                    changed = True
        return changed


def state_staging_copy(current: IntegrationData) -> IntegrationData:
    """Copy for comparisons/enrichment; never publish this staging snapshot."""
    return deepcopy(current)


class StateConfirmations:
    """Track confirmed fields even when a command or MQTT repeats their value."""

    def __init__(self) -> None:
        self.version = 0
        self._fields: dict[tuple[str | None, str], int] = {}

    def record(self, connector_uuid: str | None, names: Iterable[str]) -> None:
        self.version += 1
        for name in names:
            self._fields[connector_uuid, name] = self.version

    def since(self, version: int, current: IntegrationData) -> StateChanges:
        """Preserve current values of fields confirmed after remote I/O began."""
        station: dict[str, Any] = {}
        connectors: dict[str, dict[str, Any]] = {}
        for (uuid, name), updated in self._fields.items():
            if updated <= version:
                continue
            if uuid is None:
                station[name] = getattr(current.station, name)
            elif uuid in current.connectors:
                connectors.setdefault(uuid, {})[name] = getattr(current.connectors[uuid], name)
        return StateChanges(station, connectors)
