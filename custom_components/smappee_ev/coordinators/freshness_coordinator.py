"""Share one local freshness timer among a coordinator's listeners."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, override

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


class FreshnessCoordinator[T](DataUpdateCoordinator[T]):
    """Notify entities about measurement expiry without fetching remote data."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._freshness_unsub: CALLBACK_TYPE | None = None
        self._freshness_listener_count = 0
        self._freshness_stopped = False

    @callback
    @override
    def async_add_listener(
        self, update_callback: CALLBACK_TYPE, context: Any = None
    ) -> CALLBACK_TYPE:
        remove_listener = super().async_add_listener(update_callback, context)
        self._freshness_listener_count += 1
        if self._freshness_unsub is None and not self._freshness_stopped:
            self._freshness_unsub = async_track_time_interval(
                self.hass, self._async_freshness_tick, timedelta(seconds=30)
            )

        removed = False

        @callback
        def remove() -> None:
            nonlocal removed
            if removed:
                return
            remove_listener()
            removed = True
            self._freshness_listener_count -= 1
            if self._freshness_listener_count == 0:
                self.cancel_freshness_timer()

        return remove

    @callback
    def _async_freshness_tick(self, _now: datetime) -> None:
        if not self._freshness_stopped and not self.hass.is_stopping:
            self.async_update_listeners()

    @callback
    def cancel_freshness_timer(self) -> None:
        """Stop local freshness notifications on unload or runtime shutdown."""
        if self._freshness_unsub is not None:
            self._freshness_unsub()
            self._freshness_unsub = None

    @override
    async def async_shutdown(self) -> None:
        self._freshness_stopped = True
        self.cancel_freshness_timer()
        await super().async_shutdown()
