from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee EV number entities (per station and connector)."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[int, SmappeeCoordinator] = store["coordinators"]
    station_clients: dict[int, SmappeeApiClient] = store["station_clients"]
    connector_clients: dict[int, dict[str, SmappeeApiClient]] = store["connector_clients"]

    entities: list[NumberEntity] = []

    for sid, coord in coordinators.items():
        st_client = station_clients.get(sid)
        if not st_client:
            continue

        serial = getattr(coord.station_client, "serial_id", "unknown")
        station_name = (
            getattr(getattr(coord.data, "station", None), "name", None) or f"Smappee EV {serial}"
        )

        # LED Brightness (station-level)
        entities.append(
            SmappeeNumber(
                coordinator=coord,
                api_client=st_client,
                sid=sid,
                uuid=None,
                name=f"{station_name} – LED Brightness",
                unique_id_suffix=f"{serial}:led_brightness",
                action="led_brightness",
                min_value=0,
                max_value=10,
                step=1,
            )
        )

        # Per connector: Current limit + Percentage limit
        for uuid, client in (connector_clients.get(sid) or {}).items():
            entities.append(
                SmappeeNumber(
                    coordinator=coord,
                    api_client=client,
                    sid=sid,
                    uuid=uuid,
                    name=f"{station_name} – Current limit",
                    unique_id_suffix=f"{serial}:{uuid}:current_limit",
                    action="current_limit",
                    min_value=6,
                    max_value=32,
                    step=1,
                )
            )
            entities.append(
                SmappeeNumber(
                    coordinator=coord,
                    api_client=client,
                    sid=sid,
                    uuid=uuid,
                    name=f"{station_name} – Percentage limit",
                    unique_id_suffix=f"{serial}:{uuid}:percentage_limit",
                    action="percentage_limit",
                    min_value=1,
                    max_value=100,
                    step=1,
                )
            )

    async_add_entities(entities)


class SmappeeNumber(CoordinatorEntity[SmappeeCoordinator], NumberEntity):
    """Number entity for Smappee EV Wallbox."""

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        uuid: str | None,
        name: str,
        unique_id_suffix: str,
        action: str,
        min_value: float,
        max_value: float,
        step: float,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._sid = sid
        self._uuid = uuid
        self._action = action
        self._attr_name = name
        self._attr_unique_id = f"{sid}:{unique_id_suffix}"
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step

    @property
    def device_info(self):
        serial = getattr(self.coordinator.station_client, "serial_id", "unknown")
        return {
            "identifiers": {(DOMAIN, f"{self._sid}:{serial}")},
            "name": getattr(getattr(self.coordinator.data, "station", None), "name", None)
            or f"Smappee EV {serial}",
            "manufacturer": "Smappee",
        }

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if not data:
            return None

        if self._action == "led_brightness":
            return getattr(data, "led_brightness", None)

        if self._uuid and self._uuid in (data.connectors or {}):
            conn = data.connectors[self._uuid]
            if self._action == "current_limit":
                return getattr(conn, "selected_current_limit", None)
            if self._action == "percentage_limit":
                return getattr(conn, "selected_percentage_limit", None)
        return None

    async def async_set_native_value(self, value: float) -> None:
        if self._action == "led_brightness":
            await self.api_client.set_brightness(int(value))
        elif self._action == "current_limit":
            data = self.coordinator.data
            if not data or not self._uuid or self._uuid not in (data.connectors or {}):
                _LOGGER.debug("No connector context for current_limit set; skipping")
                return
            conn = data.connectors[self._uuid]
            min_c = int(getattr(conn, "min_current", 6) or 6)
            max_c = int(getattr(conn, "max_current", 32) or 32)
            val = int(value)
            if max_c <= min_c:
                await self.api_client.start_charging(min_c)
                conn.selected_current_limit = min_c
            else:
                rng = max_c - min_c
                pct = int(round((val - min_c) * 100.0 / rng))
                pct = max(0, min(100, pct))
                await self.api_client.set_percentage_limit(pct)
                conn.selected_current_limit = val
                conn.selected_percentage_limit = pct
        elif self._action == "percentage_limit":
            await self.api_client.set_percentage_limit(int(value))
        else:
            _LOGGER.warning("Unknown action for number entity: %s", self._action)
