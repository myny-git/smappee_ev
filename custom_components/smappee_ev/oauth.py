import aiohttp
import logging
import time
import asyncio
from typing import Any, Dict, Optional
from .const import BASE_URL

OAUTH_TOKEN_URL = f"{BASE_URL.replace('/v3', '/v1')}/oauth2/token"

_LOGGER = logging.getLogger(__name__)


class OAuth2Client:
    """Handles OAuth2 authentication and token refresh for the Smappee API."""

    def __init__(self, data: Dict[str, Any]):
        self.client_id: str = data.get("client_id")
        self.client_secret: str = data.get("client_secret")
        self.username: str = data.get("username")
        self.password: str = data.get("password")
        self.access_token: Optional[str] = data.get("access_token")
        self.refresh_token: Optional[str] = data.get("refresh_token")
        self.token_expires_at: Optional[float] = None
        self.max_refresh_attempts: int = 3

        _LOGGER.debug("OAuth2Client initialized (client_id: %s, username: %s)", self.client_id, self.username)

    async def authenticate(self) -> Optional[Dict[str, Any]]:
        """Authenticate using username/password and return tokens."""
        _LOGGER.info("Authenticating with client_id: %s, username: %s", self.client_id, self.username)

        payload = {
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            async with aiohttp.ClientSession() as session:
                response = await session.post(OAUTH_TOKEN_URL, data=payload)
                resp_text = await response.text()
                _LOGGER.debug("Token endpoint response: %s", resp_text)

                if response.status != 200:
                    _LOGGER.error("Authentication failed: %s", resp_text)
                    return None

                tokens = await response.json()
                if "access_token" in tokens:
                    self.access_token = tokens["access_token"]
                    self.refresh_token = tokens["refresh_token"]
                    self.token_expires_at = time.time() + tokens.get("expires_in", 3600)
                    _LOGGER.info("Authentication succeeded, token valid until %s", self.token_expires_at)
                    return tokens
                else:
                    _LOGGER.error("No access token in response: %s", tokens)
                    return None

        except Exception as e:
            _LOGGER.error("Exception during authentication: %s", e)
            return None

    async def _refresh_token(self) -> None:
        """Refresh the access token if needed, with a retry limit."""
        _LOGGER.info("Refreshing access token using refresh token.")

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        for attempt in range(self.max_refresh_attempts):
            try:
                async with aiohttp.ClientSession() as session:
                    response = await session.post(OAUTH_TOKEN_URL, data=payload)
                    resp_text = await response.text()

                    if response.status == 200:
                        tokens = await response.json()
                        if "access_token" in tokens:
                            self.access_token = tokens.get("access_token")
                            self.refresh_token = tokens.get("refresh_token")
                            self.token_expires_at = time.time() + tokens.get("expires_in", 3600)
                            _LOGGER.info("Token refreshed, valid until %s", self.token_expires_at)
                            return
                        else:
                            _LOGGER.error("No access token in refresh response: %s", tokens)
                            break
                    else:
                        _LOGGER.error(
                            "Failed to refresh token (status %s): %s",
                            response.status,
                            resp_text,
                        )
            except Exception as e:
                _LOGGER.error(
                    "Exception during token refresh attempt %d: %s",
                    attempt + 1,
                    e,
                )
            await asyncio.sleep(2)

        _LOGGER.error(
            "Failed to refresh token after %d attempts.", self.max_refresh_attempts
        )
        raise Exception("Unable to refresh token after multiple attempts.")

        # If all attempts fail, raise an exception
        _LOGGER.error(
            "Failed to refresh token after %d attempts. Please check credentials or network connection.",
            self.max_refresh_attempts,
        )
        raise Exception("Unable to refresh token after multiple attempts.")

    async def ensure_token_valid(self) -> None:
        """Ensure the access token is valid, refreshing if necessary."""
        if (
            not self.access_token
            or not self.token_expires_at
            or time.time() >= self.token_expires_at
        ):
            _LOGGER.info("Access token expired or missing, refreshing...")
            await self._refresh_token()
        else:
            _LOGGER.debug("Access token is valid until %s", self.token_expires_at)
