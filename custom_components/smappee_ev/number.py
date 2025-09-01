from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api_client import SmappeeApiClient
from .base_entities import SmappeeConnectorEntity, SmappeeStationEntity
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData, RuntimeData
from .helpers import build_connector_label

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV number entities (multi-station)."""
    runtime: RuntimeData = config_entry.runtime_data  # type: ignore[attr-defined]
    sites = runtime.sites

    entities: list[NumberEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeApiClient = bucket["station_client"]
            conns: dict[str, SmappeeApiClient] = bucket.get("connector_clients", {})

            # Per connector
            for cuuid, client in (conns or {}).items():
                entities.append(
                    SmappeeCombinedCurrentSlider(
                        coordinator=coord,
                        api_client=client,
                        sid=sid,
                        station_uuid=st_uuid,
                        connector_uuid=cuuid,
                    )
                )
                entities.append(
                    SmappeeMinSurplusPctNumber(
                        coordinator=coord,
                        api_client=client,
                        sid=sid,
                        station_uuid=st_uuid,
                        connector_uuid=cuuid,
                    )
                )

            # Station-level (LED Brightness)
            entities.append(
                SmappeeBrightnessNumber(
                    coordinator=coord,
                    api_client=st_client,
                    sid=sid,
                    station_uuid=st_uuid,
                )
            )

    async_add_entities(entities, True)


class _BaseNumber(NumberEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

    def _post_init(self, unit: str, min_value: int, max_value: int, step: int) -> None:
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step


class SmappeeCombinedCurrentSlider(SmappeeConnectorEntity, _BaseNumber):
    """Combined slider showing current (A), with percentage in attributes."""

    _attr_device_class = NumberDeviceClass.CURRENT

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ) -> None:
        data: IntegrationData | None = coordinator.data
        st: ConnectorState | None = data.connectors.get(connector_uuid) if data else None

        min_current = st.min_current if st else 6
        max_current = st.max_current if st else 32

        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="number:current",
            name=f"Max charging speed {build_connector_label(api_client, connector_uuid).split(' ', 1)[1]}",
        )
        self.api_client = api_client
        self._uuid = connector_uuid
        self._post_init(UnitOfElectricCurrent.AMPERE, min_current, max_current, 1)

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
        if st.max_current <= st.min_current:
            return int(st.min_current)
        rng = st.max_current - st.min_current
        pct = st.selected_percentage_limit or 0
        cur = st.min_current + (float(pct) / 100.0) * rng
        return max(st.min_current, min(st.max_current, int(round(cur))))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._state()
        if not st:
            return {}
        cur = self.native_value or st.min_current
        if st.max_current <= st.min_current:
            return {
                "percentage": None,
                "percentage_formatted": "—",
                "fixed_range": True,
            }
        rng = st.max_current - st.min_current
        pct = int(round((cur - st.min_current) * 100.0 / rng))
        return {"percentage": pct, "percentage_formatted": f"{pct}%", "fixed_range": False}

    async def async_set_native_value(self, value: float) -> None:
        st = self._state()
        if not st:
            return
        min_c, max_c = st.min_current, st.max_current

        val = int(max(min_c, min(int(value), max_c)))

        _LOGGER.debug(
            "Setting current: requested=%s → clamped=%s (range %s-%s)",
            value,
            val,
            min_c,
            max_c,
        )

        if max_c <= min_c:
            _, pct = await self.api_client.start_charging(
                min_c, min_current=min_c, max_current=max_c
            )
            st.selected_current_limit = min_c
            st.selected_percentage_limit = pct
        else:
            rng = max_c - min_c
            pct = int(round((val - min_c) * 100.0 / rng))
            pct = max(0, min(100, pct))
            cur, pct2 = await self.api_client.set_percentage_limit(
                pct, min_current=min_c, max_current=max_c
            )
            st.selected_current_limit = cur
            st.selected_percentage_limit = pct2

    @callback
    def _handle_coordinator_update(self) -> None:
        st = self._state()
        if st:
            new_min = int(st.min_current)
            new_max = int(st.max_current)

            old_min = self._attr_native_min_value
            old_max = self._attr_native_max_value
            if new_max < new_min:
                new_max = new_min
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

        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:  # RestoreEntity
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if not last or last.state in (None, "unknown", "unavailable"):
            return
        try:
            restored = int(float(last.state))
        except (TypeError, ValueError):
            return
        st = self._state()
        if st:
            # Only set if not yet populated so we don't fight real data
            if st.selected_current_limit is None:
                st.selected_current_limit = restored
                # Derive percentage if range known
                if st.max_current > st.min_current:
                    rng = st.max_current - st.min_current
                    pct = int(round((restored - st.min_current) * 100.0 / rng))
                    st.selected_percentage_limit = max(0, min(100, pct))
                data = self.coordinator.data
                if data:
                    self.coordinator.async_set_updated_data(data)
        self.async_write_ha_state()


class SmappeeBrightnessNumber(SmappeeStationEntity, _BaseNumber):
    """LED brightness setting for Smappee EV (station-level)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="number:led_brightness",
            name="LED Brightness",
        )
        self.api_client = api_client
        self._post_init(PERCENTAGE, 0, 100, 1)

    @property
    def native_value(self) -> int | None:
        data: IntegrationData | None = self.coordinator.data
        return int(data.station.led_brightness) if data and data.station else None

    async def async_set_native_value(self, value: float) -> None:
        val = max(0, min(100, int(value)))
        await self.api_client.set_brightness(val)
        # Optimistic update for immediate UI feedback
        data: IntegrationData | None = self.coordinator.data
        if data and data.station:
            data.station.led_brightness = val
            self.coordinator.async_set_updated_data(data)

    async def async_added_to_hass(self) -> None:  # RestoreEntity
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if not last or last.state in (None, "unknown", "unavailable"):
            return
        try:
            restored = int(float(last.state))
        except (TypeError, ValueError):
            return
        data: IntegrationData | None = self.coordinator.data
        if data and data.station and getattr(data.station, "led_brightness", None) is None:
            data.station.led_brightness = restored
            self.coordinator.async_set_updated_data(data)
        self.async_write_ha_state()


class SmappeeMinSurplusPctNumber(SmappeeConnectorEntity, _BaseNumber):
    """Min Surplus Percentage (connector-level)."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ) -> None:
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="number:min_surpluspct",
            name=f"Min Surplus Percentage {build_connector_label(api_client, connector_uuid).split(' ', 1)[1]}",
        )
        self.api_client = api_client
        self._uuid = connector_uuid
        self._post_init(PERCENTAGE, 0, 100, 1)

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self._uuid) if data else None

    @property
    def native_value(self) -> int | None:
        st = self._state()
        return int(st.min_surpluspct) if st else None

    async def async_set_native_value(self, value: float) -> None:
        await self.api_client.set_min_surpluspct(int(value))
        # Optimistic immediate update
        st = self._state()
        if st:
            st.min_surpluspct = int(value)
            data = self.coordinator.data
            if data:
                self.coordinator.async_set_updated_data(data)

    async def async_added_to_hass(self) -> None:  # RestoreEntity
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if not last or last.state in (None, "unknown", "unavailable"):
            return
        try:
            restored = int(float(last.state))
        except (TypeError, ValueError):
            return
        st = self._state()
        if st and st.min_surpluspct is None:
            st.min_surpluspct = restored
            data = self.coordinator.data
            if data:
                self.coordinator.async_set_updated_data(data)
        self.async_write_ha_state()
