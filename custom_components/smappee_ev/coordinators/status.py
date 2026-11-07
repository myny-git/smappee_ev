"""Coordinator for Smappee service status polling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from math import isfinite
from time import monotonic
from typing import Any, Protocol, override

from aiohttp import ClientTimeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..const import (
    DOMAIN,
    HTTP_CONNECT_TIMEOUT,
    HTTP_TOTAL_TIMEOUT,
    MANUFACTURER,
    MQTT_REAL_POWER_FRESHNESS_TIMEOUT,
)
from .freshness import MeasurementFreshness

_LOGGER = logging.getLogger(__name__)

STATUS_URL = "https://status.smappee.net/status"
# Check MQTT locally every five minutes; healthy installations only fetch every fifteen.
_SCAN_INTERVAL = timedelta(minutes=5)
_REFRESH_INTERVAL = timedelta(minutes=15)


class _MqttTelemetry(Protocol):
    """The MQTT fields used by status polling, independent of runtime containers."""

    mqtt_transport_connected: bool
    measurement_freshness: MeasurementFreshness


class _StationTelemetry(Protocol):
    @property
    def station_coordinator(self) -> _MqttTelemetry | None:
        """Return this station's coordinator when it has been created."""


class _SiteTelemetry(Protocol):
    """Read-only site view so concrete runtime containers can provide the data."""

    @property
    def site_coordinator(self) -> _MqttTelemetry | None:
        """Return the site's measurement coordinator when available."""

    @property
    def stations(self) -> Mapping[str, _StationTelemetry]:
        """Return the stations whose MQTT telemetry belongs to this site."""


@dataclass
class SmappeeStatusData:
    """Parsed Smappee service status response."""

    subsystems: dict[str, str] = field(default_factory=dict)
    active_incidents: list[dict] = field(default_factory=list)


STATUS_SUBSYSTEMS = {
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


def _parse_status_data(payload: object) -> SmappeeStatusData:
    """Validate public response data before exposing it to entities."""
    if not isinstance(payload, dict):
        raise TypeError("Status response must be an object")
    systems = payload.get("subsystems", [])
    incidents = payload.get("incidents", [])
    if not isinstance(systems, list) or not isinstance(incidents, list):
        raise TypeError("Status subsystems and incidents must be lists")
    subsystems: dict[str, str] = {}
    for item in systems:
        if not isinstance(item, dict):
            raise TypeError("Invalid status subsystem")
        name, status = item.get("subsystem"), item.get("status")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Missing status subsystem name")
        if not isinstance(status, str) or not status.strip():
            raise ValueError("Missing subsystem status")
        subsystems[name] = status
    active_incidents = []
    for incident in incidents:
        if not isinstance(incident, dict) or not isinstance(incident.get("resolved", True), bool):
            raise TypeError("Invalid status incident")
        if incident.get("resolved", True):
            continue
        for key in ("subsystem", "level"):
            if incident.get(key) is not None and not isinstance(incident[key], str):
                raise ValueError(f"Invalid incident {key}")
        timestamp = incident.get("timestamp")
        if timestamp is not None:
            if (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, int | float)
                or not isfinite(timestamp)
            ):
                raise ValueError("Invalid incident timestamp")
            # Validate the conversion used by the sensor, including platform limits.
            datetime.fromtimestamp(timestamp / 1000, tz=UTC)
        active_incidents.append(incident)
    if not subsystems and not active_incidents:
        raise ValueError("Status response contains no subsystem status or active incidents")
    return SmappeeStatusData(subsystems=subsystems, active_incidents=active_incidents)


class SmappeeStatusCoordinator(DataUpdateCoordinator[SmappeeStatusData]):
    """Poll https://status.smappee.net/status and expose subsystem statuses."""

    def __init__(
        self,
        hass: HomeAssistant,
        sites: Mapping[int, _SiteTelemetry] | None = None,
        *,
        config_entry: ConfigEntry[Any] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Smappee Service Status",
            update_interval=_SCAN_INTERVAL,
            config_entry=config_entry,
        )
        self._logged_unknown_subsystems: set[str] = set()
        self._logged_unknown_statuses: set[str] = set()
        self._sites = sites if sites is not None else {}
        self._last_status_fetch: float | None = None
        self._last_status_fetch_success = False

    @property
    def unique_id_prefix(self) -> str:
        """Keep public status entities separate for independently loaded accounts."""
        prefix = "smappee_service_status"
        return f"{self.config_entry.entry_id}:{prefix}" if self.config_entry else prefix

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id_prefix)},
            name="Smappee Service Status",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://status.smappee.net",
        )

    def _has_live_mqtt_data(self) -> bool:
        """Require recent accepted measurements for each site, excluding heartbeats."""
        now = datetime.now(UTC)
        for site in self._sites.values():
            coordinators = [
                site.site_coordinator,
                *(station.station_coordinator for station in site.stations.values()),
            ]
            if not any(
                coord is not None
                and coord.mqtt_transport_connected
                and any(
                    timedelta(0) <= now - received < MQTT_REAL_POWER_FRESHNESS_TIMEOUT
                    for received in coord.measurement_freshness.received.values()
                )
                for coord in coordinators
            ):
                return False
        return bool(self._sites)

    @override
    async def _async_update_data(self) -> SmappeeStatusData:
        now = monotonic()
        if (
            self._last_status_fetch_success
            and self._last_status_fetch is not None
            and now - self._last_status_fetch < _REFRESH_INTERVAL.total_seconds()
            and self._has_live_mqtt_data()
        ):
            return self.data

        self._last_status_fetch = now
        self._last_status_fetch_success = False
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                STATUS_URL,
                timeout=ClientTimeout(total=HTTP_TOTAL_TIMEOUT, connect=HTTP_CONNECT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = _parse_status_data(await resp.json(content_type=None))
        except Exception as err:
            raise UpdateFailed(f"Error fetching Smappee status: {err}") from err

        for subsystem, status in data.subsystems.items():
            if (
                subsystem not in STATUS_SUBSYSTEMS
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
        self._last_status_fetch_success = True
        return data
