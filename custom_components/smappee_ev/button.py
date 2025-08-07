from __future__ import annotations

import logging

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN
from .api_client import SmappeeApiClient

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV buttons from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    connector_clients: dict[str, SmappeeApiClient] = data["connectors"]
    station_client: SmappeeApiClient = data["station"]

    entities: list[ButtonEntity] = []

    # Connector-based buttons
    for client in connector_clients.values():
        connector = client.connector_number
        entities.extend([
            SmappeeActionButton(client, f"Start charging {connector}", "start_charging", f"start_{connector}"),
            SmappeeActionButton(client, f"Stop charging {connector}", "stop_charging", f"stop_{connector}"),
            SmappeeActionButton(client, f"Pause charging {connector}", "pause_charging", f"pause_{connector}"),
            SmappeeActionButton(client, f"Set charging mode {connector}", "set_charging_mode", f"mode_{connector}"),
        ])

    # Station-level buttons
    entities.extend([
        SmappeeActionButton(station_client, "Set LED brightness", "set_brightness", "set_brightness"),
        SmappeeActionButton(station_client, "Set available", "set_available", "set_available"),
        SmappeeActionButton(station_client, "Set unavailable", "set_unavailable", "set_unavailable"),
    ])

    async_add_entities(entities)


class SmappeeActionButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        api_client: SmappeeApiClient,
        name: str,
        action: str,
        unique_id_suffix: str,
    ) -> None:
        self.api_client = api_client
        self._attr_name = name
        self._attr_unique_id = f"{api_client.serial_id}_{unique_id_suffix}"
        self._action = action

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    async def async_press(self) -> None:
        _LOGGER.debug("Button '%s' pressed", self._action)
        if self._action == "start_charging":
            connector = self.api_client.connector_number
            entity_id = f"number.smappee_ev_wallbox_max_charging_speed_{connector}"
            state = self.hass.states.get(entity_id)
            try:
                current = int(state.state) if state else 6
            except (ValueError, TypeError):
                _LOGGER.warning("Invalid current value for connector %s, using fallback 6 A", connector)
                current = 6
            _LOGGER.debug("Calling start_charging with current: %s", current)
            await self.api_client.start_charging(current)
        
        elif self._action == "stop_charging":
            await self.api_client.stop_charging()
        
        elif self._action == "pause_charging":
            await self.api_client.pause_charging()
        
        elif self._action == "set_available":
            await self.api_client.set_available()
        
        elif self._action == "set_unavailable":
            await self.api_client.set_unavailable()
        
        elif self._action == "set_brightness":
            entity_id = f"number.smappee_ev_wallbox_led_brightness"
            state = self.hass.states.get(entity_id)
            try:
                brightness = int(state.state) if state else 70
            except (ValueError, TypeError):
                brightness = 70
            await self.api_client.set_brightness(brightness)
        
        elif self._action == "set_charging_mode":
            connector = self.api_client.connector_number
            mode_entity_id = f"select.smappee_ev_wallbox_charging_mode_{connector}"
            current_entity_id = f"number.smappee_ev_wallbox_max_charging_speed_{connector}"

            mode_state = self.hass.states.get(mode_entity_id)
            current_state = self.hass.states.get(current_entity_id)

            mode = mode_state.state if mode_state else "NORMAL"
            try:
                current = int(current_state.state) if current_state else 6
            except (ValueError, TypeError):
                current = 6

            limit = current if mode == "NORMAL" else 0
            _LOGGER.debug("Calling set_charging_mode(%s, limit=%s)", mode, limit)
            await self.api_client.set_charging_mode(mode, limit)
