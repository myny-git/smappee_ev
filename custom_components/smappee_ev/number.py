from __future__ import annotations
import logging

from typing import Any, Dict

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV number entities from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: Dict[str, SmappeeApiClient] = data["connector_clients"] 
    station_client: SmappeeApiClient = data["station_client"]

    entities: list[NumberEntity] = []

    # Connector numbers
    for uuid, client in connector_clients.items():
        entities.append(SmappeeCombinedCurrentSlider(coordinator, client, uuid))
        entities.append(SmappeeMinSurplusPctNumber(coordinator, client, uuid))

    # Station number
    entities.append(SmappeeBrightnessNumber(coordinator, station_client))

    async_add_entities(entities)


class _SmappeeNumberBase(CoordinatorEntity[SmappeeCoordinator], NumberEntity):
    """Base class for Smappee EV numbers."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        *,
        name: str,
        unique_id: str,
        unit: str,
        min_value: int,
        max_value: int,
        step: int = 1,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        #self._current_value = int(initial_value) if initial_value is not None else min_value

    @property
    def device_info(self):
        """Return device info for the wallbox."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

class SmappeeCombinedCurrentSlider(_SmappeeNumberBase):
    """Combined slider showing current and percentage."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        connector_uuid: str,
    ) -> None:
        
        self._connector_uuid = connector_uuid
        data: IntegrationData | None = coordinator.data
        state: ConnectorState | None = (data.connectors.get(connector_uuid) if data else None)
        min_current = state.min_current if state else getattr(api_client, "min_current", 6)
        max_current = state.max_current if state else getattr(api_client, "max_current", 32)



        super().__init__(
            coordinator,
            api_client,
            name=f"Max charging speed {api_client.connector_number}",
            unique_id=f"{api_client.serial_id}_connector{api_client.connector_number}_combined_current",
            unit="A",
            min_value=min_current,
            max_value=max_current,
            step=1,
        )



        #api_client.register_value_callback("current_limit", self._handle_external_update)
        #api_client.register_value_callback("percentage_limit", self._handle_percentage_update)

    @property
    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._connector_uuid) if data else None

    @property
    def native_value(self) -> int:
        st = self._state
        if not st:
            # Fallback to client if there is no snapshot
            cur = getattr(self.api_client, "selected_current_limit", None)
            return int(cur if cur is not None else getattr(self.api_client, "min_current", 6))
        # Calculate current based on percentage if current is not explicit
        if st.selected_current_limit is not None:
            return int(st.selected_current_limit)
        # reconstruct current via percentage
        rng = max(st.max_current - st.min_current, 1)
        pct = st.selected_percentage_limit if st.selected_percentage_limit is not None else 0
        cur = round((pct / 100) * rng + st.min_current)
        return int(cur)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._state
        if not st:
            return {}
        rng = max(st.max_current - st.min_current, 1)
        cur = self.native_value
        pct = round((cur - st.min_current) / rng * 100)
        return {"percentage": pct, "percentage_formatted": f"{pct}%"}

    async def async_set_native_value(self, value: int) -> None:
        st = self._state
        min_c = st.min_current if st else getattr(self.api_client, "min_current", 6)
        max_c = st.max_current if st else getattr(self.api_client, "max_current", 32)

        val = int(max(min_c, min(int(value), max_c)))
        rng = max(max_c - min_c, 1)
        pct = round((val - min_c) / rng * 100)

        await self.api_client.set_percentage_limit(pct)
        # Direct refresh
        await self.coordinator.async_request_refresh()

    async def async_update(self) -> None:
        st = self._state
        if st:
            self._attr_native_min_value = st.min_current
            self._attr_native_max_value = st.max_current


class SmappeeBrightnessNumber(_SmappeeNumberBase):
    """LED brightness setting for Smappee EV."""

    def __init__(self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient) -> None:
        super().__init__(
            coordinator,
            api_client,
            name="LED Brightness",
            unique_id=f"{api_client.serial_id}_led_brightness",
            unit="%",
            min_value=0,
            max_value=100,
            step=1,
        )

    @property
    def native_value(self) -> int:
        data: IntegrationData | None = self.coordinator.data
        if data and data.station:
            return int(data.station.led_brightness)
        return int(getattr(self.api_client, "led_brightness", 70))

    async def async_set_native_value(self, value: float) -> None:
        await self.api_client.set_brightness(int(value))
        await self.coordinator.async_request_refresh()

class SmappeeMinSurplusPctNumber(_SmappeeNumberBase):
    """Min Surplus Percentage (slider) for Smappee EV."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        connector_uuid: str,
    ) -> None:
        self._connector_uuid = connector_uuid

        data: IntegrationData | None = coordinator.data
        st: ConnectorState | None = (data.connectors.get(connector_uuid) if data else None)
        #initial = st.min_surpluspct if st else getattr(api_client, "min_surpluspct", 100)

        super().__init__(
            coordinator,
            api_client,
            name=f"Min Surplus Percentage {api_client.connector_number}",
            unique_id=f"{api_client.serial_id}_connector{api_client.connector_number}_min_surpluspct",
            unit="%",
            min_value=0,
            max_value=100,
            step=1,
        )
        

    @property
    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._connector_uuid) if data else None

    @property
    def native_value(self) -> int:
        st = self._state
        if st:
            return int(st.min_surpluspct)
        return int(getattr(self.api_client, "min_surpluspct", 100))

    async def async_set_native_value(self, value: float) -> None:
        await self.api_client.set_min_surpluspct(int(value))
        await self.coordinator.async_request_refresh()