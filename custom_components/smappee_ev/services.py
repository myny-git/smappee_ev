import logging
from typing import cast
from datetime import datetime


from homeassistant.const 
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import ServiceCall, callback, HomeAssistant
from homeassistant.helpers import device_registry
from .const import DOMAIN

SERVICE_LOCK = "lock"
SERVICE_UNLOCK = "unlock"

SUPPORTED_SERVICES = (
    SERVICE_LOCK,
    SERVICE_UNLOCK,
)

_LOGGER = logging.getLogger(__name__)


@callback
def async_setup_services(hass: HomeAssistant) -> bool:
    async def async_handle_lock(call):
        _LOGGER.debug(f"Call:{call.data}")
        #coordinator = _get_coordinator_from_device(hass, call)
        #vehicle_id = _get_vehicle_id_from_device(hass, call)
        #await coordinator.async_lock_vehicle(vehicle_id)

    async def async_handle_unlock(call):
        _LOGGER.debug(f"Call:{call.data}")
        #coordinator = _get_coordinator_from_device(hass, call)
        #vehicle_id = _get_vehicle_id_from_device(hass, call)
        #await coordinator.async_unlock_vehicle(vehicle_id)
  
    services = {
        SERVICE_LOCK: async_handle_lock,
        SERVICE_UNLOCK: async_handle_unlock,
    }

    for service in SUPPORTED_SERVICES:
        hass.services.async_register(DOMAIN, service, services[service])
    return True


@callback
def async_unload_services(hass) -> None:
    for service in SUPPORTED_SERVICES:
        hass.services.async_remove(DOMAIN, service)


def _get_vehicle_id_from_device(hass: HomeAssistant, call: ServiceCall) -> str:
    coordinators = list(hass.data[DOMAIN].keys())
    if len(coordinators) == 1:
        coordinator = hass.data[DOMAIN][coordinators[0]]
        vehicles = coordinator.vehicle_manager.vehicles
        if len(vehicles) == 1:
            return list(vehicles.keys())[0]

    device_entry = device_registry.async_get(hass).async_get(call.data[ATTR_DEVICE_ID])
    for entry in device_entry.identifiers:
        if entry[0] == DOMAIN:
            vehicle_id = entry[1]
    return vehicle_id


def _get_coordinator_from_device(
    hass: HomeAssistant, call: ServiceCall
) -> HyundaiKiaConnectDataUpdateCoordinator:
    coordinators = list(hass.data[DOMAIN].keys())
    if len(coordinators) == 1:
        return hass.data[DOMAIN][coordinators[0]]
    else:
        device_entry = device_registry.async_get(hass).async_get(
            call.data[ATTR_DEVICE_ID]
        )
        config_entry_ids = device_entry.config_entries
        config_entry_id = next(
            (
                config_entry_id
                for config_entry_id in config_entry_ids
                if cast(
                    ConfigEntry,
                    hass.config_entries.async_get_entry(config_entry_id),
                ).domain
                == DOMAIN
            ),
            None,
        )
        config_entry_unique_id = hass.config_entries.async_get_entry(
            config_entry_id
        ).unique_id
        return hass.data[DOMAIN][config_entry_unique_id]
