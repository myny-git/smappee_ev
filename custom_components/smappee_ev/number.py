from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
    """Set up Smappee EV number entities from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: dict[str, SmappeeApiClient] = data["connector_clients"]
    station_client: SmappeeApiClient = data["station_client"]

    entities: list[NumberEntity] = []

    # Connector numbers
    for uuid, client in connector_clients.items():
        entities.append(SmappeeCombinedCurrentSlider(coordinator, client, uuid))
        entities.append(SmappeeMinSurplusPctNumber(coordinator, client, uuid))

    # Station number
    entities.append(SmappeeBrightnessNumber(coordinator, station_client))

    async_add_entities(entities, update_before_add=True)


class _Base(CoordinatorEntity[SmappeeCoordinator], NumberEntity):
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

    @property
    def device_info(self):
        """Return device info for the wallbox."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }


class SmappeeCombinedCurrentSlider(_Base):
    """Combined slider showing current (A), with percentage in attributes."""

    def __init__(
        self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, connector_uuid: str
    ) -> None:
        self._uuid = connector_uuid
        data: IntegrationData | None = coordinator.data
        st: ConnectorState | None = data.connectors.get(connector_uuid) if data else None

        min_current = st.min_current if st else 6
        max_current = st.max_current if st else 32

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

        # self.coordinator.async_add_listener(self.async_write_ha_state)

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._uuid) if data else None

    @property
    def native_value(self) -> int | None:
        st = self._state()
        if not st:
            return None
        if st.selected_current_limit is not None:
            return int(st.selected_current_limit)
        rng = max(st.max_current - st.min_current, 1)
        pct = st.selected_percentage_limit or 0
        return int(round((pct / 100) * rng + st.min_current))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._state()
        if not st:
            return {}
        rng = max(st.max_current - st.min_current, 1)
        cur = self.native_value or st.min_current
        pct = round((cur - st.min_current) / rng * 100)
        return {"percentage": pct, "percentage_formatted": f"{pct}%"}

    async def async_set_native_value(self, value: float) -> None:
        st = self._state()
        if not st:
            return
        min_c, max_c = st.min_current, st.max_current
        val = int(max(min_c, min(int(value), max_c)))
        rng = max(max_c - min_c, 1)
        pct = round((val - min_c) / rng * 100)

        # Command via API, then refresh coordinator

        _LOGGER.debug(
            "Setting current: requested=%s → clamped=%s → pct=%s (range %s-%s)",
            value,
            val,
            pct,
            min_c,
            max_c,
        )

        await self.api_client.set_percentage_limit(pct)
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        st = self._state()
        if st:
            new_min = int(st.min_current)
            new_max = int(st.max_current)

            if new_max >= new_min:
                old_min = self._attr_native_min_value
                old_max = self._attr_native_max_value

                if old_min != new_min:
                    self._attr_native_min_value = new_min
                if old_max != new_max:
                    self._attr_native_max_value = new_max

                if old_min != new_min or old_max != new_max:
                    _LOGGER.debug(
                        "Updated slider range to %s–%s A (was %s–%s)",
                        new_min,
                        new_max,
                        old_min,
                        old_max,
                    )
        # laat CoordinatorEntity de state schrijven
        super()._handle_coordinator_update()


class SmappeeBrightnessNumber(_Base):
    """LED brightness setting for Smappee EV (station-level)."""

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
    def native_value(self) -> int | None:
        data: IntegrationData | None = self.coordinator.data
        return int(data.station.led_brightness) if data and data.station else None

    async def async_set_native_value(self, value: float) -> None:
        await self.api_client.set_brightness(int(value))
        await self.coordinator.async_request_refresh()


class SmappeeMinSurplusPctNumber(_Base):
    """Min Surplus Percentage (connector-level)."""

    def __init__(
        self, coordinator: SmappeeCoordinator, api_client: SmappeeApiClient, connector_uuid: str
    ) -> None:
        self._uuid = connector_uuid
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

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._uuid) if data else None

    @property
    def native_value(self) -> int | None:
        st = self._state()
        return int(st.min_surpluspct) if st else None

    async def async_set_native_value(self, value: float) -> None:
        await self.api_client.set_min_surpluspct(int(value))
        await self.coordinator.async_request_refresh()
