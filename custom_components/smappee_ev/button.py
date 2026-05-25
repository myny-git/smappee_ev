from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api_client import SmappeeApiClient
from .base_entities import SmappeeConnectorEntity
from .coordinator import SmappeeCoordinator
from .data import RuntimeData
from .helpers import build_connector_label

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee EV buttons (multi-station)."""
    runtime: RuntimeData = config_entry.runtime_data  # type: ignore[attr-defined]
    sites = runtime.sites

    # Remove stale start_charging button entities left over from older versions
    registry = er.async_get(hass)
    stale = [
        entry.entity_id
        for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id)
        if entry.unique_id.endswith(":button:start_charging")
    ]
    for entity_id in stale:
        registry.async_remove(entity_id)
        _LOGGER.debug("Removed stale start_charging button entity: %s", entity_id)

    entities: list[ButtonEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            conns: dict[str, SmappeeApiClient] = bucket.get("connector_clients", {})

            for cuuid, client in (conns or {}).items():
                lbl = build_connector_label(client, cuuid).split(" ", 1)[1]  # get number / tail
                entities.extend([
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


class SmappeeActionButton(SmappeeConnectorEntity, ButtonEntity):
    """Generic action button for a connector using shared base entity."""

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
        # Build name/unique id via base class
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix=f"button:{action}",
            name=name,
        )
        self.api_client = api_client
        self._action = action

    async def async_press(self) -> None:
        """Execute the action on press."""
        if self._action == "pause_charging":
            await self.api_client.set_charging_mode("PAUSED")
        elif self._action == "stop_charging":
            await self.api_client.stop_charging()
        elif self._action == "set_charging_mode":
            data = self.coordinator.data if self.coordinator else None
            mode = "NORMAL"
            if data and self.connector_uuid in (data.connectors or {}):
                conn = data.connectors[self.connector_uuid]
                mode = (
                    getattr(conn, "selected_mode", None)
                    or getattr(conn, "ui_mode_base", None)
                    or "NORMAL"
                )
            await self.api_client.set_charging_mode(mode)
        else:
            _LOGGER.debug("Unknown action for button: %s", self._action)
