"""Active charging session tracking helpers for Smappee station coordinators."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timedelta
import logging
from time import time as _now
from typing import TYPE_CHECKING

from aiohttp import ClientError
from homeassistant.core import CALLBACK_TYPE
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from ..helpers import anonymize_uuid
from ..models.state import ConnectorState, IntegrationData, RecentSession

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..api.device_handle import SmappeeDeviceHandle

_LOGGER = logging.getLogger(__name__)

SESSION_START_REFRESH_DELAY = 20
SESSION_ACTIVE_REFRESH_INTERVAL = 5 * 60
SESSION_PAUSED_REFRESH_INTERVAL = 15 * 60
SESSION_MIN_REFRESH_INTERVAL = 2 * 60
SESSION_FINAL_REFRESH_DELAYS = (30, 2 * 60, 5 * 60)

_SESSION_ACTIVE_STATES = {"STARTED", "CHARGING", "CHARGING_STARTED", "RUNNING"}
_SESSION_PAUSED_STATES = {"PAUSED", "SUSPENDED"}
_SESSION_STOPPED_STATES = {"STOPPED", "CHARGING_FINISHED", "FINISHED", "COMPLETED", "IDLE"}


class SessionTrackingMixin:
    """Recent-session refresh and active-session tracking helpers."""

    if TYPE_CHECKING:
        hass: HomeAssistant
        data: IntegrationData
        connector_clients: dict[str, SmappeeDeviceHandle]
        _connector_session_available: dict[str, bool]
        _last_session_api_attempt: float
        _last_session_api_update: float
        _session_refresh_unsub: CALLBACK_TYPE | None
        _session_active_loop_unsub: CALLBACK_TYPE | None
        _session_active_loop_interval: int | None
        _session_final_refresh_unsubs: list[CALLBACK_TYPE]
        _session_refresh_lock: asyncio.Lock
        _session_tracking_started: bool
        _shutting_down: bool

        def async_set_updated_data(self, data: IntegrationData) -> None: ...

        def _start_background_reauth(self) -> None: ...

    def _log_connector_session_transition(
        self, uuid: str, available: bool, err: Exception | None = None
    ) -> None:
        """Log connector session endpoint reachability only when it changes."""
        previous = self._connector_session_available.get(uuid)
        if previous is available:
            if not available and err is not None:
                _LOGGER.debug(
                    "Connector %s session fetch still failing: %s", anonymize_uuid(uuid), err
                )
            return

        self._connector_session_available[uuid] = available
        if available:
            if previous is False:
                _LOGGER.info("Connector %s session fetch recovered", anonymize_uuid(uuid))
            return

        if err is not None:
            _LOGGER.warning("Connector %s session fetch failed: %s", anonymize_uuid(uuid), err)

    def _cancel_session_refresh(self) -> None:
        """Cancel one-shot delayed session refresh."""
        if self._session_refresh_unsub is None:
            return

        unsub = self._session_refresh_unsub
        self._session_refresh_unsub = None
        with suppress(RuntimeError):
            unsub()

    @staticmethod
    def _normalized_session_value(value: object) -> str:
        return str(value or "").strip().upper()

    def _is_session_active(self, conn: ConnectorState) -> bool:
        state = self._normalized_session_value(conn.session_state)
        mode = self._normalized_session_value(conn.raw_charging_mode)
        cause = self._normalized_session_value(conn.session_cause)
        status = self._normalized_session_value(conn.status_current)
        return (
            state in _SESSION_ACTIVE_STATES
            or state in _SESSION_PAUSED_STATES
            or mode == "PAUSED"
            or cause in _SESSION_ACTIVE_STATES
            or cause in _SESSION_PAUSED_STATES
            or status in _SESSION_ACTIVE_STATES
            or status in _SESSION_PAUSED_STATES
            or bool(conn.paused)
        )

    def _is_session_paused(self, conn: ConnectorState) -> bool:
        state = self._normalized_session_value(conn.session_state)
        mode = self._normalized_session_value(conn.raw_charging_mode)
        cause = self._normalized_session_value(conn.session_cause)
        status = self._normalized_session_value(conn.status_current)
        return (
            state in _SESSION_PAUSED_STATES
            or mode == "PAUSED"
            or cause in _SESSION_PAUSED_STATES
            or status in _SESSION_PAUSED_STATES
            or bool(conn.paused)
        )

    def _is_session_finished(self, conn: ConnectorState) -> bool:
        state = self._normalized_session_value(conn.session_state)
        cause = self._normalized_session_value(conn.session_cause)
        status = self._normalized_session_value(conn.status_current)
        return (
            state in _SESSION_STOPPED_STATES
            or cause in _SESSION_STOPPED_STATES
            or status in _SESSION_STOPPED_STATES
        )

    def _active_session_connectors(self) -> list[ConnectorState]:
        data = self.data
        if not data:
            return []
        return [conn for conn in data.connectors.values() if self._is_session_active(conn)]

    def _session_loop_interval(self) -> int:
        active = self._active_session_connectors()
        if active and all(self._is_session_paused(conn) for conn in active):
            return SESSION_PAUSED_REFRESH_INTERVAL
        return SESSION_ACTIVE_REFRESH_INTERVAL

    def _sync_session_tracking_from_current_state(self) -> None:
        if self._active_session_connectors():
            self._ensure_active_session_loop()

    def _handle_session_tracking_transition(
        self, conn: ConnectorState, was_active: bool, connector_uuid: str | None
    ) -> None:
        if not self._session_tracking_started:
            return

        is_active = self._is_session_active(conn)
        if is_active:
            self._cancel_final_session_refreshes()
            self._ensure_active_session_loop()

            if not was_active:
                self._schedule_session_refresh(
                    f"connector {anonymize_uuid(connector_uuid) or '?'} active",
                    delay=SESSION_START_REFRESH_DELAY,
                )
            return

        if was_active or self._is_session_finished(conn):
            if not self._active_session_connectors():
                self._cancel_active_session_loop()
            self._schedule_final_session_refreshes(connector_uuid)

    def _ensure_active_session_loop(self) -> None:
        """Start periodic session refresh while a charging session is active."""
        if self._shutting_down:
            return

        interval = self._session_loop_interval()

        if self._session_active_loop_unsub is not None:
            if self._session_active_loop_interval == interval:
                return
            self._cancel_active_session_loop()

        self._session_active_loop_interval = interval

        async def _refresh_active_session(_now: datetime) -> None:
            if self._shutting_down:
                self._cancel_active_session_loop()
                return

            if not self._active_session_connectors():
                self._cancel_active_session_loop()
                return

            await self._async_refresh_recent_sessions("active session interval")

            if not self._active_session_connectors():
                self._cancel_active_session_loop()
                return

            new_interval = self._session_loop_interval()
            if new_interval != self._session_active_loop_interval:
                self._ensure_active_session_loop()

        self._session_active_loop_unsub = async_track_time_interval(
            self.hass,
            _refresh_active_session,
            timedelta(seconds=interval),
        )

    def _cancel_active_session_loop(self) -> None:
        """Cancel periodic active-session refresh."""
        if self._session_active_loop_unsub is not None:
            unsub = self._session_active_loop_unsub
            self._session_active_loop_unsub = None
            with suppress(RuntimeError):
                unsub()

        self._session_active_loop_interval = None

    def _schedule_final_session_refreshes(self, connector_uuid: str | None) -> None:
        """Schedule final delayed refreshes after a charging session ends."""
        if self._shutting_down:
            return

        self._cancel_final_session_refreshes()

        for delay in SESSION_FINAL_REFRESH_DELAYS:
            unsub_holder: dict[str, CALLBACK_TYPE] = {}

            async def _refresh(
                _now: datetime,
                delay: int = delay,
                unsub_holder: dict[str, CALLBACK_TYPE] = unsub_holder,
            ) -> None:
                unsub = unsub_holder.get("unsub")
                if unsub is not None:
                    with suppress(ValueError):
                        self._session_final_refresh_unsubs.remove(unsub)
                if self._shutting_down:
                    return

                await self._async_refresh_recent_sessions(
                    f"connector {anonymize_uuid(connector_uuid) or '?'} finalizing after {delay}s",
                    force=True,
                )

            unsub = async_call_later(self.hass, delay, _refresh)
            unsub_holder["unsub"] = unsub
            self._session_final_refresh_unsubs.append(unsub)

    def _cancel_final_session_refreshes(self) -> None:
        """Cancel all final delayed session refresh callbacks."""
        for unsub in list(self._session_final_refresh_unsubs):
            with suppress(RuntimeError, ValueError):
                unsub()
        self._session_final_refresh_unsubs.clear()

    def _schedule_session_refresh(self, reason: str, *, delay: int, force: bool = False) -> None:
        """Schedule one delayed recent-session refresh."""
        if self._shutting_down:
            return

        self._cancel_session_refresh()

        async def _refresh(_now: datetime) -> None:
            self._session_refresh_unsub = None

            if self._shutting_down:
                return

            await self._async_refresh_recent_sessions(reason, force=force)

        self._session_refresh_unsub = async_call_later(self.hass, delay, _refresh)

    async def _async_get_recent_sessions(self) -> list[RecentSession]:
        """Fetch recent charging sessions from the connector endpoints."""
        pairs = list(self.connector_clients.items())
        if not pairs:
            _LOGGER.warning("Skipping recent session refresh: no connector clients available")
            return []

        results = await asyncio.gather(
            *(client.async_get_recent_sessions() for _, client in pairs),
            return_exceptions=True,
        )
        sessions: list[RecentSession] = []
        errors: list[tuple[str, Exception]] = []

        for (connector_uuid, _client), result in zip(pairs, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, ConfigEntryAuthFailed):
                    raise result
                if isinstance(result, Exception):
                    errors.append((connector_uuid, result))
                    self._log_connector_session_transition(connector_uuid, False, result)
                    continue
                raise result
            self._log_connector_session_transition(connector_uuid, True)
            sessions.extend(session for session in result if isinstance(session, dict))

        if errors and len(errors) == len(pairs):
            raise errors[0][1]
        return sessions

    async def _async_refresh_recent_sessions(self, reason: str, *, force: bool = False) -> None:
        if self._session_refresh_lock.locked():
            _LOGGER.debug("Skipping recent session refresh; previous refresh still running")
            return

        async with self._session_refresh_lock:
            now = _now()
            if not force and now - self._last_session_api_attempt < SESSION_MIN_REFRESH_INTERVAL:
                _LOGGER.debug("Skipping recent session refresh for %s; throttled", reason)
                return
            self._last_session_api_attempt = now

            try:
                recent_sessions = await self._async_get_recent_sessions()
            except ConfigEntryAuthFailed:
                self._start_background_reauth()
                return
            except (TimeoutError, ClientError, RuntimeError) as err:
                _LOGGER.warning("Recent session refresh failed for %s: %s", reason, err)
                return

            self._last_session_api_update = now
            if self.data:
                self.async_set_updated_data(replace(self.data, recent_sessions=recent_sessions))
            _LOGGER.debug("Recent sessions refreshed for %s", reason)
