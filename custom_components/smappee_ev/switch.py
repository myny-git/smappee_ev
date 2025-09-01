from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api_client import SmappeeApiClient
from .base_entities import SmappeeConnectorEntity, SmappeeStationEntity
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData, RuntimeData, StationState
from .helpers import build_connector_label

_LOGGER = logging.getLogger(__name__)


# station_serial no longer needed; provided by base entity


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee EV switches (multi-station)."""
    runtime: RuntimeData = config_entry.runtime_data  # type: ignore[attr-defined]
    sites = runtime.sites

    entities: list[SwitchEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeApiClient = bucket["station_client"]
            conns: dict[str, SmappeeApiClient] = bucket.get("connector_clients", {})

            # Station-level switch
            entities.append(
                SmappeeAvailabilitySwitch(
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

    async_add_entities(entities, True)


# ====================================================================================
# Charging ON/OFF (acts like a start/pause toggle on the connector)
# ====================================================================================


class SmappeeChargingSwitch(SmappeeConnectorEntity, SwitchEntity, RestoreEntity):
    """Switch to control start/pause charging on a specific connector."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ) -> None:
        num_lbl = build_connector_label(api_client, connector_uuid).split(" ", 1)[1]
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="switch:charging",
            name=f"Connector {num_lbl} EVCC charging",
        )
        self.api_client = api_client
        self._is_on = False

    # ---------- Helpers ----------

    def _conn_state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        if not data:
            return None
        return (data.connectors or {}).get(self._uuid)

    # ---------- HA hooks ----------

    @property
    def device_info(self):
        return super().device_info

    @property
    def is_on(self) -> bool:
        """Show last EVCC intent only (not physical session state)."""
        return self._is_on

    async def async_added_to_hass(self) -> None:
        # Restore last EVCC intent across restarts
        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == "on"

    # ---------- Actions ----------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start charging using selected or minimum current; set NORMAL mode first if needed."""
        st = self._conn_state()
        # Prefer selected current; else min_current; guard to >= 1
        if st:
            current = (
                st.selected_current_limit
                if st.selected_current_limit is not None
                else st.min_current
            )
            mode = getattr(st, "selected_mode", None) or getattr(st, "ui_mode_base", None)
        else:
            current = 6
            mode = "NORMAL"

        current = int(max(int(current or 6), 6))

        try:
            if mode != "NORMAL":
                _LOGGER.debug(
                    "Charging switch: switching mode to NORMAL before starting (sid=%s, uuid=%s)",
                    self._sid,
                    self._uuid,
                )
                await self.api_client.set_charging_mode("NORMAL", current)

            _LOGGER.debug(
                "Charging switch ON → start_charging %s A (sid=%s, uuid=%s)",
                current,
                self._sid,
                self._uuid,
            )
            cur, pct = await self.api_client.start_charging(
                current,
                min_current=st.min_current if st else 6,
                max_current=st.max_current if st else 32,
            )
            if st:
                st.selected_current_limit = cur
                st.selected_percentage_limit = pct
                data = self.coordinator.data
                if data:
                    self.coordinator.async_set_updated_data(data)
            self._is_on = True
            self.async_write_ha_state()
        except (
            ClientError,
            asyncio.CancelledError,
            TimeoutError,
            HomeAssistantError,
            UpdateFailed,
        ) as err:
            _LOGGER.warning("Failed to start charging on %s: %s", self._uuid, err)
            self.async_write_ha_state()
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause charging."""
        try:
            _LOGGER.debug(
                "Charging switch OFF → pause_charging (sid=%s, uuid=%s)", self._sid, self._uuid
            )
            await self.api_client.pause_charging()
            self._is_on = False
            self.async_write_ha_state()
        except (
            ClientError,
            asyncio.CancelledError,
            TimeoutError,
            HomeAssistantError,
            UpdateFailed,
        ) as err:
            _LOGGER.warning("Failed to pause charging on %s: %s", self._uuid, err)
            self.async_write_ha_state()
            raise


# ====================================================================================
# Availability ON/OFF (expose/take connector available for charging)
# ====================================================================================


class SmappeeAvailabilitySwitch(SmappeeStationEntity, SwitchEntity):
    """Switch to toggle station availability (acchargingstation action)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,  # <-- station client
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="switch:station_available",
            name="Station available",
        )
        self.api_client = api_client

    @property
    def device_info(self):
        return super().device_info

    def _station_state(self) -> StationState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.station if data else None

    @property
    def is_on(self) -> bool:
        st = self._station_state()
        return bool(getattr(st, "available", True)) if st else True

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
        except Exception as err:
            _LOGGER.warning("Set station availability failed (sid=%s): %s", self._sid, err)
            # revert optimistic update
            if data and st is not None and prev is not None:
                st.available = prev
                self.coordinator.async_set_updated_data(data)
            raise
