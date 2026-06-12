"""Discovery HTTP helpers for Smappee EV setup."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientSession

from .const import BASE_URL
from .oauth import OAuth2Client, SmappeeAuthError

_LOGGER = logging.getLogger(__name__)


async def async_discover_service_locations(
    session: ClientSession, oauth_client: OAuth2Client
) -> list[dict[str, Any]]:
    """Return all service locations that have a deviceSerialNumber."""
    await oauth_client.ensure_token_valid()
    headers = {
        "Authorization": f"Bearer {oauth_client.access_token}",
        "Content-Type": "application/json",
    }
    async with session.get(f"{BASE_URL}/servicelocation", headers=headers) as resp:
        if resp.status != 200:
            text = await resp.text()
            if resp.status in (401, 403):
                raise SmappeeAuthError(f"/servicelocation auth failed: {resp.status}")
            raise RuntimeError(f"/servicelocation failed: {resp.status} - {text}")
        data = await resp.json()
    locations = data.get("serviceLocations", []) if isinstance(data, dict) else (data or [])
    return [sl for sl in locations if sl.get("deviceSerialNumber")]


async def async_fetch_devices(
    session: ClientSession, oauth_client: OAuth2Client, sid: int
) -> list[dict[str, Any]] | None:
    """Return smartdevices for a service location."""
    await oauth_client.ensure_token_valid()
    headers = {
        "Authorization": f"Bearer {oauth_client.access_token}",
        "Content-Type": "application/json",
    }
    async with session.get(
        f"{BASE_URL}/servicelocation/{sid}/smartdevices", headers=headers
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            if resp.status in (401, 403):
                raise SmappeeAuthError(f"/smartdevices auth failed for {sid}: {resp.status}")
            _LOGGER.warning("GET smartdevices for %s failed: %s - %s", sid, resp.status, text)
            return None
        data = await resp.json()
        return data if isinstance(data, list) else None
