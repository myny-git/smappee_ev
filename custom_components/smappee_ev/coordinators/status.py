"""Coordinator for Smappee service status polling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import override

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

STATUS_URL = "https://status.smappee.net/status"
_SCAN_INTERVAL = timedelta(minutes=5)


@dataclass
class SmappeeStatusData:
    """Parsed Smappee service status response."""

    subsystems: dict[str, str] = field(default_factory=dict)
    active_incidents: list[dict] = field(default_factory=list)


_KNOWN_SUBSYSTEMS = {
    "website",
    "dashboard",
    "android",
    "ios",
    "api",
    "cloud",
    "livedata",
    "historical",
    "evline",
    "evline-ocpp",
    "evline-csms",
    "global",
}
_KNOWN_STATUSES = {"ok", "errors", "maintenance"}


class SmappeeStatusCoordinator(DataUpdateCoordinator[SmappeeStatusData]):
    """Poll https://status.smappee.net/status and expose subsystem statuses."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Smappee Service Status",
            update_interval=_SCAN_INTERVAL,
        )
        self._logged_unknown_subsystems: set[str] = set()
        self._logged_unknown_statuses: set[str] = set()

    @override
    async def _async_update_data(self) -> SmappeeStatusData:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(STATUS_URL, timeout=None) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as err:
            raise UpdateFailed(f"Error fetching Smappee status: {err}") from err

        subsystems = {
            item["subsystem"]: item["status"]
            for item in data.get("subsystems", [])
            if "subsystem" in item and "status" in item
        }
        for subsystem, status in subsystems.items():
            if (
                subsystem not in _KNOWN_SUBSYSTEMS
                and subsystem not in self._logged_unknown_subsystems
            ):
                _LOGGER.warning("Unknown Smappee subsystem '%s' — please report this", subsystem)
                self._logged_unknown_subsystems.add(subsystem)
            if status not in _KNOWN_STATUSES and status not in self._logged_unknown_statuses:
                _LOGGER.warning(
                    "Unknown Smappee status '%s' for subsystem '%s' — please report this",
                    status,
                    subsystem,
                )
                self._logged_unknown_statuses.add(status)
        active_incidents = [
            item
            for item in data.get("incidents", [])
            if isinstance(item, dict) and not item.get("resolved", True)
        ]
        return SmappeeStatusData(subsystems=subsystems, active_incidents=active_incidents)
