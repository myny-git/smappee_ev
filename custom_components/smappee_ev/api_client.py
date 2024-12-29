import aiohttp
import logging
import random

_LOGGER = logging.getLogger(__name__)


class SmappeeApiClient:
    def __init__(self, oauth_client, serial):
        self.oauth_client = oauth_client
        self.base_url = "https://app1pub.smappee.net/dev/v3"
        self.serial = serial

    async def update():
        return True

    async def update_all():
        return True

    async def force_update_all():
        return True

    async def check_and_refresh_token():
        await self.oauth_client.ensure_token_valid()
        return True

    async def check_action_status():
        return True

    @property
    def fetchLatestSessionCounter(self) -> int:
        """Set the charging mode for the given serial number and connector."""
        # Ensure token is refreshed if needed
        await self.oauth_client.ensure_token_valid()
        return random.randint(0, 100)

#        url = f"{self.base_url}/chargingstations/{self.serial}/sessions?active=true&range={midnight.timestamp()}"
#        headers = {
#            "Authorization": f"Bearer {self.oauth_client.access_token}",
#            "Content-Type": "application/json",
#        }
#        _LOGGER.debug(f"Sending request to {url}")

#        try:
#            async with aiohttp.ClientSession() as session:
#                response = await session.get(url, headers=headers)
#                if response.status != 200:
#                    if response.status == 401:
#                        raise Exception("Token expired")
#                    error_message = await response.text()
#                    _LOGGER.error(f"Failed to set charging mode: {error_message}")
#                    raise Exception(f"Error setting charging mode: {error_message}")
#                _LOGGER.debug(response.text())
#                return 10
#        except Exception as e:
#            _LOGGER.error(f"Exception occurred while getting latest session counter: {str(e)}")
#            raise
    
    async def set_charging_mode(self, serial, mode, limit):
        """Set the charging mode for the given serial number and connector."""
        # Ensure token is refreshed if needed
        await self.oauth_client.ensure_token_valid()

        url = f"{self.base_url}/chargingstations/{serial}/connectors/1/mode"
        headers = {
            "Authorization": f"Bearer {self.oauth_client.access_token}",
            "Content-Type": "application/json",
        }

        limitPercentage = True if mode == "NORMAL_PERCENTAGE" else False
        if limitPercentage:
            mode = "NORMAL"

        # Create the base payload with the mode
        payload = {"mode": mode}

        # Add the limit only if the mode is NORMAL
        if mode == "NORMAL":
            if limitPercentage:
                payload["limit"] = {"unit": "PERCENTAGE", "value": limit}
            else:
                payload["limit"] = {"unit": "AMPERE", "value": limit}

        _LOGGER.debug(f"Sending request to {url} with payload {payload}")

        # Make the API request to set the charging mode
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.put(url, json=payload, headers=headers)
                if response.status != 200:
                    if response.status == 401:
                        raise Exception("Token expired")
                    
                    error_message = await response.text()
                    _LOGGER.error(f"Failed to set charging mode: {error_message}")
                    raise Exception(f"Error setting charging mode: {error_message}")
                _LOGGER.debug("Successfully set charging mode")
        except Exception as e:
            _LOGGER.error(f"Exception occurred while setting charging mode: {str(e)}")
            raise
