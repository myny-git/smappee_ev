from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

from aiohttp import ClientError
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV switch entity from config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: dict[str, SmappeeApiClient] = data["connector_clients"]  # keyed by UUID

    entities: list[SwitchEntity] = []
    for client in connector_clients.values():
        entities.append(SmappeeChargingSwitch(coordinator=coordinator, api_client=client))
        entities.append(SmappeeAvailabilitySwitch(coordinator=coordinator, api_client=client))

    async_add_entities(entities)


class SmappeeChargingSwitch(CoordinatorEntity[SmappeeCoordinator], SwitchEntity):
    """Switch entity to control start/pause charging."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._attr_name = f"EVCC Charging Control {api_client.connector_number}"
        self._attr_unique_id = (
            f"{api_client.serial_id}_evcc_charging_switch_{api_client.connector_number}"
        )
        self._is_on = False  # local flag only

    @property
    def device_info(self):
        """Return device information for correct device grouping."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    @property
    def is_on(self) -> bool:
        """Local ON/OFF flag, not derived from session_state."""
        return self._is_on

    async def async_added_to_hass(self) -> None:
        """Initialize local state on add."""
        self._is_on = False
        _LOGGER.debug("Initialized %s with is_on = False", self._attr_unique_id)

    async def _async_refresh(self) -> None:
        """Trigger one coordinator refresh so UI updates immediately."""
        try:
            await self.coordinator.async_request_refresh()
        except (
            TimeoutError,
            ClientError,
            asyncio.CancelledError,
            UpdateFailed,
            HomeAssistantError,
        ) as err:
            _LOGGER.debug("Coordinator refresh failed after switch action: %s", err)

    async def async_turn_on(self, **kwargs) -> None:
        """Start charging at current selected limit (fallback to min_current)."""
        current = None
        mode = None

        data = self.coordinator.data

        for st in data.connectors.values():
            if st.connector_number == self.api_client.connector_number:
                current = (
                    st.selected_current_limit
                    if st.selected_current_limit is not None
                    else st.min_current
                )
                mode = st.selected_mode
                break

        if current is None:
            current = max(getattr(self.api_client, "min_current", 6), 1)

        if mode != "NORMAL":
            _LOGGER.info("EVCC Switch: changing mode to NORMAL before starting charging")
            await self.api_client.set_charging_mode("NORMAL", current)

        _LOGGER.info(
            "Switch ON: starting charging at %sA on connector %s",
            current,
            self.api_client.connector_number,
        )
        await self.api_client.start_charging(int(current))
        self._is_on = True
        self.async_write_ha_state()
        await self._async_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Pause charging."""
        _LOGGER.info(
            "Switch OFF: pausing charging on connector %s", self.api_client.connector_number
        )
        await self.api_client.pause_charging()
        self._is_on = False
        self.async_write_ha_state()
        await self._async_refresh()


class SmappeeAvailabilitySwitch(CoordinatorEntity[SmappeeCoordinator], SwitchEntity):
    """Switch to toggle connector availability."""

    _attr_has_entity_name = True

    def __init__(self, *, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._attr_name = f"Connector {api_client.connector_number} available"
        self._attr_unique_id = (
            f"{api_client.serial_id}_connector{api_client.connector_number}_available"
        )

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        if not data:
            return None

        for st in data.connectors.values():
            if st.connector_number == self.api_client.connector_number:
                return st
        return None

    @property
    def is_on(self) -> bool:
        st = self._state()
        if st is None:
            return True
        return st.available if st.available is not None else True

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_available(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_available(False)

    async def _set_available(self, value: bool) -> None:
        data: IntegrationData | None = self.coordinator.data
        prev = None
        st = self._state()
        if data and st is not None:
            prev = st.available
            if prev != value:
                st.available = value
                self.coordinator.async_set_updated_data(data)

        try:
            if value:
                await self.api_client.set_available()
            else:
                await self.api_client.set_unavailable()
        except (
            ClientError,
            asyncio.CancelledError,
            TimeoutError,
            HomeAssistantError,
            UpdateFailed,
        ) as err:
            _LOGGER.warning(
                "Set availability failed on connector %s: %s",
                self.api_client.connector_number,
                err,
            )
            # Revert local state
            if data and st is not None and prev is not None:
                st.available = prev
                self.coordinator.async_set_updated_data(data)
            raise
        else:
            # best effort UI nudge
            with suppress(
                TimeoutError, ClientError, asyncio.CancelledError, UpdateFailed, HomeAssistantError
            ):
                await self.coordinator.async_request_refresh()
