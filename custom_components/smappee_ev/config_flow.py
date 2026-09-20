from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, override

import aiohttp
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
import voluptuous as vol

from .api.dashboard_client import SmappeeDashboardClient
from .api.discovery import SmappeeLocationTopology
from .api.errors import SmappeeError
from .const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    CONF_NEEDS_DASHBOARD_REAUTH,
    CONF_PASSWORD,
    CONF_STATION_SERIAL,
    CONF_USERNAME,
    DOMAIN,
)
from .dashboard_discovery import (
    _dashboard_discover_topologies,
    _dashboard_discover_topologies_by_serial,
)
from .registry import async_remove_config_entry_registry_entries

_LOGGER = logging.getLogger(__name__)
ERROR_AUTH_FAILED = "auth_failed"
ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_UNKNOWN = "unknown"


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
            _required_with_optional_default(CONF_USERNAME, defaults): TextSelector(),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


async def _async_dashboard_auth_data(
    user_input: dict[str, Any], session: Any
) -> tuple[dict[str, Any] | None, str | None]:
    """Authenticate against Dashboard and return config-entry data or a flow error."""
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
                return (
                    {
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DASHBOARD_REFRESH_TOKEN: refresh_token,
                    },
                    None,
                )
            _LOGGER.debug("Dashboard login response did not include a refresh token")
            return None, ERROR_UNKNOWN
        _LOGGER.debug("Dashboard login response did not include a usable access token")
        return None, ERROR_UNKNOWN
    except ConfigEntryAuthFailed as err:
        _LOGGER.debug("Dashboard authentication rejected during setup: %s", err)
        return None, ERROR_AUTH_FAILED
    except (SmappeeError, aiohttp.ClientError, RuntimeError, TimeoutError) as err:
        _LOGGER.debug("Dashboard authentication unavailable during setup: %s", err)
        return None, ERROR_CANNOT_CONNECT
    except (TypeError, ValueError) as err:
        _LOGGER.debug("Dashboard authentication returned an unexpected response: %s", err)
        return None, ERROR_UNKNOWN


class SmappeeEvConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smappee EV."""

    VERSION = 7

    _pending_data: dict[str, Any]

    def _discovery_client(self) -> SmappeeDashboardClient:
        """Recreate an authenticated client and retain any rotated refresh token."""
        return SmappeeDashboardClient(
            username=self._pending_data.get(CONF_USERNAME),
            password=self._pending_data.get(CONF_PASSWORD),
            refresh_token=self._pending_data.get(CONF_DASHBOARD_REFRESH_TOKEN),
            session=async_get_clientsession(self.hass),
            token_update_callback=self._pending_data.update,
        )

    async def _async_validate_discovery(self, serial: str | None = None) -> str | None:
        """Return a form error, or None for a usable topology."""
        client = self._discovery_client()
        topologies: list[SmappeeLocationTopology] | None
        try:
            if serial is not None:
                topologies = await _dashboard_discover_topologies_by_serial(client, serial)
            else:
                topologies = await _dashboard_discover_topologies(client)
            if topologies is None:
                return ERROR_UNKNOWN
            return None if topologies else "invalid_station_serial"
        except ConfigEntryAuthFailed:
            return ERROR_AUTH_FAILED
        except (SmappeeError, aiohttp.ClientError, RuntimeError, TimeoutError):
            return ERROR_CANNOT_CONNECT
        except Exception:
            # Remote payloads can contain credentials; do not log their contents.
            _LOGGER.debug("Dashboard discovery returned an unexpected response")
            return ERROR_UNKNOWN

    def _finish_discovery(self) -> ConfigFlowResult:
        """Commit validated setup or reconfiguration data."""
        if self.source == config_entries.SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                unique_id=self.unique_id,
                data=self._pending_data,
                options={},
            )
        return self.async_create_entry(
            title=f"Smappee EV - {self._pending_data[CONF_USERNAME]}", data=self._pending_data
        )

    async def async_step_station_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a station serial only after successful empty automatic discovery."""
        errors: dict[str, str] = {}
        defaults = dict(self._pending_data)
        if user_input is not None:
            serial = str(user_input[CONF_STATION_SERIAL]).strip()
            defaults[CONF_STATION_SERIAL] = serial
            error = (
                await self._async_validate_discovery(serial) if serial else "invalid_station_serial"
            )
            if error is None:
                self._pending_data[CONF_STATION_SERIAL] = serial
                return self._finish_discovery()
            errors["base"] = error
        return self.async_show_form(
            step_id="station_serial",
            data_schema=vol.Schema(
                {_required_with_optional_default(CONF_STATION_SERIAL, defaults): TextSelector()}
            ),
            errors=errors,
        )

    @override
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
            data, error = await _async_dashboard_auth_data(user_input, session)
        except Exception:
            _LOGGER.exception("Unexpected error during authentication")
            errors["base"] = ERROR_UNKNOWN
            return self.async_show_form(step_id=step_id, data_schema=data_schema, errors=errors)
        if data is None or error is not None:
            errors["base"] = error or ERROR_UNKNOWN
            return self.async_show_form(step_id=step_id, data_schema=data_schema, errors=errors)

        unique = f"smappee_ev:{user_input[CONF_USERNAME]}"
        await self.async_set_unique_id(unique)

        if self.source == config_entries.SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if entry.unique_id:
                self._abort_if_unique_id_mismatch(reason="wrong_account")
            if entry.data.get(CONF_NEEDS_DASHBOARD_REAUTH):
                async_remove_config_entry_registry_entries(self.hass, entry)
            if CONF_STATION_SERIAL in entry.data:
                data[CONF_STATION_SERIAL] = entry.data[CONF_STATION_SERIAL]
            return self.async_update_reload_and_abort(entry, unique_id=unique, data=data)

        if self.source == config_entries.SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            if entry.unique_id:
                self._abort_if_unique_id_mismatch(reason="wrong_account")
            if CONF_STATION_SERIAL in entry.data:
                data[CONF_STATION_SERIAL] = entry.data[CONF_STATION_SERIAL]
        else:
            self._abort_if_unique_id_configured()

        self._pending_data = data
        error = await self._async_validate_discovery()
        if error == "invalid_station_serial":
            _LOGGER.debug("Automatic discovery returned zero charging topologies")
            serial = data.get(CONF_STATION_SERIAL)
            if serial:
                _LOGGER.debug("Trying stored station serial during reconfiguration")
                error = await self._async_validate_discovery(serial)
            if error == "invalid_station_serial":
                return await self.async_step_station_serial()
        if error is not None:
            return self.async_show_form(
                step_id=step_id, data_schema=data_schema, errors={"base": error}
            )
        return self._finish_discovery()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
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
