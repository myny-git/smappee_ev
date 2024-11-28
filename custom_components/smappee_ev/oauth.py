import aiohttp
import logging
import time
import asyncio

_LOGGER = logging.getLogger(__name__)


class OAuth2Client:
    def __init__(self, data):
        self.client_id = data.get("client_id")
        self.client_secret = data.get("client_secret")
        self.access_token = data.get("access_token")
        self.refresh_token = data.get("refresh_token")
        self.token_url = "https://app1pub.smappee.net/dev/v1/oauth2/token"
        self.base_url = (
            "https://app1pub.smappee.net/dev/v3"  # Base URL for API requests
        )
        self.username = data.get("username")
        self.password = data.get("password")
        self.token_expires_at = None  # Store the expiry time
        self.max_refresh_attempts = 3

    async def authenticate(self):
        """Authenticate using client credentials and return tokens."""
        _LOGGER.debug(
            "Starting authentication with client_id: %s, username: %s",
            self.client_id,
            self.username,
        )

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "grant_type": "password",
                    "username": self.username,
                    "password": self.password,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }

                _LOGGER.debug("Sending payload: %s", payload)

                response = await session.post(self.token_url, data=payload)

                _LOGGER.debug("Received response: %s", await response.text())

                if response.status != 200:
                    _LOGGER.error(
                        "Authentication failed with status: %s", response.status
                    )
                    return None

                tokens = await response.json()
                _LOGGER.debug("Received tokens: %s", tokens)

                if "access_token" in tokens:
                    self.access_token = tokens["access_token"]
                    self.refresh_token = tokens["refresh_token"]
                    self.token_expires_at = time.time() + tokens.get(
                        "expires_in", 3600
                    )  # Set expiry time
                    return tokens
                else:
                    _LOGGER.error("No access token in response: %s", tokens)
                    return None

        except Exception as e:
            _LOGGER.error("Exception occurred during authentication: %s", e)
            return None

    async def _refresh_token(self):
        """Refresh the access token if needed, with a retry limit."""
        _LOGGER.debug("Refreshing access token using refresh token.")

        for attempt in range(self.max_refresh_attempts):
            try:
                async with aiohttp.ClientSession() as session:
                    response = await session.post(
                        self.token_url,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": self.refresh_token,
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                        },
                    )

                    if response.status == 200:
                        tokens = await response.json()
                        _LOGGER.debug("Refreshed tokens: %s", tokens)

                        if "access_token" in tokens:
                            self.access_token = tokens.get("access_token")
                            self.refresh_token = tokens.get("refresh_token")
                            self.token_expires_at = time.time() + tokens.get(
                                "expires_in", 3600
                            )
                            return
                        else:
                            _LOGGER.error("No access token in response: %s", tokens)
                            break
                    else:
                        _LOGGER.error(
                            "Failed to refresh token (status %s): %s",
                            response.status,
                            await response.text(),
                        )

            except Exception as e:
                _LOGGER.error(
                    "Exception occurred during token refresh attempt %d: %s",
                    attempt + 1,
                    e,
                )

            # Add a small delay between retries, if needed
            await asyncio.sleep(2)

        # If all attempts fail, raise an exception
        _LOGGER.error(
            "Failed to refresh token after %d attempts. Please check credentials or network connection.",
            self.max_refresh_attempts,
        )
        raise Exception("Unable to refresh token after multiple attempts.")

    async def ensure_token_valid(self):
        """Ensure the access token is valid, refreshing if necessary."""
        if not self.access_token or not self.token_expires_at or time.time() >= self.token_expires_at:
            _LOGGER.debug("Access token expired or missing, refreshing...")
            await self._refresh_token()
        else:
            _LOGGER.debug("Access token is still valid until %s", self.token_expires_at)
