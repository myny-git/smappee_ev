import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from .oauth import OAuth2Client  # Ensure this import works
from .const import DOMAIN  # Ensure const.py exists with DOMAIN defined

import logging

_LOGGER = logging.getLogger(__name__)

class smappee_evConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for smappee ev"""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("client_id"): str,
                    vol.Required("client_secret"): str,
                    vol.Required("username"): str,
                    vol.Required("password"): str
                })
            )

        # Authenticate with the API and get access and refresh tokens
        oauth_client = OAuth2Client(user_input)
        tokens = await oauth_client.authenticate()

        if not tokens:
            return self.async_show_form(step_id="user", errors={"base": "auth_failed"})

        user_input["access_token"] = tokens["access_token"]
        user_input["refresh_token"] = tokens["refresh_token"]

        return self.async_create_entry(title="Smappee EV", data=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return smappee_evFlowHandler(config_entry)


class smappee_evFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({
                    vol.Required("serial", default=self.config_entry.data.get("serial")): str,
                })
            )
        _LOGGER.debug("evFlowHandler_start...")
        self.async_create_entry(title="Smappee EV", data=user_input)
        _LOGGER.debug("evFlowHandler_start...done")
        _LOGGER.debug(f"{serial}")

        return True
