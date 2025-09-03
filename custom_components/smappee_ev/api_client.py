from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from .const import BASE_URL, HTTP_CONNECT_TIMEOUT, HTTP_TOTAL_TIMEOUT

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
    ):
        self.oauth_client = oauth_client
        self.serial = serial
        self.smart_device_uuid = smart_device_uuid
        self.smart_device_id = smart_device_id
        self.service_location_id = service_location_id
        self.connector_number = connector_number
        self.led_device_id: str | None = None
        self.led_device_uuid: str | None = None

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

    async def _ensure_led_device(self) -> None:
        if self.led_device_id and self.led_device_uuid:
            return
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices"
        resp = await self._session.get(url, headers=self.auth_headers(), timeout=self._timeout)
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"LED discovery failed: {txt}")
        devices = await resp.json()
        for dev in devices or []:
            for prop in dev.get("configurationProperties", []) or []:
                spec = prop.get("spec") or {}
                if spec.get("name") == "etc.smart.device.type.car.charger.led.config.brightness":
                    self.led_device_id = str(dev.get("id"))
                    self.led_device_uuid = str(dev.get("uuid"))
                    return

        raise RuntimeError("LED controller smartdevice not found on this service location")

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
                raise RuntimeError(f"Request failed {resp.status} ({method} {url}): {text}")
            if return_json:
                if resp.content_length == 0:
                    return None
                return await resp.json()
            return None

    # ------------------------------------------------------------------
    # COMMANDS (write actions)
    # ------------------------------------------------------------------
    async def set_charging_mode(self, mode: str, limit: int | None = None) -> bool:
        _LOGGER.debug("Setting charging mode: %s, limit: %s", mode, limit)
        if mode == "NORMAL":
            mode = "STANDARD"
        if mode in ("SMART", "SOLAR", "STANDARD"):
            url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setChargingMode"
            payload: list[dict[str, Any]] = [
                {"spec": {"name": "mode", "species": "String"}, "value": mode}
            ]
        else:
            _LOGGER.warning("Unsupported charging mode: %s", mode)
            return False
        await self._request("POST", url, json=payload, expected=(200, 204))
        _LOGGER.debug("Charging mode set successfully")
        return True

    async def start_charging(
        self, current: int, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[int, int]:
        """Start charging at (clamped) amperage.

        Returns (current_amps, percentage_limit) so caller can update ConnectorState.
        """
        # _request handles auth
        if max_current < min_current:
            min_current, max_current = max_current, min_current

        if max_current == min_current:
            target = int(min_current)
            percentage = 100
        else:
            target = max(min_current, min(int(current), max_current))
            rng = max_current - min_current
            percentage = int(max(0, min(100, round(((target - min_current) * 100.0) / rng))))

        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/startCharging"
        payload = [{"spec": {"name": "percentageLimit", "species": "Integer"}, "value": percentage}]
        await self._request("POST", url, json=payload, expected=(200,))

        _LOGGER.debug(
            "Started charging successfully (target=%s A, pct=%s, range=%s-%s)",
            target,
            percentage,
            min_current,
            max_current,
        )
        return target, percentage

    async def pause_charging(self) -> None:
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/pauseCharging"
        await self._request("POST", url, json=[], expected=(200,))
        _LOGGER.debug("Paused charging successfully")

    async def stop_charging(self) -> None:
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/stopCharging"
        await self._request("POST", url, json=[], expected=(200,))
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
        await self._request("POST", url, json=payload, expected=(200,))
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
        await self._request("PATCH", url, json=payload, expected=(200,))
        _LOGGER.info("min.surpluspct set successfully to %d%%", min_surpluspct)

    async def set_percentage_limit(
        self, percentage: int, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[int, int]:
        """Set percentage limit; returns (current_amps, percentage)."""
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setPercentageLimit"
        payload = [{"spec": {"name": "percentageLimit", "species": "Integer"}, "value": percentage}]
        await self._request("POST", url, json=payload, expected=(200,))
        _LOGGER.debug("Set percentage limit successfully to %d%%", percentage)

        if max_current <= min_current:
            cur_int = int(min_current)
        else:
            rng = max_current - min_current
            cur_float = min_current + (float(percentage) / 100.0) * rng
            cur_int = max(min_current, min(max_current, int(round(cur_float))))
        return cur_int, int(percentage)

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
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/meteringconfiguration"
        try:
            async with self._session.get(
                url, headers=self.auth_headers(), timeout=self._timeout
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.warning(
                        "Metering configuration fetch failed (status %s): %s",
                        resp.status,
                        text,
                    )
                    return None
                return await resp.json()
        except (TimeoutError, aiohttp.ClientError) as exc:
            _LOGGER.warning("Metering configuration fetch failed: %s", exc)
            return None
