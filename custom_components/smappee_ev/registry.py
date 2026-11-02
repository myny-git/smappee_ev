"""Registry cleanup helpers for the Smappee EV integration."""

from __future__ import annotations

import logging
import re

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN
from .models.runtime_data import SmappeeEvConfigEntry

_LOGGER = logging.getLogger(__name__)


def _device_owned_only_by_entry(device: dr.DeviceEntry, entry_id: str) -> bool:
    """Use modern ownership without touching deprecated compatibility properties."""
    if hasattr(device, "config_entry_id"):
        return device.config_entry_id == entry_id
    return device.config_entries == {entry_id}


@callback
def async_remove_config_entry_registry_entries(
    hass: HomeAssistant, entry: SmappeeEvConfigEntry
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
        if _device_owned_only_by_entry(device_entry, entry.entry_id):
            device_registry.async_remove_device(device_entry.id)
        elif not hasattr(device_entry, "config_entry_id"):
            device_registry.async_update_device(
                device_entry.id, remove_config_entry_id=entry.entry_id
            )
        else:
            continue
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


@callback
def async_migrate_status_registry(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> None:
    """Preserve registered entity IDs and user metadata when scoping status by account."""
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.platform != DOMAIN or not entity.unique_id.startswith("smappee_service_status:"):
            continue
        unique_id = f"{entry.entry_id}:{entity.unique_id}"
        if entity_registry.async_get_entity_id(entity.domain, DOMAIN, unique_id) is None:
            entity_registry.async_update_entity(entity.entity_id, new_unique_id=unique_id)

    device_registry = dr.async_get(hass)
    old_identifier = (DOMAIN, "smappee_service_status")
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if old_identifier in device.identifiers and _device_owned_only_by_entry(
            device, entry.entry_id
        ):
            identifiers = set(device.identifiers) - {old_identifier}
            identifiers.add((DOMAIN, f"{entry.entry_id}:smappee_service_status"))
            device_registry.async_update_device(device.id, new_identifiers=identifiers)


_SITE_SETTING_ID = re.compile(
    r"^(?P<sid>[0-9]+):.+:site-(?P=sid):number:"
    r"(?P<metric>capacity_maximum_power|overload_maximum_load)$"
)


def site_setting_unique_id(sid: int, metric: str) -> str:
    """Identify site settings independently of gateway and charging station."""
    return f"{sid}:site-{sid}:number:{metric}"


@callback
def async_migrate_site_setting_ids(hass: HomeAssistant, entry: SmappeeEvConfigEntry) -> None:
    """Update unique IDs in place, retaining entity IDs and user customizations."""
    registry = er.async_get(hass)
    entries = sorted(
        er.async_entries_for_config_entry(registry, entry.entry_id),
        key=lambda item: (item.disabled_by is not None, item.entity_id),
    )
    for entity in entries:
        if entity.domain != "number" or entity.platform != DOMAIN:
            continue
        match = _SITE_SETTING_ID.fullmatch(entity.unique_id)
        if match is None:
            continue
        new_id = site_setting_unique_id(int(match["sid"]), match["metric"])
        if registry.async_get_entity_id("number", DOMAIN, new_id) is not None:
            # Existing duplicates may have separate automations referring to them.
            # Retain them rather than deleting user registry entries implicitly.
            _LOGGER.warning(
                "Site setting %s already has a canonical entity; retaining %s",
                match["metric"],
                entity.entity_id,
            )
            continue
        registry.async_update_entity(entity.entity_id, new_unique_id=new_id)
