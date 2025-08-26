from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator

_LOGGER = logging.getLogger(__name__)


def _station_serial(coord: SmappeeCoordinator) -> str:
    return getattr(coord.station_client, "serial_id", "unknown")


def _station_name(coord: SmappeeCoordinator, sid: int) -> str:
    st = coord.data.station if coord.data else None
    return getattr(st, "name", None) or f"Smappee EV {_station_serial(coord)}"


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee EV buttons (multi-site)."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[int, SmappeeCoordinator] = store["coordinators"]
    station_clients: dict[int, SmappeeApiClient] = store["station_clients"]
    connector_clients: dict[int, dict[str, SmappeeApiClient]] = store["connector_clients"]

    entities: list[ButtonEntity] = []
    for sid, coord in coordinators.items():
        st_client = station_clients.get(sid)
        if not st_client:
            continue
        # Per connector: Start / Pause / Stop / Set mode
        for uuid, client in (connector_clients.get(sid) or {}).items():
            num = getattr(client, "connector_number", None)
            num_lbl = f"{num}" if num is not None else uuid[-4:]
            entities.append(
                SmappeeActionButton(
                    coordinator=coord,
                    api_client=client,
                    sid=sid,
                    uuid=uuid,
                    name=f"Start charging {num_lbl}",
                    action="start_charging",
                    unique_id_suffix=f"{uuid}:start",
                )
            )
            entities.append(
                SmappeeActionButton(
                    coordinator=coord,
                    api_client=client,
                    sid=sid,
                    uuid=uuid,
                    name=f"Pause charging {num_lbl}",
                    action="pause_charging",
                    unique_id_suffix=f"{uuid}:pause",
                )
            )
            entities.append(
                SmappeeActionButton(
                    coordinator=coord,
                    api_client=client,
                    sid=sid,
                    uuid=uuid,
                    name=f"Stop charging {num_lbl}",
                    action="stop_charging",
                    unique_id_suffix=f"{uuid}:stop",
                )
            )
            entities.append(
                SmappeeActionButton(
                    coordinator=coord,
                    api_client=client,
                    sid=sid,
                    uuid=uuid,
                    name=f"Set charging mode {num_lbl}",
                    action="set_charging_mode",
                    unique_id_suffix=f"{uuid}:setmode",
                )
            )

    async_add_entities(entities)


class SmappeeActionButton(CoordinatorEntity[SmappeeCoordinator], ButtonEntity):
    """Generic action button for a connector."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        uuid: str | None,
        name: str,
        action: str,
        unique_id_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._sid = sid
        self._uuid = uuid
        self._action = action
        self._attr_name = name
        self._attr_unique_id = f"{sid}:{_station_serial(coordinator)}:{unique_id_suffix}"

    @property
    def device_info(self):
        serial = _station_serial(self.coordinator)
        return {
            "identifiers": {(DOMAIN, f"{self._sid}:{serial}")},
            "name": _station_name(self.coordinator, self._sid),
            "manufacturer": "Smappee",
        }

    async def async_press(self) -> None:
        """Execute the action on press."""
        if self._action == "start_charging":
            # Pass current if we know it, otherwise call without arg
            data = self.coordinator.data if self.coordinator else None
            target_a = 6
            if data and self._uuid and self._uuid in (data.connectors or {}):
                conn = data.connectors[self._uuid]
                sel = getattr(conn, "selected_current_limit", None)
                mn = getattr(conn, "min_current", None)
                if isinstance(sel, int) and sel > 0:
                    target_a = sel
                elif isinstance(mn, int) and mn > 0:
                    target_a = mn
            await self.api_client.start_charging(current=target_a)
        elif self._action == "pause_charging":
            await self.api_client.pause_charging()
        elif self._action == "stop_charging":
            await self.api_client.stop_charging()
        elif self._action == "set_charging_mode":
            data = self.coordinator.data if self.coordinator else None
            mode = "NORMAL"
            if data and self._uuid and self._uuid in (data.connectors or {}):
                conn = data.connectors[self._uuid]

                mode = (
                    getattr(conn, "selected_mode", None)
                    or getattr(conn, "ui_mode_base", None)
                    or "NORMAL"
                )
            await self.api_client.set_charging_mode(mode)
        else:
            _LOGGER.debug("Unknown action for button: %s", self._action)
