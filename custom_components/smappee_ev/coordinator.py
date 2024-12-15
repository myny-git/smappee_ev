from datetime import timedelta
import traceback
import logging

from .oauth import OAuth2Client
from .api_client import SmappeeApiClient
from .const import DOMAIN

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    "client_id",
    "client_secret",
    "username",
    "password",
    "serial",
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

class SmappeeChargerCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize."""
        _LOGGER.debug("Init OAuth...")
        self.oauth_client = OAuth2Client(config_entry.data)
        _LOGGER.debug("Init OAuth...done")
        _LOGGER.debug("Init API...")    
        self.api_client = SmappeeApiClient(self.oauth_client)
        _LOGGER.debug("Init API...done")    
      
        self.scan_interval: int = 30
        self.force_refresh_interval: int = 60
      
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=min(self.scan_interval, self.force_refresh_interval)
            ),
        )

    async def _async_update_data(self):
        await self.async_check_and_refresh_token()
        self.api_client.update()
        return self.data

    async def async_update_all(self) -> None:
        await self.async_check_and_refresh_token()
        await self.hass.async_add_executor_job(
            self.api_client.update_all
        )
        await self.async_refresh()

    async def async_force_update_all(self) -> None:
        await self.async_check_and_refresh_token()
        await self.hass.async_add_executor_job(
            self.api_client.force_update_all
        )
        await self.async_refresh()

    async def async_check_and_refresh_token(self):
        await self.hass.async_add_executor_job(
            self.api_client.check_and_refresh_token
        )

    async def async_await_action_and_refresh(self, chargingmode, action_id):
        try:
            await self.hass.async_add_executor_job(
                self.api_client.check_action_status,
                chargingmode,
                action_id,
                True,
                60,
            )
        finally:
            await self.async_refresh()

    async def async_lock_vehicle(self, chargingmode: str):
        await self.async_check_and_refresh_token()
        action_id = await self.hass.async_add_executor_job(
            self.api_client.chargingmode, chargingmode
        )
        self.hass.async_create_task(
            self.async_await_action_and_refresh(chargingmode, action_id)
        )

