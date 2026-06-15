from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .const import CONF_DASHBOARD_REFRESH_TOKEN, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .dashboard_client import SmappeeDashboardClient

_LOGGER = logging.getLogger(__name__)


def _required_with_optional_default(key: str, defaults: Mapping[str, Any]) -> vol.Required:
    """Return a required field with a default only when one is available."""
    if key in defaults and defaults[key] is not None:
        return vol.Required(key, default=defaults[key])
    return vol.Required(key)


def _credentials_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Return the credentials form schema, optionally prefilled from entry data."""
    defaults = defaults or {}
    return vol.Schema(
        {
            _required_with_optional_default(CONF_USERNAME, defaults): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


async def _async_dashboard_auth_data(
    user_input: dict[str, Any], session: Any
) -> dict[str, Any] | None:
    """Authenticate against Dashboard and return config-entry data."""
    dashboard_tokens: dict[str, object] = {}
    dashboard_client = SmappeeDashboardClient(
        username=user_input.get(CONF_USERNAME),
        password=user_input.get(CONF_PASSWORD),
        refresh_token=None,
        session=session,
        token_update_callback=dashboard_tokens.update,
    )

    try:
        if await dashboard_client.async_login():
            refresh_token = dashboard_tokens.get(CONF_DASHBOARD_REFRESH_TOKEN)
            if refresh_token:
                return {
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_DASHBOARD_REFRESH_TOKEN: refresh_token,
                }
    except ConfigEntryAuthFailed as err:
        _LOGGER.debug("Dashboard authentication rejected during setup: %s", err)
    except (aiohttp.ClientError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
        _LOGGER.debug("Dashboard authentication unavailable during setup: %s", err)
    return None


class SmappeeEvConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smappee EV."""

    VERSION = 5

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial setup step."""
        return await self._async_step_credentials(user_input, "user")

    async def _async_step_credentials(
        self,
        user_input: dict[str, Any] | None,
        step_id: str,
        defaults: Mapping[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle credential validation for setup, reauth and reconfigure."""
        errors: dict[str, str] = {}
        session = async_get_clientsession(self.hass)
        data_schema = _credentials_schema(defaults)

        if user_input is None:
            return self.async_show_form(step_id=step_id, data_schema=data_schema)

        try:
            data = await _async_dashboard_auth_data(user_input, session)
        except Exception:
            _LOGGER.exception("Unexpected error during authentication")
            errors["base"] = "cannot_connect"
            return self.async_show_form(step_id=step_id, data_schema=data_schema, errors=errors)
        if data is None:
            errors["base"] = "auth_failed"
            return self.async_show_form(step_id=step_id, data_schema=data_schema, errors=errors)

        unique = f"smappee_ev:{user_input[CONF_USERNAME]}"
        await self.async_set_unique_id(unique)

        if self.source == config_entries.SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if entry.unique_id:
                self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(entry, unique_id=unique, data=data)

        if self.source == config_entries.SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            if entry.unique_id:
                self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                entry, unique_id=unique, data=data, options={}
            )

        self._abort_if_unique_id_configured()

        return self.async_create_entry(title=f"Smappee EV — {user_input[CONF_USERNAME]}", data=data)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:  # type: ignore[override]
        """Begin re-authentication flow."""
        self.context["source"] = config_entries.SOURCE_REAUTH
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm new credentials for reauthentication."""
        return await self._async_step_credentials(
            user_input, "reauth_confirm", self._get_reauth_entry().data
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of existing credentials."""
        self.context["source"] = config_entries.SOURCE_RECONFIGURE
        return await self._async_step_credentials(
            user_input, "reconfigure", self._get_reconfigure_entry().data
        )
