from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any, Literal

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from .const import BASE_URL, HTTP_CONNECT_TIMEOUT, HTTP_TOTAL_TIMEOUT
from .oauth import SmappeeAuthError

_LOGGER = logging.getLogger(__name__)


class SmappeeApiClient:
    """Command-only client. No polling/state; the coordinator owns state."""

    def __init__(
        self,
        oauth_client,
        serial: str,
        smart_device_uuid: str,
        smart_device_id: str,
        service_location_id: str,
        *,
        session: ClientSession,
        connector_number: int | None = None,
        is_station: bool = False,
        charging_station_serial: str | None = None,
    ):
        self.oauth_client = oauth_client
        self.serial = serial
        self.charging_station_serial = charging_station_serial
        self.smart_device_uuid = smart_device_uuid
        self.smart_device_id = smart_device_id
        self.service_location_id = service_location_id
        self.connector_number = connector_number

        self.is_station = is_station
        self._session: ClientSession = session
        self._timeout: ClientTimeout = ClientTimeout(
            connect=HTTP_CONNECT_TIMEOUT, total=HTTP_TOTAL_TIMEOUT
        )
        self.station_action_uuid: str | None = None
        # Stateless: no per-connector mutable charging attributes kept here.
        # All dynamic values live in Coordinator/ConnectorState.
        self._callbacks: set[Callable[..., Any]] = set()  # kept for future hook use

        _LOGGER.info(
            "SmappeeApiClient initialized (serial=%s, connector=%s, station=%s)",
            self.serial,
            self.connector_number,
            self.is_station,
        )

    # ---- small helpers the coordinator uses to fetch state ----
    async def ensure_auth(self) -> None:
        await self.oauth_client.ensure_token_valid()

    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.oauth_client.access_token}",
            "Content-Type": "application/json",
        }

    def get_http_session(self) -> ClientSession:
        return self._session

    @property
    def serial_id(self) -> str:
        return self.serial

    # ------------------------------------------------------------------
    # Generic request helper (auth + error handling)
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        expected: tuple[int, ...] = (200, 204),
        return_json: bool = False,
    ) -> Any | None:
        await self.ensure_auth()
        async with self._session.request(
            method,
            url,
            json=json,
            headers=self.auth_headers(),
            timeout=self._timeout,
        ) as resp:
            if resp.status not in expected:
                text = await resp.text()
                if resp.status in (401, 403):
                    raise SmappeeAuthError(
                        f"Request authentication failed {resp.status} ({method} {url}): {text}"
                    )
                raise RuntimeError(f"Request failed {resp.status} ({method} {url}): {text}")
            if return_json:
                if resp.content_length == 0:
                    return None
                try:
                    return await resp.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    _LOGGER.debug("Empty or invalid JSON response for %s %s: %s", method, url, err)
                    return None
            return None

    async def async_get_smartdevices(self) -> list[dict[str, Any]] | None:
        """Fetch all smartdevices for this service location."""
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices"
        data = await self._request("GET", url, expected=(200,), return_json=True)
        return data if isinstance(data, list) else None

    async def async_get_smartdevice(self, smart_device_id: str) -> dict[str, Any] | None:
        """Fetch a single smartdevice by its numeric/string ID."""
        url = (
            f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{smart_device_id}"
        )
        data = await self._request("GET", url, expected=(200,), return_json=True)
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------
    # COMMANDS (write actions)
    # ------------------------------------------------------------------
    async def set_charging_mode(
        self,
        mode: str,
    ) -> bool:
        """Set charging mode via the smartdevices endpoint (STANDARD, SMART, SOLAR).

        This matches the Smappee app mode buttons exactly:
        - Standard button → setchargingmode = {"mode":"STANDARD"}  --> also matches the "resume" action when paused
        - Smart button    → setchargingmode = {"mode":"SMART"}
        - Solar button    → setchargingmode = {"mode":"SOLAR"}

        No current/percentage limit is used here; use set_percentage_limit for speed control.
        """
        mode_up = (mode or "").upper()
        if mode_up not in ("STANDARD", "SMART", "SOLAR"):
            _LOGGER.warning(
                "Unsupported charging mode for smartdevices endpoint: %s. Use STANDARD, SMART or SOLAR.",
                mode,
            )
            return False

        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setChargingMode"
        payload: list[dict[str, Any]] = [
            {"spec": {"name": "mode", "species": "String"}, "value": mode_up}
        ]
        await self._request("POST", url, json=payload, expected=(200, 204))
        _LOGGER.debug("Charging mode set successfully (%s via smartdevices)", mode_up)
        return True

    async def set_charging_mode_chargingstations(
        self,
        mode: str,
        *,
        limit: int | None = None,
        limit_unit: Literal["AMPERE", "PERCENTAGE"] = "AMPERE",
        connector: int | None = None,
    ) -> bool:
        """Set charging mode via the chargingstations endpoint (NORMAL, SMART, PAUSED).

        This is the legacy endpoint matching the chargingstations REST API:
          PUT /chargingstations/{serial}/connectors/{pos}/mode
        For NORMAL mode an optional ``limit`` (with ``limit_unit`` AMPERE or PERCENTAGE) may
        be supplied.  SMART and PAUSED do not accept a limit.
        """
        mode_up = (mode or "").upper()
        if mode_up not in ("NORMAL", "SMART", "PAUSED"):
            _LOGGER.warning(
                "Unsupported charging mode for chargingstations endpoint: %s. Use NORMAL, SMART or PAUSED.",
                mode,
            )
            return False

        connector_id = int(connector or self.connector_number or 1)
        station_serial = self.charging_station_serial or self.serial
        url = f"{BASE_URL}/chargingstations/{station_serial}/connectors/{connector_id}/mode"

        cs_payload: dict[str, Any] = {"mode": mode_up}
        if mode_up == "NORMAL" and limit is not None:
            limit_unit_up = (str(limit_unit or "")).upper()
            if limit_unit_up not in ("AMPERE", "PERCENTAGE"):
                _LOGGER.warning("Unsupported limit unit: %s", limit_unit)
                return False
            cs_payload["limit"] = {"unit": limit_unit_up, "value": int(limit)}

        await self._request("PUT", url, json=cs_payload, expected=(200, 204))
        _LOGGER.debug(
            "Charging mode set successfully via chargingstations (mode=%s, connector=%s)",
            mode_up,
            connector_id,
        )
        return True

    async def start_charging(
        self, current: float, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[float, int]:
        """Start charging at (clamped) amperage.

        Returns (current_amps, percentage_limit) so caller can update ConnectorState.
        """
        # _request handles auth
        if max_current < min_current:
            min_current, max_current = max_current, min_current

        min_current_f = float(min_current)
        max_current_f = float(max_current)
        current_f = round(float(current), 1)

        if max_current_f == min_current_f:
            target = round(min_current_f, 1)
            percentage = 100
        else:
            requested = max(min_current_f, min(current_f, max_current_f))
            rng = max_current_f - min_current_f
            percentage = int(max(0, min(100, round(((requested - min_current_f) * 100.0) / rng))))
            target = round(min_current_f + (percentage / 100.0) * rng, 1)

        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/startCharging"
        payload = [{"spec": {"name": "percentageLimit", "species": "Integer"}, "value": percentage}]
        await self._request("POST", url, json=payload, expected=(200, 204))

        _LOGGER.debug(
            "Started charging successfully (target=%s A, pct=%s, range=%s-%s)",
            target,
            percentage,
            min_current,
            max_current,
        )
        return target, percentage

    async def pause_charging(self) -> None:
        """Pause charging via the smartdevices endpoint (recommended, more stable).

        This matches the Smappee app Pause button:  pausecharging = {}
        """
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/pauseCharging"
        await self._request("POST", url, json=[], expected=(200, 204))
        _LOGGER.debug("Paused charging successfully (smartdevices endpoint)")

    async def pause_charging_chargingstations(self) -> None:
        """Pause charging via the chargingstations endpoint (legacy).

        Sends mode=PAUSED to /chargingstations/{serial}/connectors/{pos}/mode.
        Use this as a fallback if your firmware does not respond to the smartdevices endpoint.
        """
        await self.set_charging_mode_chargingstations("PAUSED")

    async def stop_charging(self) -> None:
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/stopCharging"
        await self._request("POST", url, json=[], expected=(200, 204))
        _LOGGER.debug("Stopped charging successfully")

    async def set_brightness(self, brightness: int) -> None:
        if not self.smart_device_uuid:
            raise RuntimeError("set_brightness: missing station smart_device_uuid")

        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setBrightness"
        payload = [
            {
                "spec": {
                    "name": "etc.smart.device.type.car.charger.led.config.brightness",
                    "species": "Integer",
                },
                "value": int(brightness),
            }
        ]
        await self._request("POST", url, json=payload, expected=(200, 204))
        _LOGGER.info("LED brightness set successfully to %d%%", brightness)

    async def set_min_surpluspct(self, min_surpluspct: int) -> None:
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_id}"
        payload = {
            "configurationProperties": [
                {
                    "spec": {
                        "name": "etc.smart.device.type.car.charger.config.min.excesspct",
                        "species": "Integer",
                    },
                    "value": min_surpluspct,
                }
            ]
        }
        await self._request("PATCH", url, json=payload, expected=(200, 204))
        _LOGGER.info("min.surpluspct set successfully to %d%%", min_surpluspct)

    async def set_percentage_limit(
        self, percentage: int, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[float, int]:
        """Set percentage limit; returns (current_amps_float, percentage)."""
        pct_int = max(0, min(100, int(round(percentage))))
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setPercentageLimit"
        payload = [{"spec": {"name": "percentageLimit", "species": "Integer"}, "value": pct_int}]
        await self._request("POST", url, json=payload, expected=(200, 204))
        _LOGGER.debug("Set percentage limit successfully to %d%%", pct_int)

        if max_current <= min_current:
            cur_float = float(min_current)
        else:
            rng = max_current - min_current
            cur_float = round(min_current + (float(pct_int) / 100.0) * rng, 1)
            cur_float = max(float(min_current), min(float(max_current), cur_float))
        return cur_float, pct_int

    async def set_current(
        self, current: float, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[float, int]:
        """Set charging current in Ampere (1 decimal precision).

        Converts the requested current to the nearest integer percentage of the
        configured min–max range and delegates to set_percentage_limit.
        Returns (current_amps_float, percentage) so the caller can update
        ConnectorState immediately without waiting for the next poll.
        """
        if max_current <= min_current:
            pct = 100
        else:
            val = max(float(min_current), min(round(float(current), 1), float(max_current)))
            rng = max_current - min_current
            pct = int(round((val - min_current) / float(rng) * 100))
            pct = max(0, min(100, pct))
        _LOGGER.debug(
            "set_current: %.1f A → %d%% (range %s–%s A)",
            current,
            pct,
            min_current,
            max_current,
        )
        return await self.set_percentage_limit(
            pct, min_current=min_current, max_current=max_current
        )

    async def set_available(self) -> None:
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setAvailable"
        await self._request("POST", url, json=[], expected=(200, 204))
        _LOGGER.debug("Set charger available successfully")

    async def set_unavailable(self) -> None:
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setUnavailable"
        await self._request("POST", url, json=[], expected=(200, 204))
        _LOGGER.debug("Set charger unavailable successfully")

    async def async_get_metering_configuration(self) -> dict | None:
        """Fetch metering configuration for this service location."""
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/meteringconfiguration"
        try:
            data = await self._request("GET", url, expected=(200,), return_json=True)
        except SmappeeAuthError:
            raise
        except (RuntimeError, TimeoutError, aiohttp.ClientError) as exc:
            _LOGGER.warning("Metering configuration fetch failed: %s", exc)
            return None
        return data if isinstance(data, dict) else None
