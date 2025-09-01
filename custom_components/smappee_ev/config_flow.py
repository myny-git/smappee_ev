from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
    UPDATE_INTERVAL_DEFAULT,
)
from .oauth import OAuth2Client


class SmappeeEvConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smappee EV."""

    VERSION = 5
    reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step (and reauth)."""

        errors: dict[str, str] = {}
        session = async_get_clientsession(self.hass)

        # Initial form excludes update interval (moved to options)
        data_schema = vol.Schema({
            vol.Required(CONF_CLIENT_ID): str,
            vol.Required(CONF_CLIENT_SECRET): str,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        })

        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=data_schema)

        oauth_client = OAuth2Client(user_input, session=session)
        tokens = await oauth_client.authenticate()
        if not tokens:
            errors["base"] = "auth_failed"
            return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

        # persist tokens
        user_input = dict(user_input)
        user_input["access_token"] = tokens["access_token"]
        user_input["refresh_token"] = tokens.get("refresh_token")

        unique = f"smappee_ev:{user_input[CONF_USERNAME]}"
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()

        if self.reauth_entry:
            self.hass.config_entries.async_update_entry(self.reauth_entry, data=user_input)
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self.reauth_entry.entry_id)
            )
            return self.async_abort(reason="reauth_successful")

        return self.async_create_entry(
            title=f"Smappee EV — {user_input[CONF_USERNAME]}", data=user_input
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:  # type: ignore[override]
        """Begin re-authentication flow."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])  # type: ignore[index]
        return await self.async_step_user()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        return SmappeeEvOptionsFlow(config_entry)


class SmappeeEvOptionsFlow(config_entries.OptionsFlow):
    """Handle the options flow (update interval + credentials refresh)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = vol.Schema({
            vol.Required(CONF_CLIENT_ID, default=self.config_entry.data.get(CONF_CLIENT_ID)): str,
            vol.Required(
                CONF_CLIENT_SECRET, default=self.config_entry.data.get(CONF_CLIENT_SECRET)
            ): str,
            vol.Required(CONF_USERNAME, default=self.config_entry.data.get(CONF_USERNAME)): str,
            vol.Required(CONF_PASSWORD, default=self.config_entry.data.get(CONF_PASSWORD)): str,
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=self.config_entry.options.get(
                    CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_DEFAULT
                ),
            ): vol.All(int, vol.Range(min=5, max=3600)),
        })
        if user_input is None:
            return self.async_show_form(step_id="init", data_schema=schema)
        return self.async_create_entry(title="Smappee EV", data=user_input)
