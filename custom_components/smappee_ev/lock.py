from __future__ import annotations

import asyncio
import logging
from typing import Any, override

from aiohttp import ClientError
from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api.device_handle import SmappeeDeviceHandle
from .api.errors import SmappeeError
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .entity import SmappeeStationRestEntity
from .models.runtime_data import SmappeeEvConfigEntry
from .models.state import IntegrationData, StationState

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV locks (multi-station)."""
    runtime = config_entry.runtime_data

    entities: list[LockEntity] = []
    for sid, site in (runtime.sites or {}).items():
        sid_int = int(sid)
        for st_uuid, bucket in site.stations.items():
            coord: SmappeeCoordinator | None = bucket.station_coordinator
            st_client: SmappeeDeviceHandle | None = bucket.station_client

            if coord is not None and st_client is not None:
                entities.append(
                    SmappeeCableLock(
                        coordinator=coord,
                        api_client=st_client,
                        sid=sid_int,
                        station_uuid=st_uuid,
                    )
                )

    async_add_entities(entities, False)


class SmappeeCableLock(SmappeeStationRestEntity, LockEntity):
    """Lock to control the charging station's cable lock (socket version)."""

    _attr_has_entity_name = True
    _attr_translation_key = "cable_lock"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeStationRestEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="lock:cable_lock",
        )
        self.api_client = api_client

    def _station_state(self) -> StationState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.station if data else None

    @property
    @override
    def available(self) -> bool:
        """Require a reported state, without claiming hardware support.

        Fixed-cable stations can also report ``cableLocked``. Users must enable
        this optional entity only if their station supports cable locking.
        """
        if not super().available:
            return False
        st = self._station_state()
        return st is not None and st.cable_locked is not None

    @property
    @override
    def is_locked(self) -> bool | None:
        st = self._station_state()
        return getattr(st, "cable_locked", None) if st else None

    @override
    async def async_lock(self, **kwargs: Any) -> None:
        await self._set_locked(True)

    @override
    async def async_unlock(self, **kwargs: Any) -> None:
        await self._set_locked(False)

    async def _set_locked(self, value: bool) -> None:
        data: IntegrationData | None = self.coordinator.data
        st = self._station_state()
        if data is None or st is None or st.cable_locked is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="station_unavailable",
            )

        try:
            if value:
                await self.api_client.set_cable_locked()
            else:
                await self.api_client.set_cable_unlocked()
        except asyncio.CancelledError:
            # The server may have accepted the write before cancellation.
            # Reconcile without claiming success or swallowing cancellation;
            # the coordinator suppresses refreshes during shutdown/MQTT-only.
            self.coordinator.async_schedule_dashboard_refresh()
            raise
        except ConfigEntryAuthFailed:
            raise
        except (
            SmappeeError,
            ClientError,
            TimeoutError,
            HomeAssistantError,
            UpdateFailed,
            RuntimeError,
            ValueError,
        ) as err:
            # A failed response does not prove the server rejected the write.
            # This also covers transport failures wrapped in RuntimeError by
            # the device handle. Keep local state until Dashboard confirms it.
            self.coordinator.async_schedule_dashboard_refresh()
            _LOGGER.warning("Set cable lock failed (sid=%s): %s", self._sid, err)
            if isinstance(err, HomeAssistantError):
                raise
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="station_service_failed",
                translation_placeholders={
                    "method_name": "set_cable_locked" if value else "set_cable_unlocked",
                    "error": str(err),
                },
            ) from err

        # Polling may replace the snapshot while the request is in flight.
        # Publish only the successful lock change on the current snapshot.
        current: IntegrationData | None = self.coordinator.data
        if current is not None and current.station.cable_locked != value:
            current.station.cable_locked = value
            self.coordinator.async_set_updated_data(current)
        self.coordinator.async_schedule_dashboard_refresh()
