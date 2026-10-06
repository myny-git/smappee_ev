"""Share one local freshness timer among a coordinator's listeners."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, override

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator


class FreshnessCoordinator[T](DataUpdateCoordinator[T]):
    """Notify entities about measurement expiry without fetching remote data."""

    _freshness_unsub: CALLBACK_TYPE | None = None

    @callback
    @override
    def async_add_listener(
        self, update_callback: CALLBACK_TYPE, context: Any = None
    ) -> CALLBACK_TYPE:
        remove_listener = super().async_add_listener(update_callback, context)
        if self._freshness_unsub is None and not self._shutdown_requested:
            self._freshness_unsub = async_track_time_interval(
                self.hass, self._async_freshness_tick, timedelta(seconds=30)
            )

        @callback
        def remove() -> None:
            remove_listener()
            if not self._listeners:
                self.cancel_freshness_timer()

        return remove

    @callback
    def _async_freshness_tick(self, _now: datetime) -> None:
        if not self._shutdown_requested and not self.hass.is_stopping:
            self.async_update_listeners()

    @callback
    def cancel_freshness_timer(self) -> None:
        """Stop local freshness notifications on unload or runtime shutdown."""
        if self._freshness_unsub is not None:
            self._freshness_unsub()
            self._freshness_unsub = None

    @override
    async def async_shutdown(self) -> None:
        self.cancel_freshness_timer()
        await super().async_shutdown()
