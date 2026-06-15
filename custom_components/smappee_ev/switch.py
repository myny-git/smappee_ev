from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import UpdateFailed

from .base_entities import SmappeeConnectorEntity, SmappeeStationRestEntity
from .coordinator import SmappeeCoordinator
from .data import IntegrationData, SmappeeEvConfigEntry, StationState
from .device_handle import SmappeeDeviceHandle

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


# station_serial no longer needed; provided by base entity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV switches (multi-station)."""
    runtime = config_entry.runtime_data
    sites = runtime.sites

    entities: list[SwitchEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeDeviceHandle = bucket["station_client"]
            conns: dict[str, SmappeeDeviceHandle] = bucket.get("connector_clients", {})

            if conns:
                # Station-level switch
                entities.append(
                    SmappeeAvailabilitySwitch(
                        coordinator=coord,
                        api_client=st_client,
                        sid=sid,
                        station_uuid=st_uuid,
                    )
                )
                entities.append(
                    SmappeeOfflineChargingSwitch(
                        coordinator=coord,
                        api_client=st_client,
                        sid=sid,
                        station_uuid=st_uuid,
                    )
                )

                # Connector-level switches
                for cuuid, client in (conns or {}).items():
                    entities.append(
                        SmappeeChargingSwitch(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                        )
                    )

    async_add_entities(entities, False)


# ====================================================================================
# Charging ON/OFF (acts like a start/pause toggle on the connector)
# ====================================================================================


class SmappeeChargingSwitch(SmappeeConnectorEntity, SwitchEntity, RestoreEntity):
    """Switch to control start/pause charging on a specific connector."""

    _attr_has_entity_name = True
    _attr_translation_key = "evcc_charging"
    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            api_client,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="switch:charging",
        )
        self.api_client = api_client
        self._is_on = False

    # ---------- HA hooks ----------

    @property
    def is_on(self) -> bool:
        """Show last EVCC intent only (not physical session state)."""
        return self._is_on

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Restore last EVCC intent across restarts
        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == "on"

    # ---------- Actions ----------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set charging mode to STANDARD via the configured API path."""
        try:
            _LOGGER.debug(
                "Charging switch ON â†’ STANDARD (sid=%s, uuid=%s)",
                self._sid,
                self.connector_uuid,
            )
            await self.api_client.set_charging_mode("STANDARD")
            st = self._conn_state
            if st:
                st.selected_mode = "STANDARD"
                data = self.coordinator.data
                if data:
                    self.coordinator.async_set_updated_data(data)
            self.coordinator.async_schedule_dashboard_refresh()
            self._is_on = True
            self.async_write_ha_state()
        except asyncio.CancelledError:
            raise
        except (ClientError, TimeoutError, HomeAssistantError, UpdateFailed, RuntimeError) as err:
            _LOGGER.warning("Failed to start charging on %s: %s", self.connector_uuid, err)
            self.async_write_ha_state()
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause charging via the configured API path."""
        try:
            _LOGGER.debug(
                "Charging switch OFF â†’ pause (sid=%s, uuid=%s)",
                self._sid,
                self.connector_uuid,
            )
            await self.api_client.pause_charging()
            self.coordinator.async_schedule_dashboard_refresh()
            self._is_on = False
            self.async_write_ha_state()
        except asyncio.CancelledError:
            raise
        except (ClientError, TimeoutError, HomeAssistantError, UpdateFailed, RuntimeError) as err:
            _LOGGER.warning("Failed to pause charging on %s: %s", self.connector_uuid, err)
            self.async_write_ha_state()
            raise


# ====================================================================================
# Availability ON/OFF (expose/take connector available for charging)
# ====================================================================================


class SmappeeAvailabilitySwitch(SmappeeStationRestEntity, SwitchEntity):
    """Switch to toggle station availability (acchargingstation action)."""

    _attr_has_entity_name = True
    _attr_translation_key = "station_available"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,  # <-- station client
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeStationRestEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="switch:station_available",
        )
        self.api_client = api_client

    def _station_state(self) -> StationState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.station if data else None

    @property
    def is_on(self) -> bool:
        st = self._station_state()
        return bool(getattr(st, "available", True)) if st else True

    @property
    def icon(self) -> str:
        return "mdi:ev-station" if self.is_on else "mdi:ev-station-disabled"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_available(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_available(False)

    async def _set_available(self, value: bool) -> None:
        data: IntegrationData | None = self.coordinator.data
        st = self._station_state()
        prev = getattr(st, "available", None) if st else None

        if data and st is not None and prev != value:
            st.available = value
            self.coordinator.async_set_updated_data(data)

        try:
            if value:
                await self.api_client.set_available()
            else:
                await self.api_client.set_unavailable()
            self.coordinator.async_schedule_dashboard_refresh()
        except Exception as err:
            _LOGGER.warning("Set station availability failed (sid=%s): %s", self._sid, err)
            # revert optimistic update
            if data and st is not None and prev is not None:
                st.available = prev
                self.coordinator.async_set_updated_data(data)
            raise


class SmappeeOfflineChargingSwitch(SmappeeStationRestEntity, SwitchEntity):
    """Switch to toggle station-level offline charging/failsafe mode."""

    _attr_has_entity_name = True
    _attr_translation_key = "offline_charging"
    _attr_entity_category = EntityCategory.CONFIG

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
            unique_suffix="switch:offline_charging",
        )
        self.api_client = api_client

    def _station_state(self) -> StationState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.station if data else None

    @property
    def is_on(self) -> bool:
        st = self._station_state()
        return bool(getattr(st, "offline_charging_enabled", False)) if st else False

    @property
    def icon(self) -> str:
        return "mdi:cloud-outline" if self.is_on else "mdi:cloud-off-outline"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_offline_charging(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_offline_charging(False)

    async def _set_offline_charging(self, enabled: bool) -> None:
        data: IntegrationData | None = self.coordinator.data
        st = self._station_state()
        if st is None:
            return

        prev_enabled = st.offline_charging_enabled
        failsafe = st.offline_failsafe_current_a
        if failsafe is None:
            failsafe = 3

        if data and prev_enabled != enabled:
            st.offline_charging_enabled = enabled
            self.coordinator.async_set_updated_data(data)

        try:
            await self.api_client.set_offline_charging_config(enabled, failsafe)
            self.coordinator.async_schedule_dashboard_refresh()
        except Exception as err:
            _LOGGER.warning("Set offline charging failed (sid=%s): %s", self._sid, err)
            if data and prev_enabled is not None:
                st.offline_charging_enabled = prev_enabled
                self.coordinator.async_set_updated_data(data)
            raise
