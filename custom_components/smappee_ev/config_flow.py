import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
import aiohttp

from .oauth import OAuth2Client  # Ensure this import works
from .const import (DOMAIN, CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_USERNAME, CONF_PASSWORD, CONF_SERIAL)

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
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_SERIAL): str
                })
            )

        # Authenticate with the API and get access and refresh tokens
        oauth_client = OAuth2Client(user_input)
        tokens = await oauth_client.authenticate()

        if not tokens:
            return self.async_show_form(step_id="user", errors={"base": "auth_failed"})

        user_input["access_token"] = tokens["access_token"]
        user_input["refresh_token"] = tokens["refresh_token"]
        
        # Retrieving the servicelocation_id
        try:
            headers = {
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession() as session:
                resp = await session.get(
                    "https://app1pub.smappee.net/dev/v3/servicelocation",
                    headers=headers,
                )
                if resp.status != 200:
                    _LOGGER.error("Failed to retrieve service locations: %s", await resp.text())
                    errors["base"] = "servicelocation_failed"
                    return self.async_show_form(
                        step_id="user",
                        data_schema=vol.Schema({
                            vol.Required(CONF_CLIENT_ID): str,
                            vol.Required(CONF_CLIENT_SECRET): str,
                            vol.Required(CONF_USERNAME): str,
                            vol.Required(CONF_PASSWORD): str,
                            vol.Required(CONF_SERIAL): str
                        }),
                        errors=errors
                    )
                data = await resp.json()
                locations = data.get("serviceLocations", [])
                if not locations:
                    errors["base"] = "no_locations"
                    return self.async_show_form(
                        step_id="user",
                        data_schema=vol.Schema({
                            vol.Required(CONF_CLIENT_ID): str,
                            vol.Required(CONF_CLIENT_SECRET): str,
                            vol.Required(CONF_USERNAME): str,
                            vol.Required(CONF_PASSWORD): str,
                            vol.Required(CONF_SERIAL): str
                        }),
                        errors=errors
                    )
                service_location_id = locations[0].get("serviceLocationId")
                user_input["service_location_id"] = service_location_id
        except Exception as e:
            _LOGGER.error(f"Exception while retrieving service_location_id: {e}")
            erors = {}
            errors["base"] = "servicelocation_failed"
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_SERIAL): str
                }),
                errors=errors
            )

        # Retrieving the UUID
        try:
            url = f"https://app1pub.smappee.net/dev/v3/servicelocation/{service_location_id}/metering"
            async with aiohttp.ClientSession() as session:
                resp = await session.get(url, headers=headers)
                if resp.status != 200:
                    _LOGGER.error("Failed to retrieve metering info: %s", await resp.text())
                    errors["base"] = "uuid_failed"
                    return self.async_show_form(
                        step_id="user",
                        data_schema=vol.Schema({
                            vol.Required(CONF_CLIENT_ID): str,
                            vol.Required(CONF_CLIENT_SECRET): str,
                            vol.Required(CONF_USERNAME): str,
                            vol.Required(CONF_PASSWORD): str,
                            vol.Required(CONF_SERIAL): str
                        }),
                        errors=errors
                    )
                data = await resp.json()
                # Controleer of er chargingStations en chargers zijn
                charging_stations = data.get("chargingStations", [])
                if not charging_stations or not charging_stations[0].get("chargers"):
                    errors["base"] = "no_chargers"
                    return self.async_show_form(
                        step_id="user",
                        data_schema=vol.Schema({
                            vol.Required(CONF_CLIENT_ID): str,
                            vol.Required(CONF_CLIENT_SECRET): str,
                            vol.Required(CONF_USERNAME): str,
                            vol.Required(CONF_PASSWORD): str,
                            vol.Required(CONF_SERIAL): str
                        }),
                        errors=errors
                    )
                smart_device_uuid = charging_stations[0]["chargers"][0].get("uuid")
                user_input["smart_device_uuid"] = smart_device_uuid
        except Exception as e:
            _LOGGER.error(f"Exception while retrieving smart_device_uuid: {e}")
            erors = {}
            errors["base"] = "uuid_failed"
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_SERIAL): str
                }),
                errors=errors
            )

        return self.async_create_entry(title="Smappee EV", data=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return smappee_evFlowHandler(config_entry)


class smappee_evFlowHandler(config_entries.OptionsFlow):
    """Handle the options flow."""

    def __init__(self, config_entry):
        _LOGGER.debug('evFlowHandler...empty')
        #self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({
                    vol.Required("client_id", default=self.config_entry.data.get(CONF_CLIENT_ID)): str,
                    vol.Required("client_secret", default=self.config_entry.data.get(CONF_CLIENT_SECRET)): str,
                    vol.Required("username", default=self.config_entry.data.get(CONF_USERNAME)): str,
                    vol.Required("password", default=self.config_entry.data.get(CONF_PASSWORD)): str,
                    vol.Required("serial", default=self.config_entry.data.get(CONF_SERIAL)): str,
                })
            )
        _LOGGER.debug("Serial: ")
        _LOGGER.debug(user_input.get(CONF_SERIAL))
        #self.config_entry.data[CONF_SERIAL] = user_input.get(CONF_SERIAL)
        
        return self.async_create_entry(title="Smappee EV", data=user_input)
