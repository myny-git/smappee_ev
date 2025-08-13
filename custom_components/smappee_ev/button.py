from __future__ import annotations

import asyncio
import logging

from aiohttp import ClientError
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api_client import SmappeeApiClient
from .const import DOMAIN
from .coordinator import SmappeeCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV buttons from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: SmappeeCoordinator = data["coordinator"]
    connector_clients: dict[str, SmappeeApiClient] = data["connector_clients"]
    station_client: SmappeeApiClient = data["station_client"]

    entities: list[ButtonEntity] = []

    # Connector-based buttons
    for uuid, client in connector_clients.items():             # <— had values() eerst
        connector = client.connector_number or 1
        entities.extend(
            [
                SmappeeActionButton(api_client=client, uuid=uuid, name=f"Start charging {connector}", action="start_charging", unique_id_suffix=f"start_{connector}"),
                SmappeeActionButton(api_client=client, uuid=uuid, name=f"Stop charging {connector}", action="stop_charging", unique_id_suffix=f"stop_{connector}"),
                SmappeeActionButton(api_client=client, uuid=uuid, name=f"Pause charging {connector}", action="pause_charging", unique_id_suffix=f"pause_{connector}"),
                SmappeeActionButton(api_client=client, uuid=uuid, name=f"Set charging mode {connector}", action="set_charging_mode", unique_id_suffix=f"mode_{connector}"),
            ]
        )

    # Station-level buttons
    entities.extend(
        [
            SmappeeActionButton(
                api_client=station_client,
                name="Set LED brightness",
                action="set_brightness",
                unique_id_suffix="set_brightness",
            ),
            SmappeeActionButton(
                api_client=station_client,
                name="Set available",
                action="set_available",
                unique_id_suffix="set_available",
            ),
            SmappeeActionButton(
                api_client=station_client,
                name="Set unavailable",
                action="set_unavailable",
                unique_id_suffix="set_unavailable",
            ),
        ]
    )

    for ent in entities:
        ent._smappee_coordinator = coordinator

    async_add_entities(entities)


class SmappeeActionButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, *, api_client: SmappeeApiClient, uuid: str | None = None,
                 name: str, action: str, unique_id_suffix: str) -> None:
        self.api_client = api_client
        self._uuid = uuid                           # <— nieuw
        self._attr_name = name
        self._attr_unique_id = f"{api_client.serial_id}_{unique_id_suffix}"
        self._action = action
        self._smappee_coordinator: SmappeeCoordinator | None = None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    async def _async_refresh(self) -> None:
        """Small shared helper: fetch coordinator and refresh once."""
        try:
            if self._smappee_coordinator:
                await self._smappee_coordinator.async_request_refresh()
        except (TimeoutError, ClientError, asyncio.CancelledError, UpdateFailed, HomeAssistantError) as err:
            _LOGGER.debug("Coordinator refresh failed after '%s': %s", self._action, err)

    async def async_press(self) -> None:
        try:
            if self._action == "start_charging":
                current = None
                data = self._smappee_coordinator.data if self._smappee_coordinator else None
                if data and self._uuid and self._uuid in data.connectors:
                    st = data.connectors[self._uuid]
                    current = st.selected_current_limit if st.selected_current_limit is not None else st.min_current
                if current is None:
                    current = 6  # ultimate safety
                await self.api_client.start_charging(int(current))

            elif self._action == "stop_charging":
                await self.api_client.stop_charging()

            elif self._action == "pause_charging":
                await self.api_client.pause_charging()

            elif self._action == "set_available":
                await self.api_client.set_available()

            elif self._action == "set_unavailable":
                await self.api_client.set_unavailable()

            elif self._action == "set_brightness":
                brightness = 70
                data = self._smappee_coordinator.data if self._smappee_coordinator else None
                if data and data.station:
                    brightness = int(data.station.led_brightness)
                await self.api_client.set_brightness(brightness)

            elif self._action == "set_charging_mode":
                mode = "NORMAL"
                limit = None
                data = self._smappee_coordinator.data if self._smappee_coordinator else None
                if data and self._uuid and self._uuid in data.connectors:
                    st = data.connectors[self._uuid]
                    mode = st.selected_mode or "NORMAL"
                    if mode == "NORMAL":
                        limit = st.selected_current_limit if st.selected_current_limit is not None else st.min_current

                await self.api_client.set_charging_mode(mode, limit)

        finally:
            await self._async_refresh()

