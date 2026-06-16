from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entities import SmappeeConnectorEntity, SmappeeSiteEntity, SmappeeStationEntity
from .const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT, DOMAIN
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData, SmappeeEvConfigEntry, StationState
from .device_handle import SmappeeDeviceHandle

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


def _active_or_true(value: bool | None) -> bool:
    """Default to active for value-only Dashboard writes when state is unknown."""
    return bool(value) if value is not None else True


def _dashboard_coord_for_site(site: dict[str, Any]) -> SmappeeCoordinator | None:
    """Return a station coordinator that can serve site-scoped Dashboard settings."""
    for bucket in ((site or {}).get("stations") or {}).values():
        if not isinstance(bucket, dict):
            continue
        coord: SmappeeCoordinator | None = bucket.get("coordinator")
        if coord is not None and getattr(coord, "dashboard_client", None) is not None:
            return coord
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV number entities (multi-station)."""
    runtime = config_entry.runtime_data
    sites = runtime.sites

    entities: list[NumberEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        dashboard_coord = _dashboard_coord_for_site(site or {})
        if dashboard_coord is not None:
            entities.append(
                SmappeeCapacityMaximumPowerNumber(
                    coordinator=dashboard_coord,
                    sid=sid,
                )
            )
            entities.append(
                SmappeeOverloadMaximumLoadNumber(
                    coordinator=dashboard_coord,
                    sid=sid,
                )
            )

        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            conns: dict[str, SmappeeDeviceHandle] = bucket.get("connector_clients", {})

            if getattr(coord, "dashboard_client", None) is not None:
                st_client: SmappeeDeviceHandle | None = bucket.get("station_client") or getattr(
                    coord, "station_client", None
                )
                if st_client is not None:
                    entities.append(
                        SmappeeOfflineFailsafeCurrentNumber(
                            coordinator=coord,
                            api_client=st_client,
                            sid=sid,
                            station_uuid=st_uuid,
                        )
                    )

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
                    SmappeeConnectorMaxCurrentNumber(
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

    async_add_entities(entities, False)


class _BaseNumber(RestoreNumber):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

    def _post_init(self, unit: str, min_value: float, max_value: float, step: float) -> None:
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step


class SmappeeCombinedCurrentSlider(SmappeeConnectorEntity, _BaseNumber):
    """Combined slider showing current (A), with percentage in attributes."""

    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_icon = "mdi:current-ac"
    _attr_translation_key = "max_charging_speed"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
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
            api_client,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="number:current",
        )
        self.api_client = api_client
        self._post_init(UnitOfElectricCurrent.AMPERE, float(min_current), float(max_current), 0.1)

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self.connector_uuid) if data else None

    @property
    def native_value(self) -> float | None:
        st = self._state()
        if not st:
            return None
        if st.selected_current_limit is not None:
            return round(
                max(
                    float(st.min_current),
                    min(float(st.max_current), float(st.selected_current_limit)),
                ),
                1,
            )
        if st.max_current <= st.min_current:
            return round(float(st.min_current), 1)
        rng = st.max_current - st.min_current
        pct = st.selected_percentage_limit or 0
        cur = st.min_current + (float(pct) / 100.0) * rng
        return round(max(float(st.min_current), min(float(st.max_current), cur)), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._state()
        if not st:
            return {}
        cur = self.native_value
        if cur is None:
            cur = float(st.min_current)
        if st.max_current <= st.min_current:
            return {
                "percentage": None,
                "percentage_formatted": "\u2014",
                "fixed_range": True,
            }
        rng = st.max_current - st.min_current
        pct = int(round((cur - st.min_current) * 100.0 / rng))
        return {"percentage": pct, "percentage_formatted": f"{pct}%", "fixed_range": False}

    async def async_set_native_value(self, value: float) -> None:
        st = self._state()
        if not st:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="connector_service_failed",
                translation_placeholders={
                    "method_name": "set_current",
                    "error": "connector state is unavailable",
                },
            )
        min_c, max_c = st.min_current, st.max_current
        cur_float, pct_int = await self.api_client.set_current(
            value, min_current=int(min_c), max_current=int(max_c)
        )
        st.selected_current_limit = cur_float
        st.selected_percentage_limit = pct_int
        data = self.coordinator.data
        if data:
            self.coordinator.async_set_updated_data(data)
        self.coordinator.async_schedule_dashboard_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        st = self._state()
        if st:
            new_min = float(st.min_current)
            new_max = float(st.max_current)

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
                    "Updated slider range to %.1f-%.1f A (was %.1f-%.1f)",
                    new_min,
                    new_max,
                    old_min,
                    old_max,
                )

        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:  # RestoreEntity
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if not last or last.native_value is None:
            return
        try:
            restored = round(float(last.native_value), 1)
        except (TypeError, ValueError):
            return
        updated_data = False
        st = self._state()
        if st:
            # Only restore when the API has not provided either representation.
            if st.selected_current_limit is None and st.selected_percentage_limit is None:
                st.selected_current_limit = restored
                # Derive percentage if range known
                if st.max_current > st.min_current:
                    rng = st.max_current - st.min_current
                    pct = int(round((restored - st.min_current) * 100.0 / rng))
                    st.selected_percentage_limit = max(0, min(100, pct))
                data = self.coordinator.data
                if data:
                    self.coordinator.async_set_updated_data(data)
                    updated_data = True
        if not updated_data:
            if getattr(self, "platform", None) is not None:
                self.async_write_ha_state()


class SmappeeConnectorMaxCurrentNumber(SmappeeConnectorEntity, _BaseNumber):
    """Connector configuration maximum current."""

    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:current-ac"
    _attr_translation_key = "connector_max_current"

    def __init__(
        self,
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
            unique_suffix="number:connector_max_current",
        )
        self.api_client = api_client
        data: IntegrationData | None = coordinator.data
        st: ConnectorState | None = data.connectors.get(connector_uuid) if data else None
        min_current = st.min_current if st else DEFAULT_MIN_CURRENT
        self._post_init(
            UnitOfElectricCurrent.AMPERE,
            float(min_current),
            float(DEFAULT_MAX_CURRENT),
            1,
        )

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self.connector_uuid) if data else None

    @property
    def native_value(self) -> int | None:
        st = self._state()
        value = getattr(st, "max_current", None) if st else None
        return int(value) if value is not None else None

    @callback
    def _handle_coordinator_update(self) -> None:
        st = self._state()
        if st and st.min_current is not None:
            self._attr_native_min_value = float(st.min_current)
        super()._handle_coordinator_update()

    async def async_set_native_value(self, value: float) -> None:
        st = self._state()
        if not st:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="connector_service_failed",
                translation_placeholders={
                    "method_name": "set_connector_max_current",
                    "error": "connector state is unavailable",
                },
            )
        min_current = int(st.min_current or DEFAULT_MIN_CURRENT)
        amps = max(min_current, min(DEFAULT_MAX_CURRENT, int(round(value))))
        await self.api_client.set_connector_max_current(amps)
        st.max_current = amps
        if st.selected_current_limit is not None:
            st.selected_current_limit = min(float(st.selected_current_limit), float(amps))
        data = self.coordinator.data
        if data:
            self.coordinator.async_set_updated_data(data)
        self.coordinator.async_schedule_dashboard_refresh()


class SmappeeMinSurplusPctNumber(SmappeeConnectorEntity, _BaseNumber):
    """Min Surplus Percentage (connector-level)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:solar-power"
    _attr_translation_key = "min_surpluspct"

    def __init__(
        self,
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
            unique_suffix="number:min_surpluspct",
        )
        self.api_client = api_client
        self._post_init(PERCENTAGE, 0, 100, 1)

    def _state(self) -> ConnectorState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.connectors.get(self.connector_uuid) if data else None

    @property
    def native_value(self) -> int | None:
        st = self._state()
        if not st or st.min_surpluspct is None:
            return None
        return int(st.min_surpluspct)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.api_client.set_min_surpluspct(int(value))
        except (ClientError, TimeoutError, RuntimeError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="connector_service_failed",
                translation_placeholders={
                    "method_name": "set_min_surpluspct",
                    "error": str(err),
                },
            ) from err
        # Optimistic immediate update
        st = self._state()
        if st:
            st.min_surpluspct = int(value)
            data = self.coordinator.data
            if data:
                self.coordinator.async_set_updated_data(data)
        self.coordinator.async_schedule_dashboard_refresh()

    async def async_added_to_hass(self) -> None:  # RestoreEntity
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if not last or last.native_value is None:
            return
        try:
            restored = int(float(last.native_value))
        except (TypeError, ValueError):
            return
        st = self._state()
        updated_data = False
        if st and st.min_surpluspct is None:
            st.min_surpluspct = restored
            data = self.coordinator.data
            if data:
                self.coordinator.async_set_updated_data(data)
                updated_data = True
        if not updated_data:
            if getattr(self, "platform", None) is not None:
                self.async_write_ha_state()


class SmappeeCapacityMaximumPowerNumber(SmappeeSiteEntity[SmappeeCoordinator], _BaseNumber):
    """Dashboard capacity protection maximum power setting."""

    _attr_device_class = NumberDeviceClass.POWER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:transmission-tower"
    _attr_translation_key = "capacity_maximum_power"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str | None = None,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="number:capacity_maximum_power",
        )
        self._post_init(UnitOfPower.KILO_WATT, 0, 10, 0.1)

    def _station_state(self) -> StationState | None:
        """Return the current station state."""
        data: IntegrationData | None = self.coordinator.data
        return data.station if data else None

    @property
    def available(self) -> bool:
        st = self._station_state()
        return bool(
            super().available
            and getattr(self.coordinator, "dashboard_client", None)
            and st is not None
        )

    @property
    def native_value(self) -> float | None:
        st = self._station_state()
        value = getattr(st, "capacity_maximum_power_kw", None) if st else None
        return round(float(value), 1) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        dashboard = getattr(self.coordinator, "dashboard_client", None)
        data = self.coordinator.data
        st = self._station_state()
        if dashboard is None or data is None or st is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="station_unavailable",
            )
        power_kw = round(max(0.0, float(value)), 1)
        active = _active_or_true(st.capacity_protection_active)
        await dashboard.async_set_capacity_protection(self._sid, active, power_kw)
        st.capacity_maximum_power_kw = power_kw
        self.coordinator.async_set_updated_data(data)
        self.coordinator.async_schedule_dashboard_refresh()


class SmappeeOverloadMaximumLoadNumber(SmappeeSiteEntity[SmappeeCoordinator], _BaseNumber):
    """Dashboard overload protection maximum load setting."""

    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:current-ac"
    _attr_translation_key = "overload_maximum_load"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        sid: int,
        station_uuid: str | None = None,
    ) -> None:
        SmappeeSiteEntity.__init__(
            self,
            coordinator,
            sid,
            unique_suffix="number:overload_maximum_load",
        )
        self._post_init(UnitOfElectricCurrent.AMPERE, 0, 32, 1)

    def _station_state(self) -> StationState | None:
        """Return the current station state."""
        data: IntegrationData | None = self.coordinator.data
        return data.station if data else None

    @property
    def available(self) -> bool:
        st = self._station_state()
        return bool(
            super().available
            and getattr(self.coordinator, "dashboard_client", None)
            and st is not None
        )

    @property
    def native_value(self) -> int | None:
        st = self._station_state()
        value = getattr(st, "overload_maximum_load_a", None) if st else None
        return int(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        dashboard = getattr(self.coordinator, "dashboard_client", None)
        data = self.coordinator.data
        st = self._station_state()
        if dashboard is None or data is None or st is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="station_unavailable",
            )
        maximum_load_a = max(0, int(round(value)))
        active = _active_or_true(st.overload_protection_active)
        await dashboard.async_set_overload_protection(self._sid, active, maximum_load_a)
        st.overload_maximum_load_a = maximum_load_a
        self.coordinator.async_set_updated_data(data)
        self.coordinator.async_schedule_dashboard_refresh()


class SmappeeOfflineFailsafeCurrentNumber(SmappeeStationEntity, _BaseNumber):
    """Station-level offline charging failsafe current."""

    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:cloud-alert"
    _attr_translation_key = "offline_failsafe_current"

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="number:offline_failsafe_current",
        )
        self.api_client = api_client
        self._post_init(UnitOfElectricCurrent.AMPERE, 0, 32, 1)

    def _station_state(self) -> StationState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.station if data else None

    @property
    def available(self) -> bool:
        st = self._station_state()
        return bool(
            super().available
            and getattr(self.coordinator, "dashboard_client", None)
            and st is not None
            and st.offline_charging_enabled is True
        )

    @property
    def native_value(self) -> int | None:
        st = self._station_state()
        value = getattr(st, "offline_failsafe_current_a", None) if st else None
        return int(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        data = self.coordinator.data
        st = self._station_state()
        if data is None or st is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="station_unavailable",
            )
        failsafe = max(0, int(round(value)))
        enabled = (
            bool(st.offline_charging_enabled) if st.offline_charging_enabled is not None else True
        )
        await self.api_client.set_offline_charging_config(enabled, failsafe)
        st.offline_charging_enabled = enabled
        st.offline_failsafe_current_a = failsafe
        self.coordinator.async_set_updated_data(data)
        self.coordinator.async_schedule_dashboard_refresh()
