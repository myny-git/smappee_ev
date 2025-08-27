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
from .helpers import make_device_info, make_unique_id

_LOGGER = logging.getLogger(__name__)


def _station_serial(coord: SmappeeCoordinator) -> str:
    return getattr(coord.station_client, "serial_id", "unknown")


def _station_name(coord: SmappeeCoordinator, sid: int) -> str:
    st = coord.data.station if coord.data else None
    return getattr(st, "name", None) or f"Smappee EV {_station_serial(coord)}"


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee EV buttons (multi-station)."""
    store = hass.data[DOMAIN][config_entry.entry_id]
    sites = store.get(
        "sites", {}
    )  # { sid: { "stations": { st_uuid: {coordinator, station_client, connector_clients, ...} } } }

    entities: list[ButtonEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            conns: dict[str, SmappeeApiClient] = bucket.get("connector_clients", {})

            for cuuid, client in (conns or {}).items():
                num = getattr(client, "connector_number", None)
                lbl = f"{num}" if isinstance(num, int) else cuuid[-4:]
                entities.extend([
                    SmappeeActionButton(
                        coordinator=coord,
                        api_client=client,
                        sid=sid,
                        station_uuid=st_uuid,
                        connector_uuid=cuuid,
                        name=f"Start charging {lbl}",
                        action="start_charging",
                    ),
                    SmappeeActionButton(
                        coordinator=coord,
                        api_client=client,
                        sid=sid,
                        station_uuid=st_uuid,
                        connector_uuid=cuuid,
                        name=f"Pause charging {lbl}",
                        action="pause_charging",
                    ),
                    SmappeeActionButton(
                        coordinator=coord,
                        api_client=client,
                        sid=sid,
                        station_uuid=st_uuid,
                        connector_uuid=cuuid,
                        name=f"Stop charging {lbl}",
                        action="stop_charging",
                    ),
                    SmappeeActionButton(
                        coordinator=coord,
                        api_client=client,
                        sid=sid,
                        station_uuid=st_uuid,
                        connector_uuid=cuuid,
                        name=f"Set charging mode {lbl}",
                        action="set_charging_mode",
                    ),
                ])

    async_add_entities(entities, True)


class SmappeeActionButton(CoordinatorEntity[SmappeeCoordinator], ButtonEntity):
    """Generic action button for a connector."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeApiClient,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
        name: str,
        action: str,
    ) -> None:
        super().__init__(coordinator)
        self.api_client = api_client
        self._sid = sid
        self._station_uuid = station_uuid
        self._connector_uuid = connector_uuid
        self._action = action
        self._attr_name = name
        self._serial = getattr(coordinator.station_client, "serial_id", "unknown")
        # Globally stable unique_id: sid:serial:station_uuid:connector_uuid:button:<action>
        self._attr_unique_id = make_unique_id(
            sid, self._serial, station_uuid, connector_uuid, f"button:{action}"
        )

    @property
    def device_info(self):
        station_name = getattr(getattr(self.coordinator.data, "station", None), "name", None)
        return make_device_info(
            self._sid,
            self._serial,
            self._station_uuid,
            station_name,
        )

    async def async_press(self) -> None:
        """Execute the action on press."""
        if self._action == "start_charging":
            # Pass current if we know it, otherwise call without arg
            data = self.coordinator.data if self.coordinator else None
            target_a = 6
            if data and self._connector_uuid in (data.connectors or {}):
                conn = data.connectors[self._connector_uuid]
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
            if data and self._connector_uuid in (data.connectors or {}):
                conn = data.connectors[self._connector_uuid]

                mode = (
                    getattr(conn, "selected_mode", None)
                    or getattr(conn, "ui_mode_base", None)
                    or "NORMAL"
                )
            await self.api_client.set_charging_mode(mode)
        else:
            _LOGGER.debug("Unknown action for button: %s", self._action)
