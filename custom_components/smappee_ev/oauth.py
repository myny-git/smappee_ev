import asyncio
from collections.abc import Mapping
import logging
import time
from typing import Any

import aiohttp
from aiohttp import ClientError

from .const import (
    BASE_URL,
    OAUTH_CONNECT_TIMEOUT,
    OAUTH_EARLY_RENEW_SKEW,
    OAUTH_MAX_REFRESH_ATTEMPTS,
    OAUTH_REFRESH_RETRY_BASE_DELAY,
    OAUTH_TOTAL_TIMEOUT,
    TOKEN_DEFAULT_EXPIRES_IN,
)

OAUTH_TOKEN_URL = f"{BASE_URL.replace('/v3', '/v1')}/oauth2/token"

_LOGGER = logging.getLogger(__name__)


class OAuth2Client:
    """Handles OAuth2 authentication and token refresh for the Smappee API."""

    def __init__(self, data: Mapping[str, Any], session: aiohttp.ClientSession):
        self._session: aiohttp.ClientSession = session
        self._timeout = aiohttp.ClientTimeout(
            connect=OAUTH_CONNECT_TIMEOUT, total=OAUTH_TOTAL_TIMEOUT
        )
        self.client_id: str = str(data.get("client_id") or "")
        self.client_secret: str = str(data.get("client_secret") or "")
        self.username: str = str(data.get("username") or "")
        self.password: str = str(data.get("password") or "")
        self.access_token: str | None = data.get("access_token")
        self.refresh_token: str | None = data.get("refresh_token")
        self.token_expires_at: float | None = None
        self.max_refresh_attempts: int = OAUTH_MAX_REFRESH_ATTEMPTS
        self._refresh_lock = asyncio.Lock()
        self._early_renew_skew = OAUTH_EARLY_RENEW_SKEW

        _LOGGER.debug(
            "OAuth2Client initialized (client_id: %s, username: %s)", self.client_id, self.username
        )

    async def authenticate(self) -> dict[str, Any] | None:
        """Authenticate using username/password and return tokens."""
        _LOGGER.info(
            "Authenticating with client_id: %s, username: %s", self.client_id, self.username
        )

        payload = {
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            async with self._session.post(
                OAUTH_TOKEN_URL, data=payload, timeout=self._timeout
            ) as response:
                text = await response.text()
                _LOGGER.debug("Token endpoint response (authenticate): %s", text)
                if response.status != 200:
                    _LOGGER.error(
                        "Authentication failed: status=%s, body=%s", response.status, text
                    )
                    return None
                tokens = await response.json()
                if "access_token" not in tokens:
                    _LOGGER.error("No access token in response: %s", tokens)
                    return None
                self.access_token = tokens.get("access_token")
                self.refresh_token = tokens.get("refresh_token")
                self.token_expires_at = time.time() + tokens.get(
                    "expires_in", TOKEN_DEFAULT_EXPIRES_IN
                )
                _LOGGER.info("Authentication succeeded; token validity window established")
                return tokens

        except (TimeoutError, ClientError, asyncio.CancelledError) as err:
            _LOGGER.error("Exception during authentication: %s", err)
            return None

    async def _refresh_token(self) -> None:
        """Refresh the access token if needed, with a retry limit."""
        _LOGGER.info("Refreshing access token using refresh token.")

        if not self.refresh_token:
            _LOGGER.warning("No refresh_token available; falling back to authenticate().")
            tokens = await self.authenticate()
            if not tokens:
                raise Exception("No refresh_token and authenticate() failed.")
            return

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        for attempt in range(self.max_refresh_attempts):
            try:
                async with self._session.post(
                    OAUTH_TOKEN_URL, data=payload, timeout=self._timeout
                ) as response:
                    text = await response.text()
                    if response.status == 200:
                        tokens = await response.json()
                        if "access_token" not in tokens:
                            _LOGGER.error("No access token in refresh response: %s", tokens)
                            break
                        self.access_token = tokens.get("access_token")
                        self.refresh_token = tokens.get("refresh_token")
                        self.token_expires_at = time.time() + tokens.get(
                            "expires_in", TOKEN_DEFAULT_EXPIRES_IN
                        )
                        _LOGGER.info("Access token refreshed; new validity window established")
                        return
                    _LOGGER.error("Failed to refresh token (status %s): %s", response.status, text)

            except (TimeoutError, ClientError, asyncio.CancelledError) as err:
                _LOGGER.error(
                    "Exception during token refresh attempt %d: %s",
                    attempt + 1,
                    err,
                )
            await asyncio.sleep(OAUTH_REFRESH_RETRY_BASE_DELAY * (attempt + 1))

        # Generic message without dynamic values to avoid CodeQL clear-text alert on variables
        _LOGGER.error("Failed to refresh access token after maximum attempts")
        raise Exception("Unable to refresh token after multiple attempts.")

    async def ensure_token_valid(self) -> None:
        """Ensure the access token is valid, refreshing if necessary."""
        now = time.time()
        if (
            not self.access_token
            or not self.token_expires_at
            or now >= (self.token_expires_at - self._early_renew_skew)
        ):
            _LOGGER.info("Access token missing/expired or near expiry; refreshing")
            await self._refresh_token()
        else:
            _LOGGER.debug("Access token still valid; no refresh needed")
