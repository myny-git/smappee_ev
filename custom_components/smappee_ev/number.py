from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import ConnectorState, IntegrationData
from .helpers import make_device_info, make_unique_id

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV number entities (multi-station)."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    sites = store.get(
        "sites", {}
    )  # { sid: { "stations": { st_uuid: {coordinator, station_client, connector_clients, ...} } } }

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


class _Base(CoordinatorEntity[SmappeeCoordinator], NumberEntity):
    """Base class for Smappee EV numbers."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        *,
        sid: int,
        station_uuid: str,
        connector_uuid: str | None,
        name: str,
        unique_id_suffix: str,
        unit: str,
        min_value: int,
        max_value: int,
        step: int = 1,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._sid = sid
        self._station_uuid = station_uuid
        self._connector_uuid = connector_uuid
        self._serial = getattr(coordinator.station_client, "serial_id", "unknown")
        self._attr_name = name
        self._attr_unique_id = make_unique_id(
            sid, self._serial, station_uuid, connector_uuid, unique_id_suffix
        )
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step

    @property
    def device_info(self):
        """Return device info for the station device in HA."""
        station_name = getattr(getattr(self.coordinator.data, "station", None), "name", None)
        return make_device_info(
            self._sid,
            self._serial,
            self._station_uuid,
            station_name,
        )


class SmappeeCombinedCurrentSlider(_Base):
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
        self._uuid = connector_uuid
        data: IntegrationData | None = coordinator.data
        st: ConnectorState | None = data.connectors.get(connector_uuid) if data else None

        min_current = st.min_current if st else 6
        max_current = st.max_current if st else 32

        super().__init__(
            coordinator,
            api_client,
            sid=sid,
            station_uuid=station_uuid,
            connector_uuid=connector_uuid,
            name=f"Max charging speed {getattr(api_client, 'connector_number', None) or connector_uuid[-4:]}",
            unique_id_suffix="number:current",
            unit=UnitOfElectricCurrent.AMPERE,
            min_value=min_current,
            max_value=max_current,
            step=1,
        )

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
            await self.api_client.start_charging(min_c)
            st.selected_current_limit = min_c
        else:
            rng = max_c - min_c
            pct = int(round((val - min_c) * 100.0 / rng))
            pct = max(0, min(100, pct))
            await self.api_client.set_percentage_limit(pct)
            st.selected_current_limit = val
            st.selected_percentage_limit = pct

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


class SmappeeBrightnessNumber(_Base):
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
        super().__init__(
            coordinator,
            api_client,
            sid=sid,
            station_uuid=station_uuid,
            connector_uuid=None,
            name="LED Brightness",
            unique_id_suffix="number:led_brightness",
            unit=PERCENTAGE,
            min_value=0,
            max_value=100,
            step=1,
        )

    @property
    def native_value(self) -> int | None:
        data: IntegrationData | None = self.coordinator.data
        return int(data.station.led_brightness) if data and data.station else None

    async def async_set_native_value(self, value: float) -> None:
        val = max(0, min(100, int(value)))
        await self.api_client.set_brightness(val)


class SmappeeMinSurplusPctNumber(_Base):
    """Min Surplus Percentage (connector-level)."""

    def __init__(
        self,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ) -> None:
        self._uuid = connector_uuid
        super().__init__(
            coordinator,
            api_client,
            sid=sid,
            station_uuid=station_uuid,
            connector_uuid=connector_uuid,
            name=f"Min Surplus Percentage {getattr(api_client, 'connector_number', None) or connector_uuid[-4:]}",
            unique_id_suffix="number:min_surpluspct",
            unit=PERCENTAGE,
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
