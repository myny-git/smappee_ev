from __future__ import annotations

import logging

from typing import Any, Dict

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN
from .api_client import SmappeeApiClient
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
# Connector-based buttons (per connector client)
    for client in connector_clients.values():
        connector = client.connector_number or 1
        entities.extend(
            [
                SmappeeActionButton(
                    api_client=client,
                    name=f"Start charging {connector}",
                    action="start_charging",
                    unique_id_suffix=f"start_{connector}",
                ),
                SmappeeActionButton(
                    api_client=client,
                    name=f"Stop charging {connector}",
                    action="stop_charging",
                    unique_id_suffix=f"stop_{connector}",
                ),
                SmappeeActionButton(
                    api_client=client,
                    name=f"Pause charging {connector}",
                    action="pause_charging",
                    unique_id_suffix=f"pause_{connector}",
                ),
                SmappeeActionButton(
                    api_client=client,
                    name=f"Set charging mode {connector}",
                    action="set_charging_mode",
                    unique_id_suffix=f"mode_{connector}",
                ),
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

    def __init__(
        self,
        *,
        api_client: SmappeeApiClient,
        name: str,
        action: str,
        unique_id_suffix: str,
    ) -> None:
        self.api_client = api_client
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
            data = self.hass.data[DOMAIN][self.platform.config_entry.entry_id]
            coordinator: SmappeeCoordinator | None = data.get("coordinator")
            if coordinator:
                await coordinator.async_request_refresh()
        except Exception as exc:
            _LOGGER.debug("Coordinator refresh failed after '%s': %s", self._action, exc)


    async def async_press(self) -> None:
        # _LOGGER.debug("Button '%s' pressed", self._action)
        # if self._action == "start_charging":
        #     connector = self.api_client.connector_number
        #     entity_id = f"number.smappee_ev_wallbox_max_charging_speed_{connector}"
        #     state = self.hass.states.get(entity_id)
        #     try:
        #         current = int(state.state) if state else 6
        #     except (ValueError, TypeError):
        #         _LOGGER.warning("Invalid current value for connector %s, using fallback 6 A", connector)
        #         current = 6
        #     _LOGGER.debug("Calling start_charging with current: %s", current)
       try:
            if self._action == "start_charging":
                current = self.api_client.selected_current_limit or self.api_client.min_current
                await self.api_client.start_charging(current)
                #self.api_client.selected_mode = "NORMAL"

            elif self._action == "stop_charging":
                await self.api_client.stop_charging()
                #self.api_client.selected_mode = "NORMAL"

            elif self._action == "pause_charging":
                await self.api_client.pause_charging()
                self.api_client.selected_mode = "NORMAL"

            elif self._action == "set_available":
                await self.api_client.set_available()

            elif self._action == "set_unavailable":
                await self.api_client.set_unavailable()

            elif self._action == "set_brightness":
                brightness = getattr(self.api_client, "led_brightness", 70)
                await self.api_client.set_brightness(int(brightness))

            elif self._action == "set_charging_mode":
                mode = getattr(self.api_client, "selected_mode", "NORMAL")
                limit = self.api_client.selected_current_limit if mode == "NORMAL" else None
                await self.api_client.set_charging_mode(mode, limit)

        finally:
            await self._refresh()

            # connector = self.api_client.connector_number
            # mode_entity_id = f"select.smappee_ev_wallbox_charging_mode_{connector}"
            # current_entity_id = f"number.smappee_ev_wallbox_max_charging_speed_{connector}"

            # mode_state = self.hass.states.get(mode_entity_id)
            # current_state = self.hass.states.get(current_entity_id)

            # mode = mode_state.state if mode_state else "NORMAL"
            # try:
            #     current = int(current_state.state) if current_state else 6
            # except (ValueError, TypeError):
            #     current = 6

            # limit = current if mode == "NORMAL" else 0
            # _LOGGER.debug("Calling set_charging_mode(%s, limit=%s)", mode, limit)
            # await self.api_client.set_charging_mode(mode, limit)
