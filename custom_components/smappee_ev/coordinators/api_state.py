"""Station REST/API fetch and merge helpers for Smappee coordinators."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
import logging

from aiohttp import ClientError
from homeassistant.exceptions import ConfigEntryAuthFailed

from ..api.device_handle import SmappeeDeviceHandle
from ..api.errors import SmappeeError
from ..const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT
from ..helpers import (
    anonymize_uuid,
    dashboard_property_value,
    percentage_to_current,
    resolve_connector_current_range,
)
from ..models.state import ConnectorState, StationState
from .base import CoordinatorMixin
from .power import _to_int

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConnectorRestSnapshot:
    """Connector REST state with the bounds exactly as reported by the API.

    ``ConnectorState`` always contains a valid range for runtime consumers. The
    optional reported bounds keep a missing REST property distinguishable from
    an explicitly reported default until the snapshot is merged.
    """

    state: ConnectorState
    reported_min_current: int | None
    reported_max_current: int | None


class StationApiMixin(CoordinatorMixin):
    """REST/API reachability, fetching, and merge helpers."""

    _station_api_available: bool | None
    _connector_api_available: dict[str, bool]

    def _log_connector_api_transition(
        self, uuid: str, available: bool, err: Exception | None = None
    ) -> None:
        """Log connector REST reachability only when it changes."""
        previous = self._connector_api_available.get(uuid)
        if previous is available:
            if not available and err is not None:
                _LOGGER.debug("Connector %s update still failing: %s", anonymize_uuid(uuid), err)
            return

        self._connector_api_available[uuid] = available
        if available:
            if previous is False:
                _LOGGER.info("Connector %s update recovered", anonymize_uuid(uuid))
            return

        if err is not None:
            _LOGGER.warning(
                "Connector %s update failed; marking unavailable: %s", anonymize_uuid(uuid), err
            )

    def _log_station_api_transition(self, available: bool, err: Exception | None = None) -> None:
        """Log station REST reachability only when it changes."""
        previous = self._station_api_available
        if previous is available:
            if not available and err is not None:
                _LOGGER.debug("Station update still failing: %s", err)
            return

        self._station_api_available = available
        if available:
            if previous is False:
                _LOGGER.info("Station update recovered")
            return

        if err is not None:
            _LOGGER.warning("Station update failed; marking unavailable: %s", err)

    @staticmethod
    def _merge_station_rest_state(prev: StationState | None, rest: StationState) -> StationState:
        """Merge REST station fields into the previous MQTT-rich station state."""
        if prev is None:
            return rest
        return replace(
            prev,
            led_brightness=rest.led_brightness
            if rest.led_brightness is not None
            else prev.led_brightness,
            dashboard_led_device_id=prev.dashboard_led_device_id,
            api_available=rest.api_available,
        )

    @staticmethod
    def _rest_selected_current_limit(
        prev: ConnectorState,
        rest: ConnectorState,
        min_current: int,
        max_current: int,
    ) -> float | None:
        """Derive the Ampere setpoint from the polled percentage limit.

        REST only exposes ``percentageLimit``, so without this a stale
        MQTT-derived ``selected_current_limit`` could never be corrected by a
        poll. Mirrors the MQTT gate: the percentage only drives the setpoint
        while the connector runs without an optimization strategy.
        """
        if (prev.optimization_strategy or "").upper() != "NONE":
            return prev.selected_current_limit
        if rest.selected_percentage_limit is None:
            return prev.selected_current_limit
        return percentage_to_current(rest.selected_percentage_limit, min_current, max_current)

    @staticmethod
    def _merge_connector_rest_state(
        prev: ConnectorState | None,
        rest: ConnectorState | ConnectorRestSnapshot,
    ) -> ConnectorState:
        """Merge REST connector fields into the previous MQTT-rich connector state."""
        if isinstance(rest, ConnectorRestSnapshot):
            rest_state = rest.state
            reported_min = rest.reported_min_current
            reported_max = rest.reported_max_current
        else:
            # Direct ConnectorState callers represent a complete REST range.
            rest_state = rest
            reported_min = rest.min_current
            reported_max = rest.max_current

        min_current, max_current = resolve_connector_current_range(
            previous_min=prev.min_current if prev is not None else None,
            previous_max=prev.max_current if prev is not None else None,
            reported_min=reported_min,
            reported_max=reported_max,
        )
        if prev is None:
            if (min_current, max_current) == (
                rest_state.min_current,
                rest_state.max_current,
            ):
                return rest_state
            return replace(rest_state, min_current=min_current, max_current=max_current)
        return replace(
            prev,
            connector_number=rest_state.connector_number,
            session_state=rest_state.session_state
            if rest_state.session_state != "Initialize"
            else prev.session_state,
            selected_current_limit=StationApiMixin._rest_selected_current_limit(
                prev, rest_state, min_current, max_current
            ),
            selected_percentage_limit=rest_state.selected_percentage_limit,
            selected_mode=rest_state.selected_mode
            if rest_state.selected_mode is not None
            else prev.selected_mode,
            min_current=min_current,
            max_current=max_current,
            min_surpluspct=rest_state.min_surpluspct
            if rest_state.min_surpluspct is not None
            else prev.min_surpluspct,
            support_grid=rest_state.support_grid
            if rest_state.support_grid is not None
            else prev.support_grid,
            api_available=rest_state.api_available,
        )

    async def _fetch_station_state(self, client: SmappeeDeviceHandle) -> StationState:
        """Read LED brightness by scanning all smartdevices for the station."""
        led_brightness: int | None = None
        try:
            devices = await client.async_get_smartdevices()
            if devices is None:
                err = RuntimeError("smartdevice list request returned no data")
                self._log_station_api_transition(False, err)
                return StationState(
                    led_brightness=led_brightness,
                    available=True,
                    api_available=False,
                )
            for dev in devices:
                for prop in dev.get("configurationProperties", []):
                    spec = prop.get("spec", {}) or {}
                    if (
                        spec.get("name")
                        == "etc.smart.device.type.car.charger.led.config.brightness"
                    ):
                        val = dashboard_property_value(prop)
                        if val is not None:
                            with suppress(TypeError, ValueError):
                                led_brightness = int(val)
                        break
            self._log_station_api_transition(True)
        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            raise
        except (SmappeeError, TimeoutError, ClientError, RuntimeError) as err:
            self._log_station_api_transition(False, err)
            return StationState(led_brightness=led_brightness, available=True, api_available=False)

        return StationState(led_brightness=led_brightness, available=True, api_available=True)

    async def _fetch_connector_state(
        self, client: SmappeeDeviceHandle
    ) -> ConnectorRestSnapshot:
        """Read one connector's properties/config from its smartdevice."""
        session_state = "Initialize"
        selected_percentage: int | None = None
        selected_mode: str | None = None
        reported_min_current: int | None = None
        reported_max_current: int | None = None
        min_surpluspct: int | None = None
        support_grid: int | None = None

        data = await client.async_get_smartdevice(client.smart_device_id)
        if data is None:
            raise RuntimeError(f"smartdevice fetch {client.smart_device_id} returned no data")

        for prop in data.get("properties", []):
            spec = prop.get("spec", {}) or {}
            name = spec.get("name")
            val = prop.get("value")
            if name == "chargingState":
                session_state = val or session_state
            elif name == "percentageLimit":
                with suppress(TypeError, ValueError):
                    selected_percentage = int(val)

        for prop in data.get("configurationProperties", []):
            spec = prop.get("spec", {}) or {}
            name = spec.get("name")
            val = dashboard_property_value(prop)
            if name == "etc.smart.device.type.car.charger.config.max.current":
                with suppress(TypeError, ValueError):
                    reported_max_current = int(val)
            elif name == "etc.smart.device.type.car.charger.config.min.current":
                with suppress(TypeError, ValueError):
                    reported_min_current = int(val)
            elif name == "etc.smart.device.type.car.charger.config.min.excesspct":
                if val is not None:
                    with suppress(TypeError, ValueError):
                        min_surpluspct = int(val)
            elif name == "etc.smart.device.type.car.charger.config.max.gridassistanceamps":
                with suppress(TypeError, ValueError):
                    support_grid = _to_int(val)

        return ConnectorRestSnapshot(
            state=ConnectorState(
                connector_number=getattr(client, "connector_number", 1),
                session_state=session_state,
                selected_current_limit=None,
                selected_percentage_limit=selected_percentage,
                selected_mode=selected_mode,
                min_current=reported_min_current
                if reported_min_current is not None
                else DEFAULT_MIN_CURRENT,
                max_current=reported_max_current
                if reported_max_current is not None
                else DEFAULT_MAX_CURRENT,
                min_surpluspct=min_surpluspct,
                support_grid=support_grid,
                api_available=True,
            ),
            reported_min_current=reported_min_current,
            reported_max_current=reported_max_current,
        )
