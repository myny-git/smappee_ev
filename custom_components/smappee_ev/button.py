from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entities import SmappeeConnectorEntity, SmappeeStationEntity
from .coordinator import SmappeeCoordinator
from .data import SmappeeEvConfigEntry
from .device_handle import SmappeeDeviceHandle

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1

ACTION_ICONS = {
    "start_charging": "mdi:play-circle",
    "pause_charging": "mdi:pause-circle",
    "stop_charging": "mdi:stop-circle",
    "resume_charging": "mdi:play-pause",
    "restart_charging_station": "mdi:restart",
}


def _dashboard_mode(mode: str | None) -> str | None:
    """Return a Dashboard v10 charging mode, accepting legacy/restored labels."""
    mode_up = str(mode or "").upper()
    if mode_up in {"STANDARD", "NORMAL"}:
        return "STANDARD"
    if mode_up in {"SMART", "SOLAR"}:
        return mode_up
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV buttons (multi-station)."""
    runtime = config_entry.runtime_data
    sites = runtime.sites

    entities: list[ButtonEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeDeviceHandle | None = bucket.get("station_client") or getattr(
                coord, "station_client", None
            )
            conns: dict[str, SmappeeDeviceHandle] = bucket.get("connector_clients", {})

            if st_client is not None:
                entities.append(
                    SmappeeStationActionButton(
                        coordinator=coord,
                        api_client=st_client,
                        sid=sid,
                        station_uuid=st_uuid,
                        action="restart_charging_station",
                    )
                )

            for cuuid, client in (conns or {}).items():
                entities.extend(
                    [
                        SmappeeActionButton(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                            action="start_charging",
                        ),
                        SmappeeActionButton(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                            action="pause_charging",
                        ),
                        SmappeeActionButton(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                            action="stop_charging",
                        ),
                        SmappeeActionButton(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                            action="resume_charging",
                        ),
                    ]
                )

    async_add_entities(entities, False)


class SmappeeStationActionButton(SmappeeStationEntity, ButtonEntity):
    """Generic action button for a station using shared base entity."""

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        action: str,
        name: str | None = None,
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix=f"button:{action}",
        )
        self._attr_translation_key = action
        if name is not None:
            self._attr_name = name
        self.api_client = api_client
        self._action = action
        self._attr_icon = ACTION_ICONS.get(action)

    async def async_press(self) -> None:
        """Execute the action on press."""
        if self._action == "restart_charging_station":
            await self.api_client.restart_charging_station()
            self.coordinator.async_schedule_dashboard_refresh()
        else:
            _LOGGER.debug("Unknown station action for button: %s", self._action)


class SmappeeActionButton(SmappeeConnectorEntity, ButtonEntity):
    """Generic action button for a connector using shared base entity."""

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
        action: str,
        name: str | None = None,
    ) -> None:
        # Build unique id via base class
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            api_client,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix=f"button:{action}",
        )
        self._attr_translation_key = action
        if name is not None:
            self._attr_name = name
        self.api_client = api_client
        self._action = action
        self._attr_icon = ACTION_ICONS.get(action)

    async def async_press(self) -> None:
        """Execute the action on press."""
        if self._action == "start_charging":
            data = self.coordinator.data if self.coordinator else None
            target_a: float = 6.0
            conn = None
            if data and self.connector_uuid in (data.connectors or {}):
                conn = data.connectors[self.connector_uuid]
                sel = getattr(conn, "selected_current_limit", None)
                mn = getattr(conn, "min_current", None)
                if isinstance(sel, int | float) and sel > 0:
                    target_a = sel
                elif isinstance(mn, int) and mn > 0:
                    target_a = mn
            min_current = getattr(conn, "min_current", 6) if conn else 6
            max_current = getattr(conn, "max_current", 32) if conn else 32
            if not isinstance(min_current, int | float) or min_current <= 0:
                min_current = 6
            if not isinstance(max_current, int | float) or max_current <= 0:
                max_current = 32
            cur, pct = await self.api_client.start_charging(
                current=target_a,
                min_current=int(min_current),
                max_current=int(max_current),
            )
            if data and conn is not None:
                conn.selected_current_limit = cur
                conn.selected_percentage_limit = pct
                self.coordinator.async_set_updated_data(data)
            self.coordinator.async_schedule_dashboard_refresh()
        elif self._action == "pause_charging":
            await self.api_client.pause_charging()
            self.coordinator.async_schedule_dashboard_refresh()
        elif self._action == "stop_charging":
            await self.api_client.stop_charging()
            self.coordinator.async_schedule_dashboard_refresh()
        elif self._action == "resume_charging":
            data = self.coordinator.data if self.coordinator else None
            mode = "STANDARD"
            if data and self.connector_uuid in (data.connectors or {}):
                conn = data.connectors[self.connector_uuid]
                mode = (
                    _dashboard_mode(getattr(conn, "selected_mode", None))
                    or _dashboard_mode(getattr(conn, "ui_mode_base", None))
                    or "STANDARD"
                )
            await self.api_client.set_charging_mode(mode)
            self.coordinator.async_schedule_dashboard_refresh()
        else:
            _LOGGER.debug("Unknown action for button: %s", self._action)
