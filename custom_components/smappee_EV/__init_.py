"""The SmartKnob integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .coordinator import SmartknobCoordinator
from .logger import _LOGGER
from .mqtt import MqttHandler
from .panel import async_register_panel, async_unregister_panel
from .store import async_get_registry
from .websockets import async_register_websockets


async def async_setup(hass: HomeAssistant, config):
    """Set up the SmartKnob component."""
    _LOGGER.debug("SmartKnob - Setup")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up SmartKnob from a config entry."""

    _LOGGER.debug("SmartKnob - Setup Entry")
    _LOGGER.debug("SmartKnob - Entry: %s", entry)
    hass.data.setdefault(DOMAIN, {})
    session = async_get_clientsession(hass)

    store = await async_get_registry(hass)
    coordinator = SmartknobCoordinator(hass, session, entry, store)

    hass.data[DOMAIN]["coordinator"] = coordinator
    hass.data[DOMAIN]["apps"] = []

    mqtt_handler = MqttHandler(hass, entry)

    hass.data[DOMAIN]["mqtt_handler"] = mqtt_handler
    hass.data[DOMAIN]["entry"] = entry

    if entry.unique_id is None:
        hass.config_entries.async_update_entry(entry, unique_id=coordinator.id, data={})

    await async_register_panel(hass)

    # Register Websockets
    await async_register_websockets(hass)

    # Load Config Data
    await coordinator.store.async_load()
    await coordinator.update()

    return True


async def async_remove_entry(hass: HomeAssistant | None, entry):
    """Handle removal of an entry."""
    async_unregister_panel(hass)
    coordinator = hass.data[DOMAIN]["coordinator"]
    await coordinator.async_delete_config()
    del hass.data[DOMAIN]
