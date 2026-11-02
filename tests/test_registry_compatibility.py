"""Status identity migration and cleanup across Home Assistant registry versions."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.registry import (
    async_migrate_status_registry,
    async_remove_config_entry_registry_entries,
)


async def test_status_migration_preserves_entity_and_device_customizations(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    registry = er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "smappee_service_status")}
    )
    devices.async_update_device(device.id, name_by_user="My service status")
    entities = []
    for platform, suffix in (("sensor", "active_incidents"), ("binary_sensor", "api")):
        entity = registry.async_get_or_create(
            platform,
            DOMAIN,
            f"smappee_service_status:{suffix}",
            config_entry=entry,
            device_id=device.id,
        )
        registry.async_update_entity(
            entity.entity_id, name="My status", disabled_by=er.RegistryEntryDisabler.USER
        )
        entities.append(entity)
    for _ in range(2):
        async_migrate_status_registry(hass, entry)
    for entity in entities:
        migrated = registry.async_get(entity.entity_id)
        assert migrated.unique_id == f"{entry.entry_id}:{entity.unique_id}"
        assert migrated.name == "My status"
        assert migrated.disabled_by is er.RegistryEntryDisabler.USER
        assert migrated.device_id == device.id
    migrated_device = devices.async_get(device.id)
    assert migrated_device.name_by_user == "My service status"
    assert migrated_device.identifiers == {(DOMAIN, f"{entry.entry_id}:smappee_service_status")}


@pytest.mark.parametrize("owner", ["account", "another-account", None])
def test_modern_cleanup_does_not_read_deprecated_properties(owner):
    class ModernDevice:
        id = "device"
        config_entry_id = owner

        @property
        def config_entries(self):
            raise AssertionError("Deprecated property accessed")

    device_registry = MagicMock()
    with (
        patch("custom_components.smappee_ev.registry.er.async_get"),
        patch(
            "custom_components.smappee_ev.registry.er.async_entries_for_config_entry",
            return_value=[],
        ),
        patch("custom_components.smappee_ev.registry.dr.async_get", return_value=device_registry),
        patch(
            "custom_components.smappee_ev.registry.dr.async_entries_for_config_entry",
            return_value=[ModernDevice()],
        ),
    ):
        async_remove_config_entry_registry_entries(MagicMock(), SimpleNamespace(entry_id="account"))
    if owner == "account":
        device_registry.async_remove_device.assert_called_once_with("device")
    else:
        device_registry.async_remove_device.assert_not_called()
    device_registry.async_update_device.assert_not_called()


@pytest.mark.parametrize("shared", [False, True])
def test_legacy_cleanup_preserves_other_accounts(shared):
    registry = MagicMock()
    device = SimpleNamespace(
        id="old-device", config_entries={"account", "other"} if shared else {"account"}
    )
    with (
        patch("custom_components.smappee_ev.registry.er.async_get"),
        patch(
            "custom_components.smappee_ev.registry.er.async_entries_for_config_entry",
            return_value=[],
        ),
        patch("custom_components.smappee_ev.registry.dr.async_get", return_value=registry),
        patch(
            "custom_components.smappee_ev.registry.dr.async_entries_for_config_entry",
            return_value=[device],
        ),
    ):
        async_remove_config_entry_registry_entries(MagicMock(), SimpleNamespace(entry_id="account"))
    if shared:
        registry.async_update_device.assert_called_once_with(
            "old-device", remove_config_entry_id="account"
        )
        registry.async_remove_device.assert_not_called()
    else:
        registry.async_remove_device.assert_called_once_with("old-device")
        registry.async_update_device.assert_not_called()
