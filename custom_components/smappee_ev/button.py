from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    api_client = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([
        SmappeeSetChargingModeButton(api_client, hass),
        SmappeePauseChargingButton(api_client, hass),
        SmappeeStopChargingButton(api_client, hass),
        SmappeeStartChargingButton(api_client, hass),
        SmappeeSetBrightnessButton(api_client, hass),
        SmappeeSetAvailableButton(api_client, hass),
        SmappeeSetUnavailableButton(api_client, hass)        
    ])

class SmappeeSetChargingModeButton(ButtonEntity):
    def __init__(self, api_client, hass):
        self.api_client = api_client
        self.hass = hass
        self._attr_name = "Set Charging Mode"
        self._attr_unique_id = f"{api_client.serial_id}_set_charging_mode"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }    

    async def async_press(self) -> None:
        serial = self.api_client.serial_id
        mode_entity_id = f"select.smappee_charging_mode_{serial}"
        current_entity_id = f"number.smappee_current_limit_{serial}"
        percent_entity_id = f"number.smappee_percentage_limit_{serial}"

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
                current = float(current_state.state)
            except (ValueError, TypeError):
                current = None
        if percent_state is not None:
            try:
                percent = float(percent_state.state)
            except (ValueError, TypeError):
                percent = None

        if mode == "NORMAL":
            limit = current if current is not None else 6
        elif mode == "NORMAL_PERCENTAGE":
            limit = percent if percent is not None else 10
        else:
            limit = 0

        await self.api_client.set_charging_mode(mode, limit)


class SmappeePauseChargingButton(ButtonEntity):
    def __init__(self, api_client, hass):
        self.api_client = api_client
        self.hass = hass
        self._attr_name = "Pause Charging"
        self._attr_unique_id = f"{api_client.serial_id}_pause_charging"
        self._attr_icon = "mdi:pause"

 
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    async def async_press(self) -> None:
        serial = self.api_client.serial_id
        await self.api_client.pause_charging()

class SmappeeStopChargingButton(ButtonEntity):
    def __init__(self, api_client, hass):
        self.api_client = api_client
        self.hass = hass
        self._attr_name = "Stop Charging"
        self._attr_unique_id = f"{api_client.serial_id}_stop_charging"
        self._attr_icon = "mdi:stop"

 
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    async def async_press(self) -> None:
        serial = self.api_client.serial_id
        await self.api_client.stop_charging()

class SmappeeStartChargingButton(ButtonEntity):
    def __init__(self, api_client, hass):
        self.api_client = api_client
        self.hass = hass
        self._attr_name = "Start Charging"
        self._attr_unique_id = f"{api_client.serial_id}_start_charging"
        self._attr_icon = "mdi:play"

 
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }

    async def async_press(self) -> None:
        serial = self.api_client.serial_id
        percent_entity_id = f"number.smappee_percentage_limit_{serial}"
        percent_state = self.hass.states.get(percent_entity_id)   
               
        try:
            percentage = int(float(percent_state.state)) if percent_state else 100
        except (ValueError, TypeError):
            percentage = 100

        
        await self.api_client.start_charging(percentage)

class SmappeeSetBrightnessButton(ButtonEntity):
    def __init__(self, api_client, hass):
        self.api_client = api_client
        self.hass = hass
        self._attr_name = "Set LED Brightness"
        self._attr_unique_id = f"{api_client.serial_id}_set_led_brightness"
        self._attr_icon = "mdi:brightness-6"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }
    
    async def async_press(self) -> None:
        entity_id = f"number.smappee_led_brightness_{self.api_client.serial_id}"
        state = self.hass.states.get(entity_id)
        try:
            brightness = int(float(state.state)) if state else 70
        except (ValueError, TypeError):
            brightness = 70
        await self.api_client.set_brightness(brightness)


class SmappeeSetAvailableButton(ButtonEntity):
    def __init__(self, api_client, hass):
        self.api_client = api_client
        self.hass = hass
        self._attr_name = "Set Available"
        self._attr_unique_id = f"{api_client.serial_id}_set_available"
        
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }
        
    async def async_press(self) -> None:
        entity_id = f"number.smappee_led_brightness_{self.api_client.serial_id}"
        state = self.hass.states.get(entity_id)

        await self.api_client.set_available()

class SmappeeSetUnavailableButton(ButtonEntity):
    def __init__(self, api_client, hass):
        self.api_client = api_client
        self.hass = hass
        self._attr_name = "Set Unavailable"
        self._attr_unique_id = f"{api_client.serial_id}_set_unavailable"
        
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.api_client.serial_id)},
            "name": "Smappee EV Wallbox",
            "manufacturer": "Smappee",
        }
        
    async def async_press(self) -> None:
        await self.api_client.set_unavailable()


