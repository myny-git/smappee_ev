import logging

from typing import Any
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV buttons from a config entry."""
    api_client = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([
        SmappeeSetChargingModeButton(api_client, hass),
        SmappeePauseChargingButton(api_client, hass),
        SmappeeStopChargingButton(api_client, hass),
        SmappeeStartChargingButton(api_client, hass),
        SmappeeSetBrightnessButton(api_client, hass),
        SmappeeSetAvailableButton(api_client, hass),
        SmappeeSetUnavailableButton(api_client, hass),
    ])

class SmappeeBaseButton(ButtonEntity):
    """Base button for Smappee EV actions."""

    _attr_has_entity_name = True

    def __init__(self, api_client: Any, hass: HomeAssistant, name: str, unique_id: str, icon: str = None) -> None:
        self.api_client = api_client
        self.hass = hass
        self._attr_name = name
        self._attr_unique_id = unique_id
        if icon:
            self._attr_icon = icon

    @property
    def device_info(self):
        """Return device info for the wallbox."""
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

class SmappeeSetChargingModeButton(SmappeeBaseButton):
    def __init__(self, api_client: Any, hass: HomeAssistant):
        super().__init__(
            api_client, hass,
            "Set Charging Mode",
            f"{api_client.serial_id}_set_charging_mode"
        )

    async def async_press(self) -> None:
        """Set charging mode based on current select/numbers in HA."""
        serial = self.api_client.serial_id
        mode_entity_id = f"select.smappee_ev_wallbox_smappee_charging_mode_{serial}"
        current_entity_id = f"number.smappee_ev_wallbox_smappee_current_limit_{serial}"
        percent_entity_id = f"number.smappee_ev_wallbox_smappee_percentage_limit_{serial}"

        mode_state = self.hass.states.get(mode_entity_id)
        current_state = self.hass.states.get(current_entity_id)
        percent_state = self.hass.states.get(percent_entity_id)

        if mode_state is None:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": f"Kan mode entity '{mode_entity_id}' niet vinden.",
                    "title": "Smappee Button"
                },
                blocking=True,
            )
            return

        mode = mode_state.state
        current = None
        percent = None
        if current_state is not None:
            try:
                current = int(current_state.state)
            except (ValueError, TypeError):
                pass
        if percent_state is not None:
            try:
                percent = int(percent_state.state)
            except (ValueError, TypeError):
                pass

        if mode == "NORMAL":
            limit = current if current is not None else 6
        elif mode == "NORMAL_PERCENTAGE":
            limit = percent if percent is not None else 10
        else:
            limit = 0

        await self.hass.services.async_call(
            domain=DOMAIN,
            service="set_charging_mode",
            service_data={"serial": serial, "mode": mode, "limit": limit},
            blocking=True,
        )

class SmappeePauseChargingButton(SmappeeBaseButton):
    def __init__(self, api_client: Any, hass: HomeAssistant):
        super().__init__(
            api_client, hass,
            "Pause Charging",
            f"{api_client.serial_id}_pause_charging",
            icon="mdi:pause"
        )

    async def async_press(self) -> None:
        await self.hass.services.async_call(
            domain=DOMAIN,
            service="pause_charging",
            blocking=True,
        )

class SmappeeStopChargingButton(SmappeeBaseButton):
    def __init__(self, api_client: Any, hass: HomeAssistant):
        super().__init__(
            api_client, hass,
            "Stop Charging",
            f"{api_client.serial_id}_stop_charging",
            icon="mdi:stop"
        )

    async def async_press(self) -> None:
        serial = self.api_client.serial_id
        percent_entity_id = f"number.smappee_ev_wallbox_smappee_percentage_limit_{serial}"
        percent_state = self.hass.states.get(percent_entity_id)

        await self.hass.services.async_call(
            domain=DOMAIN,
            service="stop_charging",
            blocking=True,
        )

class SmappeeStartChargingButton(SmappeeBaseButton):
    def __init__(self, api_client: Any, hass: HomeAssistant):
        super().__init__(
            api_client, hass,
            "Start Charging",
            f"{api_client.serial_id}_start_charging",
            icon="mdi:play"
        )

    async def async_press(self) -> None:
        serial = self.api_client.serial_id
        percent_entity_id = f"number.smappee_ev_wallbox_smappee_percentage_limit_{serial}"
        percent_state = self.hass.states.get(percent_entity_id)

        try:
            percentage = int(percent_state.state) if percent_state else 100
        except (ValueError, TypeError):
            percentage = 100

        await self.hass.services.async_call(
            domain=DOMAIN,
            service="start_charging",
            service_data={"percentage": percentage},
            blocking=True,
        )

class SmappeeSetBrightnessButton(SmappeeBaseButton):
    def __init__(self, api_client: Any, hass: HomeAssistant):
        super().__init__(
            api_client, hass,
            "Set LED Brightness",
            f"{api_client.serial_id}_set_led_brightness",
            icon="mdi:brightness-6"
        )

    async def async_press(self) -> None:
        serial = self.api_client.serial_id
        entity_id = f"number.smappee_ev_wallbox_smappee_led_brightness_{serial}"
        state = self.hass.states.get(entity_id)

        try:
            brightness = int(state.state) if state else 70
        except (ValueError, TypeError):
            brightness = 70

 #       await self.api_client.set_brightness(brightness)
        await self.hass.services.async_call(
            domain="smappee_ev",
            service="set_brightness",
            service_data={"brightness": brightness},
            blocking=True,
        )

class SmappeeSetAvailableButton(SmappeeBaseButton):
    def __init__(self, api_client: Any, hass: HomeAssistant):
        super().__init__(
            api_client, hass,
            "Set Available",
            f"{api_client.serial_id}_set_available"
        )

    async def async_press(self) -> None:
        await self.hass.services.async_call(
            domain=DOMAIN,
            service="set_available",
            blocking=True,
        )

class SmappeeSetUnavailableButton(SmappeeBaseButton):
    def __init__(self, api_client: Any, hass: HomeAssistant):
        super().__init__(
            api_client, hass,
            "Set Unavailable",
            f"{api_client.serial_id}_set_unavailable"
        )

    async def async_press(self) -> None:
        await self.hass.services.async_call(
            domain=DOMAIN,
            service="set_unavailable",
            blocking=True,
        )
