# custom_components/smappee_ev/services.py

import asyncio
import logging

from homeassistant.core import HomeAssistant, ServiceCall
from .api_client import SmappeeApiClient
from .const import (
    DOMAIN,
    SERVICE_SET_CHARGING_MODE,
    SERVICE_PAUSE_CHARGING,
    SERVICE_STOP_CHARGING,
    SERVICE_START_CHARGING,
    SERVICE_SET_BRIGHTNESS,
    SERVICE_SET_AVAILABLE,
    SERVICE_SET_UNAVAILABLE,
    SERVICE_RELOAD,
)

_LOGGER = logging.getLogger(__name__)


def get_api_client(hass: HomeAssistant, entry_id: str | None = None) -> SmappeeApiClient | None:
    """Return the api_client for the entry, or the first if not specified."""
    data = hass.data.get(DOMAIN, {})
    if entry_id:
        return data.get(entry_id)
    return next(iter(data.values()), None)


def register_services(hass: HomeAssistant) -> None:
    """Register all Smappee EV services."""

    async def async_set_charging_mode(call: ServiceCall):
        api = get_api_client(hass)
        if not api:
            _LOGGER.error("No API client found for set_charging_mode")
            return
        mode = call.data.get("mode")
        limit = call.data.get("limit", 0)
        _LOGGER.info("Service: set_charging_mode (mode=%s, limit=%s)", mode, limit)
        await api.set_charging_mode(mode, limit)

    async def async_pause_charging(call: ServiceCall):
        api = get_api_client(hass)
        if api:
            _LOGGER.info("Service: pause_charging")
            await api.pause_charging()

    async def async_stop_charging(call: ServiceCall):
        api = get_api_client(hass)
        if api:
            _LOGGER.info("Service: stop_charging")
            await api.stop_charging()

    async def async_start_charging(call: ServiceCall):
        api = get_api_client(hass)
        if not api:
            _LOGGER.error("No API client found for start_charging")
            return
            
        current = call.data.get("current")
        if current is None:
            _LOGGER.error("Missing required field: current")
            return

        _LOGGER.info("Service: start_charging (current=%s A)", current)
        await api.start_charging_current(current)            

        # percentage = call.data.get("percentage", 100)
        # _LOGGER.info("Service: start_charging (percentage=%s)", percentage)
        # await api.start_charging(percentage)

    async def async_set_brightness(call: ServiceCall):
        api = get_api_client(hass)
        if not api:
            _LOGGER.error("No API client found for set_brightness")
            return

        brightness = call.data.get("brightness", 10)

        api.led_brightness = brightness
        await api.publish_updates()
        _LOGGER.info("Service: set_brightness (brightness=%s)", brightness)
        await api.set_brightness(brightness)

    async def async_set_available(call: ServiceCall):
        api = get_api_client(hass)
        if api:
            _LOGGER.info("Service: set_available")
            await api.set_available()

    async def async_set_unavailable(call: ServiceCall):
        api = get_api_client(hass)
        if api:
            _LOGGER.info("Service: set_unavailable")
            await api.set_unavailable()

    async def async_reload(call: ServiceCall):
        _LOGGER.info("Service: reload – reloading all Smappee EV entries")
        entries = hass.config_entries.async_entries(DOMAIN)
        await asyncio.gather(*(hass.config_entries.async_reload(e.entry_id) for e in entries))

    hass.services.async_register(DOMAIN, SERVICE_SET_CHARGING_MODE, async_set_charging_mode)
    hass.services.async_register(DOMAIN, SERVICE_PAUSE_CHARGING, async_pause_charging)
    hass.services.async_register(DOMAIN, SERVICE_STOP_CHARGING, async_stop_charging)
    hass.services.async_register(DOMAIN, SERVICE_START_CHARGING, async_start_charging)
    hass.services.async_register(DOMAIN, SERVICE_SET_BRIGHTNESS, async_set_brightness)
    hass.services.async_register(DOMAIN, SERVICE_SET_AVAILABLE, async_set_available)
    hass.services.async_register(DOMAIN, SERVICE_SET_UNAVAILABLE, async_set_unavailable)
    hass.services.async_register(DOMAIN, SERVICE_RELOAD, async_reload)

    _LOGGER.debug("All Smappee EV services registered.")


def unregister_services(hass: HomeAssistant) -> None:
    """Unregister all Smappee EV services."""
    for service in [
        SERVICE_SET_CHARGING_MODE,
        SERVICE_PAUSE_CHARGING,
        SERVICE_STOP_CHARGING,
        SERVICE_START_CHARGING,
        SERVICE_SET_BRIGHTNESS,
        SERVICE_SET_AVAILABLE,
        SERVICE_SET_UNAVAILABLE,
        SERVICE_RELOAD,
    ]:
        hass.services.async_remove(DOMAIN, service)
    _LOGGER.debug("All Smappee EV services unregistered.")
