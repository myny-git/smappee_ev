import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from .oauth import OAuth2Client  # Ensure this import works
from .const import DOMAIN  # Ensure const.py exists with DOMAIN defined

class SmappeeChargingProfilesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for smappee charging profiles."""

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
                    vol.Required("password"): str,
                })
            )

        # Authenticate with the API and get access and refresh tokens
        oauth_client = OAuth2Client(user_input)
        tokens = await oauth_client.authenticate()

        if not tokens:
            return self.async_show_form(step_id="user", errors={"base": "auth_failed"})

        user_input["access_token"] = tokens["access_token"]
        user_input["refresh_token"] = tokens["refresh_token"]

        return self.async_create_entry(title="Smappee Charging Profiles", data=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmappeeChargingProfilesOptionsFlowHandler(config_entry)


class SmappeeChargingProfilesOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({
                    vol.Optional("serial", default=self.config_entry.data.get("serial")): str,
                })
            )

        return self.async_create_entry(title="", data=user_input)
