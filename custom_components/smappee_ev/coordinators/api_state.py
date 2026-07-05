"""Station REST/API fetch and merge helpers for Smappee coordinators."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
import logging

from aiohttp import ClientError

from ..api.device_handle import SmappeeDeviceHandle
from ..const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT
from ..helpers import anonymize_uuid
from ..models.state import ConnectorState, StationState
from .base import CoordinatorMixin
from .power import _to_int

_LOGGER = logging.getLogger(__name__)


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
    def _merge_connector_rest_state(
        prev: ConnectorState | None, rest: ConnectorState
    ) -> ConnectorState:
        """Merge REST connector fields into the previous MQTT-rich connector state."""
        if prev is None:
            return rest
        return replace(
            prev,
            connector_number=rest.connector_number,
            session_state=rest.session_state,
            selected_current_limit=rest.selected_current_limit
            if rest.selected_current_limit is not None
            else prev.selected_current_limit,
            selected_percentage_limit=rest.selected_percentage_limit,
            selected_mode=rest.selected_mode
            if rest.selected_mode is not None
            else prev.selected_mode,
            min_current=rest.min_current,
            max_current=rest.max_current,
            min_surpluspct=rest.min_surpluspct,
            support_grid=rest.support_grid,
            api_available=rest.api_available,
        )

    async def _fetch_station_state(self, client: SmappeeDeviceHandle) -> StationState:
        """Read LED brightness by scanning all smartdevices for the station."""
        led_brightness: int | None = None
        try:
            devices = await client.async_get_smartdevices()
            for dev in devices or []:
                for prop in dev.get("configurationProperties", []):
                    spec = prop.get("spec", {}) or {}
                    if (
                        spec.get("name")
                        == "etc.smart.device.type.car.charger.led.config.brightness"
                    ):
                        raw = prop.get("value")
                        val = raw.get("value") if isinstance(raw, dict) else raw
                        if val is not None:
                            with suppress(TypeError, ValueError):
                                led_brightness = int(val)
                        break
            self._log_station_api_transition(True)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, ClientError, RuntimeError) as err:
            self._log_station_api_transition(False, err)
            return StationState(led_brightness=led_brightness, available=True, api_available=False)

        return StationState(led_brightness=led_brightness, available=True, api_available=True)

    async def _fetch_connector_state(self, client: SmappeeDeviceHandle) -> ConnectorState:
        """Read one connector's properties/config from its smartdevice."""
        session_state = "Initialize"
        selected_percentage: int | None = None
        selected_current: int | None = None
        selected_mode: str | None = None
        min_current = DEFAULT_MIN_CURRENT
        max_current = DEFAULT_MAX_CURRENT
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
            raw = prop.get("value")
            val = raw.get("value") if isinstance(raw, dict) else raw
            if name == "etc.smart.device.type.car.charger.config.max.current":
                with suppress(TypeError, ValueError):
                    max_current = _to_int(val, default=max_current)
            elif name == "etc.smart.device.type.car.charger.config.min.current":
                with suppress(TypeError, ValueError):
                    min_current = _to_int(val, default=min_current)
            elif name == "etc.smart.device.type.car.charger.config.min.excesspct":
                if val is not None:
                    with suppress(TypeError, ValueError):
                        min_surpluspct = int(val)
            elif name == "etc.smart.device.type.car.charger.config.max.gridassistanceamps":
                with suppress(TypeError, ValueError):
                    support_grid = _to_int(val)

        return ConnectorState(
            connector_number=getattr(client, "connector_number", 1),
            session_state=session_state,
            selected_current_limit=selected_current,
            selected_percentage_limit=selected_percentage,
            selected_mode=selected_mode,
            min_current=min_current,
            max_current=max_current,
            min_surpluspct=min_surpluspct,
            support_grid=support_grid,
            api_available=True,
        )
