import asyncio
import logging
import re

import aiohttp
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .const import (
    BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_PASSWORD,
    CONF_SERIAL,
    CONF_SERVICE_LOCATION_ID,
    CONF_SERVICE_LOCATION_UUID,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
    UPDATE_INTERVAL_DEFAULT,
)
from .oauth import OAuth2Client

_LOGGER = logging.getLogger(__name__)


class SmappeeEvConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smappee EV."""

    VERSION = 3

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        session = async_get_clientsession(self.hass)
        timeout = aiohttp.ClientTimeout(connect=5, total=15)

        self._user_input = user_input or {}

        data_schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_CLIENT_SECRET): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SERIAL): str,
                vol.Optional(CONF_UPDATE_INTERVAL, default=UPDATE_INTERVAL_DEFAULT): vol.All(
                    int, vol.Range(min=5, max=3600)
                ),
            }
        )

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=data_schema,
            )

        # Authenticate with the API and get access and refresh tokens  (using HA session)
        oauth_client = OAuth2Client(user_input, session=session)
        tokens = await oauth_client.authenticate()

        if not tokens:
            errors["base"] = "auth_failed"
            return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

        user_input["access_token"] = tokens["access_token"]
        user_input["refresh_token"] = tokens["refresh_token"]

        # Fetch the service_location_id
        try:
            headers = {
                "Authorization": f"Bearer {tokens['access_token']}",
                "Content-Type": "application/json",
            }

            resp = await session.get(
                f"{BASE_URL}/servicelocation", headers=headers, timeout=timeout
            )
            if resp.status != 200:
                errors["base"] = "servicelocation_failed"
                return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
            data = await resp.json()
            locations = data.get("serviceLocations", [])
            if not locations:
                errors["base"] = "no_locations"
                return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

            self._locations = locations
            self._user_input = user_input

            location_options = {
                str(
                    loc["serviceLocationId"]
                ): f"{loc['name']} ({loc.get('deviceSerialNumber', '—')})"
                for loc in locations
            }

            location_schema = vol.Schema(
                {
                    vol.Required(
                        "selected_location", default=list(location_options.keys())[0]
                    ): vol.In(location_options),
                }
            )
            return self.async_show_form(step_id="select_location", data_schema=location_schema)

        except (TimeoutError, ClientError, asyncio.CancelledError, HomeAssistantError) as err:
            _LOGGER.error("Exception while retrieving service_location: %s", err)
            errors["base"] = "servicelocation_failed"
            return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)

    async def async_step_select_location(self, user_input=None):
        """Handle location selection."""
        if user_input is None:
            return self.async_abort(reason="no_selection")

        selected_id = int(user_input["selected_location"])
        selected_location = next(
            loc for loc in self._locations if loc["serviceLocationId"] == selected_id
        )

        self._user_input[CONF_SERVICE_LOCATION_ID] = selected_location["serviceLocationId"]
        self._user_input[CONF_SERVICE_LOCATION_UUID] = selected_location.get("serviceLocationUuid")

        return await self._continue_setup(self._user_input, selected_location)

    async def _continue_setup(self, user_input, selected_location):
        """Continue setup after location selection."""
        session = async_get_clientsession(self.hass)
        timeout = aiohttp.ClientTimeout(connect=5, total=15)
        headers = {
            "Authorization": f"Bearer {user_input['access_token']}",
            "Content-Type": "application/json",
        }

        # Fetch all carcharger devices
        try:
            url = f"{BASE_URL}/servicelocation/{user_input[CONF_SERVICE_LOCATION_ID]}/smartdevices"

            resp = await session.get(url, headers=headers, timeout=timeout)
            if resp.status != 200:
                raise Exception(await resp.text())
            devices = await resp.json()
        except (TimeoutError, ClientError, asyncio.CancelledError, HomeAssistantError) as err:
            _LOGGER.error("Error fetching smartdevices: %s", err)
            return self.async_abort(reason="uuid_failed")

        # 4) Build connector list
        carchargers = []
        for d in devices:
            if d.get("type", {}).get("category") == "CARCHARGER":
                # find connector_number if present
                num = None
                for prop in d.get("configurationProperties", []):
                    if (
                        prop.get("spec", {}).get("name")
                        == "etc.smart.device.type.car.charger.smappee.charger.number"
                    ):
                        num = prop.get("value")
                        break

                # fallback: try to parse from name
                if num is None:
                    name = d.get("name", "")
                    match = re.search(r"\s*[-–—]\s*(\d+)\s*$", name)
                    if match:
                        num = int(match.group(1))

                if isinstance(num, str) and num.isdigit():
                    num = int(num)

                carchargers.append(
                    {
                        "id": d["id"],
                        "uuid": d["uuid"],
                        "connector_number": num,
                    }
                )
        if not carchargers:
            return self.async_abort(reason="no_chargers")

        # 5) Find the station device

        stations = [
            {"id": d["id"], "uuid": d["uuid"]}
            for d in devices
            if d.get("type", {}).get("category") == "CHARGINGSTATION"
        ]

        if not stations:
            _LOGGER.debug("Found stations: %s", stations)
            return self.async_abort(reason="no_station")

        station = stations[0]

        # 6) Store everything under the keys your __init__.py expects
        user_input["carchargers"] = carchargers
        user_input["station"] = {
            "id": station["id"],
            "uuid": station["uuid"],
        }

        await self.async_set_unique_id(station["uuid"])
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"Smappee EV – {selected_location['name']}", data=user_input
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmappeeEvOptionsFlow(config_entry)


class SmappeeEvOptionsFlow(config_entries.OptionsFlow):
    """Handle the options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_CLIENT_ID, default=self.config_entry.data.get(CONF_CLIENT_ID)
                ): str,
                vol.Required(
                    CONF_CLIENT_SECRET, default=self.config_entry.data.get(CONF_CLIENT_SECRET)
                ): str,
                vol.Required(CONF_USERNAME, default=self.config_entry.data.get(CONF_USERNAME)): str,
                vol.Required(CONF_PASSWORD, default=self.config_entry.data.get(CONF_PASSWORD)): str,
                vol.Required(CONF_SERIAL, default=self.config_entry.data.get(CONF_SERIAL)): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=self.config_entry.data.get(
                        CONF_UPDATE_INTERVAL, UPDATE_INTERVAL_DEFAULT
                    ),
                ): vol.All(int, vol.Range(min=5, max=3600)),
            }
        )
        if user_input is None:
            return self.async_show_form(step_id="init", data_schema=data_schema)
        return self.async_create_entry(title="Smappee EV", data=user_input)
