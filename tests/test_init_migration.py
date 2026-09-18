"""Test the Smappee EV integration init module."""

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smappee_ev import async_migrate_entry
from custom_components.smappee_ev.const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    CONF_NEEDS_DASHBOARD_REAUTH,
    CONF_PASSWORD,
    DOMAIN,
)


async def test_cable_lock_upgrade_is_scoped_and_preserves_later_opt_in(hass):
    """Disable legacy locks once, without changing other entities or entries."""
    entry = MockConfigEntry(domain=DOMAIN, version=6, data={})
    entry.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, version=6, data={})
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    cable = registry.async_get_or_create(
        "lock", DOMAIN, "123:serial:station:lock:cable_lock", config_entry=entry
    )
    user_disabled = registry.async_get_or_create(
        "lock",
        DOMAIN,
        "123:serial:second:lock:cable_lock",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    unrelated = registry.async_get_or_create(
        "switch", DOMAIN, "123:serial:station:available", config_entry=entry
    )
    other_lock = registry.async_get_or_create(
        "lock", DOMAIN, "456:serial:station:lock:cable_lock", config_entry=other
    )

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 7
    assert registry.async_get(cable.entity_id).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(user_disabled.entity_id).disabled_by == er.RegistryEntryDisabler.USER
    assert registry.async_get(unrelated.entity_id).disabled_by is None
    assert registry.async_get(other_lock.entity_id).disabled_by is None

    registry.async_update_entity(cable.entity_id, disabled_by=None)
    assert await async_migrate_entry(hass, entry)
    assert registry.async_get(cable.entity_id).disabled_by is None


class TestMigration:
    """Test config entry migration."""

    @pytest.mark.asyncio
    async def test_migrate_entry_v4_to_v7(self, hass):
        """Test migration from version 4 to version 7."""
        # Mock config entry with version 4 and update_interval in data and options
        entry = MagicMock(spec=ConfigEntry)
        entry.version = 4
        entry.data = {
            "username": "test_user",
            "password": "test_password",  # This is a test value, not a real credential
            "update_interval": 60,
        }
        entry.options = {
            "update_interval": 30,
        }
        entry.entry_id = "test_entry_id"

        # Set up HomeAssistant mock with a properly configured async method
        hass.config_entries = MagicMock()
        async_update_mock = MagicMock(return_value=None)
        hass.config_entries.async_update_entry = async_update_mock

        # Call migration
        result = await async_migrate_entry(hass, entry)

        # Verify migration succeeded
        assert result is True

        # Verify update_interval was removed from both data and options
        async_update_mock.assert_called_once()
        call_kwargs = async_update_mock.call_args[1]

        assert "update_interval" not in call_kwargs["data"]
        assert "update_interval" not in call_kwargs["options"]
        assert call_kwargs["version"] == 7
        assert CONF_NEEDS_DASHBOARD_REAUTH not in call_kwargs["data"]

        # Verify other data was preserved
        assert call_kwargs["data"]["username"] == "test_user"
        assert call_kwargs["data"]["password"] == "test_password"  # noqa: S105

    @pytest.mark.asyncio
    async def test_migrate_entry_v5_to_v7(self, hass):
        """Test migration from version 5 to version 7."""
        # Mock config entry at version 5
        entry = MagicMock(spec=ConfigEntry)
        entry.version = 5
        entry.data = {
            "username": "test_user",
            "password": "test_password",  # This is a test value, not a real credential
        }
        entry.options = {}
        entry.entry_id = "test_entry_id"

        # Set up HomeAssistant mock with a properly configured async method
        hass.config_entries = MagicMock()
        async_update_mock = MagicMock(return_value=None)
        hass.config_entries.async_update_entry = async_update_mock

        # Call migration
        result = await async_migrate_entry(hass, entry)

        # Verify migration succeeded
        assert result is True

        async_update_mock.assert_called_once()
        call_kwargs = async_update_mock.call_args[1]
        assert call_kwargs["version"] == 7
        assert CONF_NEEDS_DASHBOARD_REAUTH not in call_kwargs["data"]

    @pytest.mark.asyncio
    async def test_migrate_entry_preserves_password_when_refresh_token_exists(self, hass):
        """Test migration keeps password as a fallback for dashboard auth."""
        entry = MagicMock(spec=ConfigEntry)
        entry.version = 5
        entry.data = {
            "username": "test_user",
            CONF_PASSWORD: "test_password",  # This is a test value, not a real credential
            "refresh_token": "old_oauth_refresh",
            CONF_DASHBOARD_REFRESH_TOKEN: "test_dashboard_refresh_token",
        }
        entry.options = {}
        entry.entry_id = "test_entry_id"

        hass.config_entries = MagicMock()
        async_update_mock = MagicMock(return_value=None)
        hass.config_entries.async_update_entry = async_update_mock

        result = await async_migrate_entry(hass, entry)

        assert result is True
        async_update_mock.assert_called_once()
        call_kwargs = async_update_mock.call_args[1]
        assert call_kwargs["data"]["username"] == "test_user"
        assert call_kwargs["data"][CONF_DASHBOARD_REFRESH_TOKEN] == "test_dashboard_refresh_token"
        assert "refresh_token" not in call_kwargs["data"]
        assert call_kwargs["data"][CONF_PASSWORD] == "test_password"
        assert call_kwargs["version"] == 7
        assert CONF_NEEDS_DASHBOARD_REAUTH not in call_kwargs["data"]

    @pytest.mark.asyncio
    async def test_migrate_entry_v4_no_update_interval(self, hass):
        """Test migration from version 4 without update_interval."""
        # Mock config entry with version 4 but no update_interval
        entry = MagicMock(spec=ConfigEntry)
        entry.version = 4
        entry.data = {
            "username": "test_user",
            "password": "test_password",  # This is a test value, not a real credential
        }
        entry.options = {}
        entry.entry_id = "test_entry_id"

        # Set up HomeAssistant mock with a properly configured async method
        hass.config_entries = MagicMock()
        async_update_mock = MagicMock(return_value=None)
        hass.config_entries.async_update_entry = async_update_mock

        # Call migration
        result = await async_migrate_entry(hass, entry)

        # Verify migration succeeded
        assert result is True

        # Verify update was performed to bump version
        async_update_mock.assert_called_once()
        call_kwargs = async_update_mock.call_args[1]

        assert call_kwargs["version"] == 7
        assert CONF_NEEDS_DASHBOARD_REAUTH not in call_kwargs["data"]

        # Verify data was preserved
        assert call_kwargs["data"]["username"] == "test_user"
        assert call_kwargs["data"]["password"] == "test_password"  # noqa: S105

    @pytest.mark.asyncio
    async def test_migrate_entry_without_dashboard_credentials_marks_reauth(self, hass):
        """Test migration marks legacy entries without Dashboard credentials for reauth."""
        entry = MagicMock(spec=ConfigEntry)
        entry.version = 5
        entry.data = {
            "client_id": "old_client",
            "client_secret": "old_secret",
            "access_token": "old_access",
            "refresh_token": "old_refresh",
        }
        entry.options = {}
        entry.entry_id = "test_entry_id"

        hass.config_entries = MagicMock()
        async_update_mock = MagicMock(return_value=None)
        hass.config_entries.async_update_entry = async_update_mock

        result = await async_migrate_entry(hass, entry)

        assert result is True
        async_update_mock.assert_called_once()
        call_kwargs = async_update_mock.call_args[1]
        assert call_kwargs["version"] == 7
        assert call_kwargs["data"] == {CONF_NEEDS_DASHBOARD_REAUTH: True}
