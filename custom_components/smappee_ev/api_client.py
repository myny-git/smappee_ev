from __future__ import annotations

import logging
from typing import Optional, Callable, Set

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
        connector_number: Optional[int] = None,
        is_station: bool = False,
    ):
        self.oauth_client = oauth_client
        self.serial = serial
        self.smart_device_uuid = smart_device_uuid
        self.smart_device_id = smart_device_id
        self.service_location_id = service_location_id
        self.connector_number = connector_number
        self.is_station = is_station
        self._session: ClientSession = session
        self._timeout = ClientTimeout(connect=5, total=15)

        # local knobs used by commands
        self.selected_mode = "NORMAL"
        self.min_current = 6
        self.max_current = 32
        self.selected_current_limit: Optional[int] = None
        self.selected_percentage_limit: Optional[int] = None

        # callbacks kept only if you still wire them somewhere
        self._callbacks: Set[Callable[[], None]] = set()

        _LOGGER.info(
            "SmappeeApiClient initialized (serial=%s, connector=%s, station=%s)",
            self.serial,
            self.connector_number,
            self.is_station,
        )


    # ---- small helpers the coordinator uses to fetch state ----
    async def ensure_auth(self) -> None:
        await self.oauth_client.ensure_token_valid()

    def auth_headers(self) -> dict:
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
    # COMMANDS (write actions)
    # ------------------------------------------------------------------
    async def set_charging_mode(self, mode: str, limit: Optional[int] = None) -> bool:
        await self.ensure_auth()
        _LOGGER.debug("Setting charging mode: %s, limit: %s", mode, limit)

        # Build URL/payload/method
        if mode in ("SMART", "SOLAR"):
            url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setChargingMode"
            payload = [{"spec": {"name": "mode", "species": "String"}, "value": mode}]
            method = self._session.post

        elif mode == "NORMAL":
            if self.connector_number is None:
                raise ValueError("connector_number is required for NORMAL mode")

            # Clamp limit to safe range if provided (or fall back to min_current)
            min_c = getattr(self, "min_current", 6)
            max_c = getattr(self, "max_current", 32)
            use_limit = limit if limit is not None else min_c
            use_limit = max(min_c, min(int(use_limit), max_c))
            
            url = f"{BASE_URL}/chargingstations/{self.serial}/connectors/{self.connector_number}/mode"
            payload = {"mode": mode, "limit": {"unit": "AMPERE", "value": use_limit}}
            method = self._session.put
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

        # Update local mirrors so UI feels instant
        self.selected_mode = mode
        if mode == "NORMAL":
            # If caller passed a limit, mirror it; otherwise we used min_current
            self.selected_current_limit = use_limit

        return True

    async def start_charging(self, current: int) -> None:
        """Convert amps -> percentage and call action."""
        if self.max_current == self.min_current:
            raise ValueError(f"Invalid current range: {self.min_current} == {self.max_current}")
        if not (self.min_current <= current <= self.max_current):
            raise ValueError(f"{current}A out of range {self.min_current}-{self.max_current}")

        rng = self.max_current - self.min_current
        percentage = max(0, min(round(((current - self.min_current) / rng) * 100), 100))
        self.selected_current_limit = current
        self.selected_percentage_limit = percentage

        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/startCharging"
        payload = [{"spec": {"name": "percentageLimit", "species": "Integer"}, "value": percentage}]
        resp = await self._session.post(url, json=payload, headers=self.auth_headers(), timeout=self._timeout)
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"start_charging failed: {text}")
        _LOGGER.debug("Started charging successfully")


    async def pause_charging(self) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/pauseCharging"
        resp = await self._session.post(url, json=[], headers=self.auth_headers(), timeout=self._timeout)
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"pause_charging failed: {text}")
        _LOGGER.debug("Paused charging successfully")
        self.selected_mode = "NORMAL"

    async def stop_charging(self) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/stopCharging"
        resp = await self._session.post(url, json=[], headers=self.auth_headers(), timeout=self._timeout)
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"stop_charging failed: {text}")
        _LOGGER.debug("Stopped charging successfully")

    async def set_brightness(self, brightness: int) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.serial}/actions/setBrightness"
        payload = [{
            "spec": {
                "name": "etc.smart.device.type.car.charger.led.config.brightness",
                "species": "Integer",
            },
            "value": brightness,
        }]
        resp = await self._session.post(url, json=payload, headers=self.auth_headers(), timeout=self._timeout)
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"set_brightness failed: {text}")
        _LOGGER.info("LED brightness set successfully to %d%%", brightness)

    async def set_min_surpluspct(self, min_surpluspct: int) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_id}"
        payload = {
            "configurationProperties": [{
                "spec": {
                    "name": "etc.smart.device.type.car.charger.config.min.excesspct",
                    "species": "Integer",
                },
                "value": min_surpluspct,
            }]
        }
        resp = await self._session.patch(url, json=payload, headers=self.auth_headers(), timeout=self._timeout)
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"set_min_surpluspct failed: {text}")
        _LOGGER.info("min.surpluspct set successfully to %d%%", min_surpluspct)

    async def set_percentage_limit(self, percentage: int) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.smart_device_uuid}/actions/setPercentageLimit"
        payload = [{"spec": {"name": "percentageLimit", "species": "Integer"}, "value": percentage}]
        resp = await self._session.post(url, json=payload, headers=self.auth_headers(), timeout=self._timeout)
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"set_percentage_limit failed: {text}")
        _LOGGER.debug("Set percentage limit successfully to %d%%", percentage)
        self.selected_percentage_limit = percentage

    async def set_available(self) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.serial}/actions/setAvailable"
        resp = await self._session.post(url, json=[], headers=self.auth_headers(), timeout=self._timeout)
        if resp.status not in (0, 200):
            text = await resp.text()
            raise RuntimeError(f"set_available failed: {text}")
        _LOGGER.debug("Set charger available successfully")

    async def set_unavailable(self) -> None:
        await self.ensure_auth()
        url = f"{BASE_URL}/servicelocation/{self.service_location_id}/smartdevices/{self.serial}/actions/setUnavailable"
        resp = await self._session.post(url, json=[], headers=self.auth_headers(), timeout=self._timeout)
        if resp.status not in (0, 200):
            text = await resp.text()
            raise RuntimeError(f"set_unavailable failed: {text}")
        _LOGGER.debug("Set charger unavailable successfully")