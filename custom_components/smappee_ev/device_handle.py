from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class SmappeeDeviceHandle:
    """Per-device command handle. No polling/state; the coordinator owns state."""

    def __init__(
        self,
        serial: str,
        smart_device_uuid: str,
        smart_device_id: str,
        service_location_id: str,
        *,
        connector_number: int | None = None,
        is_station: bool = False,
        charging_station_serial: str | None = None,
    ):
        self.serial = serial
        self.charging_station_serial = charging_station_serial
        self.smart_device_uuid = smart_device_uuid
        self.smart_device_id = smart_device_id
        self.service_location_id = service_location_id
        self.connector_number = connector_number

        self.is_station = is_station
        self.station_action_uuid: str | None = None
        self.dashboard_client: Any | None = None
        self.dashboard_device_id: str | None = None
        # Stateless: no per-connector mutable charging attributes kept here.
        # All dynamic values live in Coordinator/ConnectorState.
        self._callbacks: set[Callable[..., Any]] = set()  # kept for future hook use

        _LOGGER.info(
            "SmappeeDeviceHandle initialized (serial=%s, connector=%s, station=%s)",
            self.serial,
            self.connector_number,
            self.is_station,
        )

    @property
    def serial_id(self) -> str:
        return self.serial

    def _dashboard_configured(self) -> bool:
        dashboard = self.dashboard_client
        if dashboard is None:
            return False
        if not hasattr(dashboard, "refresh_token"):
            return True
        return bool(getattr(dashboard, "_token", None) or getattr(dashboard, "refresh_token", None))

    async def _try_dashboard_action(
        self, method_name: str, *args: Any, **kwargs: Any
    ) -> bool | None:
        """Try a Dashboard v10 action when metadata is available."""
        dashboard = self.dashboard_client
        device_id = self.dashboard_device_id
        if not self._dashboard_configured():
            return None
        if not device_id:
            raise RuntimeError("Dashboard device id not available")
        method = getattr(dashboard, method_name, None)
        if method is None:
            raise RuntimeError(f"Dashboard action {method_name} is not available")
        try:
            success = bool(await method(self.service_location_id, device_id, *args, **kwargs))
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            raise RuntimeError(
                f"Dashboard action {method_name} failed for device {device_id}"
            ) from err
        if not success:
            raise RuntimeError(f"Dashboard action {method_name} returned no success")
        return True

    async def _require_dashboard_action(self, method_name: str, *args: Any, **kwargs: Any) -> bool:
        """Run a Dashboard action and fail clearly when Dashboard metadata is missing."""
        if await self._try_dashboard_action(method_name, *args, **kwargs):
            return True
        raise RuntimeError("Dashboard API is not configured for this device")

    async def _dashboard_charger_availability(self, available: bool) -> bool | None:
        if not self._dashboard_configured():
            return None
        dashboard = self.dashboard_client
        station_serial = self.charging_station_serial or self.serial
        method = getattr(dashboard, "async_set_charger_availability", None)
        if method is None:
            raise RuntimeError("Dashboard charger availability action is not available")
        try:
            success = bool(await method(station_serial, available))
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            raise RuntimeError(
                f"Dashboard charger availability failed for station {station_serial}"
            ) from err
        if not success:
            raise RuntimeError("Dashboard charger availability returned no success")
        return True

    async def _dashboard_charging_station_restart(self) -> bool | None:
        if not self._dashboard_configured():
            return None
        dashboard = self.dashboard_client
        station_serial = self.charging_station_serial or self.serial
        method = getattr(dashboard, "async_restart_charging_station", None)
        if method is None:
            raise RuntimeError("Dashboard charging station restart action is not available")
        try:
            success = bool(await method(station_serial))
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            raise RuntimeError(
                f"Dashboard charging station restart failed for station {station_serial}"
            ) from err
        if not success:
            raise RuntimeError("Dashboard charging station restart returned no success")
        return True

    async def async_get_smartdevices(self) -> list[dict[str, Any]] | None:
        """Fetch all smartdevices for this service location."""
        if not self._dashboard_configured():
            return None
        dashboard = self.dashboard_client
        if dashboard is None:
            return None
        try:
            data = await dashboard.async_get_smart_devices(self.service_location_id)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            _LOGGER.warning("Dashboard smart devices fetch failed: %s", err)
            return None
        return data if isinstance(data, list) else None

    async def async_get_smartdevice(self, smart_device_id: str) -> dict[str, Any] | None:
        """Fetch a single smartdevice by its numeric/string ID."""
        devices = await self.async_get_smartdevices()
        wanted = str(smart_device_id)
        for device in devices or []:
            candidates = {
                str(value)
                for value in (
                    device.get("id"),
                    device.get("uuid"),
                    device.get("smartDeviceId"),
                    device.get("smartDeviceUuid"),
                )
                if value is not None
            }
            if wanted in candidates:
                return device
        return None

    # ------------------------------------------------------------------
    # COMMANDS (write actions)
    # ------------------------------------------------------------------
    async def set_charging_mode(
        self,
        mode: str,
    ) -> bool:
        """Set charging mode via Dashboard v10.

        This matches the Smappee app mode buttons exactly:
        - Standard button → setchargingmode = {"mode":"STANDARD"}  --> also matches the "resume" action when paused
        - Smart button    → setchargingmode = {"mode":"SMART"}
        - Solar button    → setchargingmode = {"mode":"SOLAR"}

        No current/percentage limit is used here; use set_percentage_limit for speed control.
        """
        mode_up = (mode or "").upper()
        if mode_up not in ("STANDARD", "SMART", "SOLAR"):
            _LOGGER.warning(
                "Unsupported charging mode: %s. Use STANDARD, SMART or SOLAR.",
                mode,
            )
            return False

        await self._require_dashboard_action("async_set_charging_mode", mode_up)
        _LOGGER.debug("Charging mode set successfully (%s via Dashboard v10)", mode_up)
        return True

    async def start_charging(
        self, current: float, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[float, int]:
        """Start charging at (clamped) amperage.

        Returns (current_amps, percentage_limit) so caller can update ConnectorState.
        """
        # _request handles auth
        if max_current < min_current:
            min_current, max_current = max_current, min_current

        min_current_f = float(min_current)
        max_current_f = float(max_current)
        current_f = round(float(current), 1)

        if max_current_f == min_current_f:
            target = round(min_current_f, 1)
            percentage = 100
        else:
            requested = max(min_current_f, min(current_f, max_current_f))
            rng = max_current_f - min_current_f
            percentage = int(max(0, min(100, round(((requested - min_current_f) * 100.0) / rng))))
            target = round(min_current_f + (percentage / 100.0) * rng, 1)

        await self._require_dashboard_action("async_start_charging", percentage)
        _LOGGER.debug(
            "Started charging successfully via Dashboard v10 (target=%s A, pct=%s, range=%s-%s)",
            target,
            percentage,
            min_current,
            max_current,
        )
        return target, percentage

    async def pause_charging(self) -> None:
        """Pause charging via Dashboard v10.

        This matches the Smappee app Pause button:  pausecharging = {}
        """
        await self._require_dashboard_action("async_pause_charging")
        _LOGGER.debug("Paused charging successfully via Dashboard v10")

    async def stop_charging(self) -> None:
        await self._require_dashboard_action("async_stop_charging")
        _LOGGER.debug("Stopped charging successfully via Dashboard v10")

    async def set_brightness(self, brightness: int) -> None:
        await self._require_dashboard_action("async_set_led_brightness", int(brightness))
        _LOGGER.info("LED brightness set successfully to %d%% via Dashboard v10", brightness)

    async def set_min_surpluspct(self, min_surpluspct: int) -> None:
        await self._require_dashboard_action("async_set_min_surpluspct", int(min_surpluspct))
        _LOGGER.info("min.surpluspct set successfully to %d%% via Dashboard v10", min_surpluspct)

    async def set_percentage_limit(
        self, percentage: int, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[float, int]:
        """Set percentage limit; returns (current_amps_float, percentage)."""
        pct_int = max(0, min(100, int(round(percentage))))
        await self._require_dashboard_action("async_set_percentage_limit", pct_int)
        _LOGGER.debug("Set percentage limit successfully to %d%% via Dashboard v10", pct_int)

        if max_current <= min_current:
            cur_float = float(min_current)
        else:
            rng = max_current - min_current
            cur_float = round(min_current + (float(pct_int) / 100.0) * rng, 1)
            cur_float = max(float(min_current), min(float(max_current), cur_float))
        return cur_float, pct_int

    async def set_current(
        self, current: float, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[float, int]:
        """Set charging current in Ampere (1 decimal precision).

        Converts the requested current to the nearest integer percentage of the
        configured min–max range and delegates to set_percentage_limit.
        Returns (current_amps_float, percentage) so the caller can update
        ConnectorState immediately without waiting for the next poll.
        """
        if max_current <= min_current:
            pct = 100
        else:
            val = max(float(min_current), min(round(float(current), 1), float(max_current)))
            rng = max_current - min_current
            pct = int(round((val - min_current) / float(rng) * 100))
            pct = max(0, min(100, pct))
        _LOGGER.debug(
            "set_current: %.1f A → %d%% (range %s–%s A)",
            current,
            pct,
            min_current,
            max_current,
        )
        return await self.set_percentage_limit(
            pct, min_current=min_current, max_current=max_current
        )

    async def set_available(self) -> None:
        if await self._dashboard_charger_availability(True):
            _LOGGER.debug("Set charger available successfully via Dashboard v11")
            return
        raise RuntimeError("Dashboard API is not configured for charger availability")

    async def set_unavailable(self) -> None:
        if await self._dashboard_charger_availability(False):
            _LOGGER.debug("Set charger unavailable successfully via Dashboard v11")
            return
        raise RuntimeError("Dashboard API is not configured for charger availability")

    async def restart_charging_station(self) -> None:
        if await self._dashboard_charging_station_restart():
            _LOGGER.debug("Restarted charging station successfully via Dashboard v11")
            return
        raise RuntimeError("Dashboard API is not configured for charging station restart")

    async def set_offline_charging_config(self, enabled: bool, failsafe_amps: int) -> None:
        dashboard = self.dashboard_client
        if not self._dashboard_configured() or dashboard is None:
            raise RuntimeError("Dashboard API is not configured for offline charging")
        station_serial = self.charging_station_serial or self.serial
        method = getattr(dashboard, "async_set_offline_charging", None)
        if method is None:
            raise RuntimeError("Dashboard offline charging action is not available")
        try:
            success = bool(await method(station_serial, bool(enabled), int(failsafe_amps)))
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            raise RuntimeError(
                f"Dashboard offline charging failed for station {station_serial}"
            ) from err
        if not success:
            raise RuntimeError("Dashboard offline charging returned no success")
        _LOGGER.debug(
            "Set offline charging successfully via Dashboard v11 (enabled=%s, failSafe=%s A)",
            enabled,
            failsafe_amps,
        )

    async def async_get_recent_sessions(self) -> list[dict[str, Any]]:
        """Fetch recent charging sessions for this charging station."""
        station_serial = self.charging_station_serial or self.serial
        if not self._dashboard_configured():
            return []
        dashboard = self.dashboard_client
        if dashboard is None:
            return []
        try:
            return await dashboard.async_get_recent_sessions(station_serial)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            raise RuntimeError(f"Dashboard recent sessions fetch failed: {err}") from err
