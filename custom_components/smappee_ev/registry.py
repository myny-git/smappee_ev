"""Registry cleanup helpers for the Smappee EV integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

_LOGGER = logging.getLogger(__name__)


@callback
def async_remove_config_entry_registry_entries(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[int, int]:
    """Remove entity and device registry entries owned by a config entry."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entity_count = 0
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        entity_registry.async_remove(entity_entry.entity_id)
        entity_count += 1

    device_count = 0
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device_entry.config_entries == {entry.entry_id}:
            device_registry.async_remove_device(device_entry.id)
        else:
            device_registry.async_update_device(
                device_entry.id, remove_config_entry_id=entry.entry_id
            )
        device_count += 1

    if entity_count or device_count:
        _LOGGER.info(
            "Removed %d entity registry entries and %d device registry entries for "
            "config entry %s before rediscovery",
            entity_count,
            device_count,
            entry.entry_id,
        )

    return entity_count, device_count
