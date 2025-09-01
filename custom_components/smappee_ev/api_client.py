from __future__ import annotations

import logging
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from .const import BASE_URL

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
        self._timeout = ClientTimeout(connect=5, total=15)
        self.station_action_uuid = None
        # Stateless: no per-connector mutable charging attributes kept here.
        # All dynamic values live in Coordinator/ConnectorState.
        self._callbacks = set()  # kept for future hook use

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
    # COMMANDS (write actions)
    # ------------------------------------------------------------------
    async def set_charging_mode(self, mode: str, limit: int | None = None) -> bool:
        await self.ensure_auth()
        _LOGGER.debug("Setting charging mode: %s, limit: %s", mode, limit)

        # Build URL/payload/method
        payload: dict[str, Any] | list[dict[str, Any]]

        if mode == "NORMAL":
            mode = "STANDARD"

        if mode in ("SMART", "SOLAR", "STANDARD"):
            url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setChargingMode"
            payload = [{"spec": {"name": "mode", "species": "String"}, "value": mode}]
            method = self._session.post

        # elif mode == "NORMAL":
        #     if self.connector_number is None:
        #         raise ValueError("connector_number is required for NORMAL mode")

        #     # Clamp limit to safe range if provided (or fall back to min_current)
        #     min_c = getattr(self, "min_current", 6)
        #     max_c = getattr(self, "max_current", 32)
        #     use_limit = limit if limit is not None else min_c
        #     use_limit = max(min_c, min(int(use_limit), max_c))

        #     url = (
        #         f"{BASE_URL}/chargingstations/{self.serial}/connectors/{self.connector_number}/mode"
        #     )
        #     payload = {"mode": mode, "limit": {"unit": "AMPERE", "value": use_limit}}
        #     method = self._session.put
        else:
            _LOGGER.warning("Unsupported charging mode: %s", mode)
            return False

        # Do request
        resp = await method(url, json=payload, headers=self.auth_headers(), timeout=self._timeout)
        if resp.status not in (200, 204):
            text = await resp.text()
            _LOGGER.error("set_charging_mode failed (%s): %s", resp.status, text)
            raise RuntimeError(f"set_charging_mode failed: {text}")

        _LOGGER.debug("Charging mode set successfully")

        return True

    async def start_charging(
        self, current: int, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[int, int]:
        """Start charging at (clamped) amperage.

        Returns (current_amps, percentage_limit) so caller can update ConnectorState.
        """
        await self.ensure_auth()
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
        resp = await self._session.post(
            url, json=payload, headers=self.auth_headers(), timeout=self._timeout
        )
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"start_charging failed: {text}")

        _LOGGER.debug(
            "Started charging successfully (target=%s A, pct=%s, range=%s-%s)",
            target,
            percentage,
            min_current,
            max_current,
        )
        return target, percentage

    async def pause_charging(self) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/pauseCharging"
        resp = await self._session.post(
            url, json=[], headers=self.auth_headers(), timeout=self._timeout
        )
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"pause_charging failed: {text}")
        _LOGGER.debug("Paused charging successfully")

    async def stop_charging(self) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/stopCharging"
        resp = await self._session.post(
            url, json=[], headers=self.auth_headers(), timeout=self._timeout
        )
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"stop_charging failed: {text}")
        _LOGGER.debug("Stopped charging successfully")

    async def set_brightness(self, brightness: int) -> None:
        await self.ensure_auth()

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
        resp = await self._session.post(
            url, json=payload, headers=self.auth_headers(), timeout=self._timeout
        )
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"set_brightness failed: {text}")
        _LOGGER.info("LED brightness set successfully to %d%%", brightness)

    async def set_min_surpluspct(self, min_surpluspct: int) -> None:
        await self.ensure_auth()
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
        resp = await self._session.patch(
            url, json=payload, headers=self.auth_headers(), timeout=self._timeout
        )
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"set_min_surpluspct failed: {text}")
        _LOGGER.info("min.surpluspct set successfully to %d%%", min_surpluspct)

    async def set_percentage_limit(
        self, percentage: int, *, min_current: int = 6, max_current: int = 32
    ) -> tuple[int, int]:
        """Set percentage limit; returns (current_amps, percentage)."""
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setPercentageLimit"
        payload = [{"spec": {"name": "percentageLimit", "species": "Integer"}, "value": percentage}]
        resp = await self._session.post(
            url, json=payload, headers=self.auth_headers(), timeout=self._timeout
        )
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"set_percentage_limit failed: {text}")
        _LOGGER.debug("Set percentage limit successfully to %d%%", percentage)

        if max_current <= min_current:
            cur = int(min_current)
        else:
            rng = max_current - min_current
            cur = min_current + (float(percentage) / 100.0) * rng
            cur = max(min_current, min(max_current, int(round(cur))))
        return int(cur), int(percentage)

    async def set_available(self) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setAvailable"
        resp = await self._session.post(
            url, json=[], headers=self.auth_headers(), timeout=self._timeout
        )

        if resp.status not in (200, 204):
            text = await resp.text()
            raise RuntimeError(f"set_available failed: {text}")
        _LOGGER.debug("Set charger available successfully")

    async def set_unavailable(self) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setUnavailable"
        resp = await self._session.post(
            url, json=[], headers=self.auth_headers(), timeout=self._timeout
        )
        if resp.status not in (200, 204):
            text = await resp.text()
            raise RuntimeError(f"set_unavailable failed: {text}")
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
