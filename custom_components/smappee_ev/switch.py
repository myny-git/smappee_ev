from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData

_LOGGER = logging.getLogger(__name__)


def _station_serial(coord: SmappeeCoordinator) -> str:
    return getattr(coord.station_client, "serial_id", "unknown")


def _station_name(coord: SmappeeCoordinator, sid: int) -> str:
    st = coord.data.station if getattr(coord, "data", None) else None
    return getattr(st, "name", None) or f"Smappee EV {_station_serial(coord)}"


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee EV switches for all discovered service locations."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[int, SmappeeCoordinator] = store["coordinators"]
    connector_clients_by_sid: dict[int, dict[str, SmappeeApiClient]] = store["connector_clients"]

    station_clients: dict[int, SmappeeApiClient] = store["station_clients"]

    entities: list[SwitchEntity] = []
    for sid, coord in coordinators.items():
        station_client = station_clients.get(sid)
        if station_client:
            entities.append(
                SmappeeAvailabilitySwitch(
                    coordinator=coord,
                    api_client=station_client,
                    sid=sid,
                )
            )

        for uuid, client in (connector_clients_by_sid.get(sid) or {}).items():
            entities.append(
                SmappeeChargingSwitch(
                    coordinator=coord,
                    api_client=client,
                    sid=sid,
                    uuid=uuid,
                )
            )

    async_add_entities(entities)


# ====================================================================================
# Charging ON/OFF (acts like a start/pause toggle on the connector)
# ====================================================================================


class SmappeeChargingSwitch(CoordinatorEntity[SmappeeCoordinator], SwitchEntity, RestoreEntity):
    """Switch to control start/pause charging on a specific connector."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        uuid: str,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._sid = sid
        self._uuid = uuid
        serial = _station_serial(coordinator)
        num = getattr(api_client, "connector_number", None)
        num_lbl = f"{num}" if num is not None else uuid[-4:]
        self._attr_name = f"Connector {num_lbl} EVCC charging"
        self._attr_unique_id = f"{sid}:{serial}:{uuid}:charging_switch"
        self._is_on = False  # EVCC intent latch only (authoritative)

    # ---------- Helpers ----------

    def _conn_state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        if not data:
            return None
        return (data.connectors or {}).get(self._uuid)

    def _device_serial(self) -> str:
        return _station_serial(self.coordinator)

    # ---------- HA hooks ----------

    @property
    def device_info(self) -> DeviceInfo:
        serial = self._device_serial()
        return {
            "identifiers": {(DOMAIN, f"{self._sid}:{serial}")},
            "name": _station_name(self.coordinator, self._sid),
            "manufacturer": "Smappee",
        }

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
            current = getattr(self.api_client, "min_current", 6)
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

            _LOGGER.info(
                "Charging switch ON → start_charging %s A (sid=%s, uuid=%s)",
                current,
                self._sid,
                self._uuid,
            )
            await self.api_client.start_charging(current)
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
            _LOGGER.info(
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


class SmappeeAvailabilitySwitch(CoordinatorEntity[SmappeeCoordinator], SwitchEntity):
    """Switch to toggle station availability (acchargingstation action)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,  # <-- station client
        sid: int,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._sid = sid
        serial = _station_serial(coordinator)
        self._attr_name = "Station available"
        self._attr_unique_id = f"{sid}:{serial}:station_available"

    @property
    def device_info(self) -> DeviceInfo:
        serial = _station_serial(self.coordinator)
        return {
            "identifiers": {(DOMAIN, f"{self._sid}:{serial}")},
            "name": _station_name(self.coordinator, self._sid),
            "manufacturer": "Smappee",
        }

    def _station_state(self):
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
