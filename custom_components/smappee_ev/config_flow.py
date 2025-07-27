import voluptuous as vol
import homeassistant.helpers.config_validation as cv
import aiohttp
import logging

from homeassistant import config_entries
from homeassistant.core import callback
from .oauth import OAuth2Client
from .const import (
    DOMAIN,
    BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_SERIAL,
    CONF_SERVICE_LOCATION_ID,
    CONF_SMART_DEVICE_UUID,
    CONF_SMART_DEVICE_ID,
    CONF_UPDATE_INTERVAL,
    UPDATE_INTERVAL_DEFAULT,
)

_LOGGER = logging.getLogger(__name__)

class SmappeeEvConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smappee EV."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_SERIAL): str,
                    vol.Optional(CONF_UPDATE_INTERVAL, default=UPDATE_INTERVAL_DEFAULT): vol.All(int, vol.Range(min=5, max=3600)),
                })
            )

        # Authenticate with the API and get access and refresh tokens
        oauth_client = OAuth2Client(user_input)
        tokens = await oauth_client.authenticate()

        if not tokens:
            errors["base"] = "auth_failed"
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required(CONF_CLIENT_ID): str,
                    vol.Required(CONF_CLIENT_SECRET): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_SERIAL): str,
                    vol.Optional(CONF_UPDATE_INTERVAL, default=UPDATE_INTERVAL_DEFAULT): vol.All(int, vol.Range(min=5, max=3600)),
                }),
                errors=errors
            )

        user_input["access_token"] = tokens["access_token"]
        user_input["refresh_token"] = tokens["refresh_token"]
        
         # Retrieve the service_location_id
        try:
            headers = {
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession() as session:
                resp = await session.get(
                    f"{BASE_URL}/servicelocation",
                    headers=headers,
                )
                if resp.status != 200:
                    _LOGGER.error("Failed to retrieve service locations: %s", await resp.text())
                    errors["base"] = "servicelocation_failed"
                    return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
                data = await resp.json()
                locations = data.get("serviceLocations", [])
                if not locations:
                    errors["base"] = "no_locations"
                    return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
                service_location_id = locations[0].get("serviceLocationId")
                user_input[CONF_SERVICE_LOCATION_ID] = service_location_id
        except Exception as e:
            _LOGGER.error(f"Exception while retrieving service_location_id: {e}")
            errors["base"] = "servicelocation_failed"
            return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

        # Retrieve smart_device_uuid and smart_device_id
        try:
            url = f"{BASE_URL}/servicelocation/{service_location_id}/smartdevices"
            async with aiohttp.ClientSession() as session:
                resp = await session.get(url, headers=headers)
                if resp.status != 200:
                    _LOGGER.error("Failed to retrieve smart devices: %s", await resp.text())
                    errors["base"] = "uuid_failed"
                    return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
                smart_devices = await resp.json()
                if not smart_devices:
                    errors["base"] = "no_chargers"
                    return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
                _LOGGER.debug(f"Smart devices data: {smart_devices}")
                smart_device_id = smart_devices[0].get("id")
                smart_device_uuid = smart_devices[0].get("uuid")
                user_input[CONF_SMART_DEVICE_UUID] = smart_device_uuid
                user_input[CONF_SMART_DEVICE_ID] = smart_device_id
                _LOGGER.debug(f"UUID: {smart_device_uuid}")
                _LOGGER.debug(f"ID: {smart_device_id}")
        except Exception as e:
            _LOGGER.error(f"Exception while retrieving smart_device_uuid: {e}")
            errors["base"] = "uuid_failed"
            return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

        return self.async_create_entry(title="Smappee EV", data=user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmappeeEvOptionsFlow(config_entry)


class SmappeeEvOptionsFlow(config_entries.OptionsFlow):
    """Handle the options flow."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        data_schema = vol.Schema({
            vol.Required(CONF_CLIENT_ID, default=self.config_entry.data.get(CONF_CLIENT_ID)): str,
            vol.Required(CONF_CLIENT_SECRET, default=self.config_entry.data.get(CONF_CLIENT_SECRET)): str,
            vol.Required(CONF_USERNAME, default=self.config_entry.data.get(CONF_USERNAME)): str,
            vol.Required(CONF_PASSWORD, default=self.config_entry.data.get(CONF_PASSWORD)): str,
            vol.Required(CONF_SERIAL, default=self.config_entry.data.get(CONF_SERIAL)): str,
            vol.Optional(CONF_UPDATE_INTERVAL, default=self.config_entry.data.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_DEFAULT)): vol.All(int, vol.Range(min=5, max=3600)),
        })
        if user_input is None:
            return self.async_show_form(step_id="init", data_schema=data_schema)
        return self.async_create_entry(title="Smappee EV", data=user_input)
