from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .oauth import OAuth2Client


def _required_with_optional_default(
    key: str, defaults: Mapping[str, Any]
) -> vol.Required:
    """Return a required field with a default only when one is available."""
    if key in defaults and defaults[key] is not None:
        return vol.Required(key, default=defaults[key])
    return vol.Required(key)


def _credentials_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Return the credentials form schema, optionally prefilled from entry data."""
    defaults = defaults or {}
    return vol.Schema(
        {
            _required_with_optional_default(CONF_CLIENT_ID, defaults): str,
            _required_with_optional_default(CONF_CLIENT_SECRET, defaults): str,
            _required_with_optional_default(CONF_USERNAME, defaults): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


def _auth_entry_data(
    user_input: dict[str, Any],
    tokens: dict[str, Any],
    token_expires_at: float | None,
) -> dict[str, Any]:
    """Return config-entry data for authenticated credentials."""
    data = {
        CONF_CLIENT_ID: user_input[CONF_CLIENT_ID],
        CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
        CONF_USERNAME: user_input[CONF_USERNAME],
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
    }
    if token_expires_at is not None:
        data["token_expires_at"] = token_expires_at

    # Once a refresh token is available, avoid persisting the account password.
    if not data["refresh_token"]:
        data[CONF_PASSWORD] = user_input[CONF_PASSWORD]

    return data


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

        oauth_client = OAuth2Client(user_input, session=session)
        tokens = await oauth_client.authenticate()
        if not tokens:
            errors["base"] = "auth_failed"
            return self.async_show_form(step_id=step_id, data_schema=data_schema, errors=errors)

        data = _auth_entry_data(user_input, tokens, oauth_client.token_expires_at)

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

        return self.async_create_entry(
            title=f"Smappee EV — {user_input[CONF_USERNAME]}", data=data
        )

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
