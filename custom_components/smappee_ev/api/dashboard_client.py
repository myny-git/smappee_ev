from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import logging
import time
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from ..const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    DASHAPI_URL,
    DASHBOARD_API_URL,
    DOMAIN,
    HTTP_CONNECT_TIMEOUT,
    HTTP_TOTAL_TIMEOUT,
)
from ..models.state import DashboardObject, DashboardObjectList, RecentSession
from .errors import (
    SmappeeAuthenticationError,
    SmappeeConnectionError,
    SmappeeError,
    SmappeeMaintenanceError,
    SmappeeProtocolError,
    SmappeeRateLimitError,
    SmappeeServerError,
)

_LOGGER = logging.getLogger(__name__)
_TOKEN_RENEW_SKEW_MS = 60_000
_MAINTENANCE_TEXT = "the smappee dashboard is currently under maintenance."


@dataclass
class DashboardMaintenanceState:
    """Remember maintenance and retry deadlines across client recreation."""

    active: bool = False
    retry_after_monotonic: float = 0.0

    def check_retry_deadline(self) -> None:
        remaining = self.retry_after_monotonic - time.monotonic()
        if remaining > 0:
            raise SmappeeRateLimitError(remaining)

    def check_response(self, text: str) -> None:
        """Recognize only the explicit maintenance notice, without logging its body."""
        if _MAINTENANCE_TEXT not in text.casefold():
            return
        if not self.active:
            self.active = True
            _LOGGER.warning("Smappee Dashboard under maintenance; will retry automatically")
        raise SmappeeMaintenanceError("Smappee Dashboard under maintenance")

    def authentication_succeeded(self) -> None:
        """Report recovery only after authentication actually succeeds."""
        if self.active:
            self.active = False
            _LOGGER.info("Smappee Dashboard recovered from maintenance")


def _retry_after_seconds(value: object) -> float:
    """Accept Retry-After delay-seconds or HTTP-date; use 30s when absent/invalid."""
    if not isinstance(value, str):
        return 30.0
    value = value.strip()
    try:
        if value.isascii() and value.isdecimal():
            return float(int(value))
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            return 30.0
        return max(0.0, (deadline - datetime.now(UTC)).total_seconds())
    except ValueError, TypeError, OverflowError:
        return 30.0


class SmappeeDashboardClient:
    """Optional client for Smappee Dashboard v10/v11 endpoints."""

    def __init__(
        self,
        *,
        username: str | None,
        password: str | None,
        refresh_token: str | None,
        session: ClientSession,
        token_update_callback: Callable[[dict[str, str]], None],
        maintenance_state: DashboardMaintenanceState | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.refresh_token = refresh_token
        self._session = session
        self._token_update_callback = token_update_callback
        self._timeout = ClientTimeout(connect=HTTP_CONNECT_TIMEOUT, total=HTTP_TOTAL_TIMEOUT)
        self._token: str | None = None
        self._token_expires_at_ms = 0
        self._auth_lock = asyncio.Lock()
        self._missing_credentials_logged = False
        self.monitoring_only = False
        self._maintenance_state = maintenance_state or DashboardMaintenanceState()

    def _token_valid(self) -> bool:
        return bool(
            self._token
            and self._token_expires_at_ms > int(time.time() * 1000) + _TOKEN_RENEW_SKEW_MS
        )

    def _update_token_data(self, data: DashboardObject) -> None:
        token = data.get("token")
        refresh_token = data.get("refreshToken")
        expires_at = data.get("tokenExpirationTimestamp")
        self._token = str(token) if token else None
        if refresh_token:
            self.refresh_token = str(refresh_token)
            self._token_update_callback({CONF_DASHBOARD_REFRESH_TOKEN: self.refresh_token})
        if expires_at is not None:
            with suppress(TypeError, ValueError):
                self._token_expires_at_ms = int(expires_at)

    async def async_login(self) -> bool:
        """Authenticate with dashboard username/password."""
        if not self.username or not self.password:
            return False
        self._maintenance_state.check_retry_deadline()

        async with self._response(
            self._session.post(
                f"{DASHAPI_URL}/login",
                json={"userName": self.username, "password": self.password},
                timeout=self._timeout,
            )
        ) as resp:
            text = await resp.text()
            self._maintenance_state.check_response(text)
            if resp.status in (401, 403):
                raise SmappeeAuthenticationError("Dashboard credentials rejected")
            if resp.status != 200:
                raise SmappeeProtocolError(f"Dashboard login failed {resp.status}")
            data = await resp.json()
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("token"), str)
            or not data["token"].strip()
        ):
            raise SmappeeProtocolError("Dashboard authentication response has no valid token")
        self._update_token_data(data)
        if self._token:
            self._maintenance_state.authentication_succeeded()
        return bool(self._token)

    async def async_refresh(self) -> bool:
        """Refresh the dashboard access token."""
        if not self.refresh_token:
            return False
        self._maintenance_state.check_retry_deadline()

        async with self._response(
            self._session.post(
                f"{DASHAPI_URL}/refreshToken",
                json={"refreshToken": self.refresh_token, "language": "nl"},
                timeout=self._timeout,
            )
        ) as resp:
            self._maintenance_state.check_response(await resp.text())
            if resp.status in (401, 403):
                raise SmappeeAuthenticationError("Dashboard refresh token rejected")
            if resp.status != 200:
                raise SmappeeProtocolError(f"Dashboard refresh failed {resp.status}")
            data = await resp.json()
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("token"), str)
            or not data["token"].strip()
        ):
            raise SmappeeProtocolError("Dashboard authentication response has no valid token")
        self._update_token_data(data)
        if self._token:
            self._maintenance_state.authentication_succeeded()
        return bool(self._token)

    async def async_ensure_auth(self) -> bool:
        """Return True when dashboard auth is available."""
        self._maintenance_state.check_retry_deadline()
        if self._token_valid():
            return True

        async with self._auth_lock:
            if self._token_valid():
                return True

            try:
                if self.refresh_token:
                    try:
                        if await self.async_refresh():
                            return True
                    except ConfigEntryAuthFailed:
                        if not (self.username and self.password):
                            raise
                        _LOGGER.debug(
                            "Dashboard refresh token rejected; trying username/password login"
                        )
                if await self.async_login():
                    return True
            except (
                ConfigEntryAuthFailed,
                SmappeeError,
            ):
                raise
            except (aiohttp.ClientError, TimeoutError) as err:
                raise SmappeeConnectionError("Dashboard authentication connection failed") from err
            except (RuntimeError, ValueError) as err:
                raise SmappeeProtocolError("Unexpected Dashboard authentication failure") from err

        if not self._missing_credentials_logged:
            _LOGGER.warning(
                "Dashboard API disabled: no valid dashboard refresh token or password available"
            )
            self._missing_credentials_logged = True
        return False

    def _headers(self) -> dict[str, str]:
        return {"token": str(self._token), "content-type": "application/json"}

    @asynccontextmanager
    async def _response(self, context: Any) -> AsyncIterator[Any]:
        """Classify outages, including failures while entering/reading a response."""
        try:
            async with context as response:
                self._maintenance_state.check_response(await response.text())
                if response.status == 429:
                    delay = _retry_after_seconds(
                        getattr(response, "headers", {}).get("Retry-After")
                    )
                    self._maintenance_state.retry_after_monotonic = max(
                        self._maintenance_state.retry_after_monotonic, time.monotonic() + delay
                    )
                    raise SmappeeRateLimitError(delay)
                if response.status == 408:
                    raise SmappeeConnectionError("Dashboard request timed out (HTTP 408)")
                if response.status >= 500:
                    raise SmappeeServerError(f"Dashboard unavailable (HTTP {response.status})")
                yield response
        except ValueError as err:
            raise SmappeeProtocolError("Dashboard returned invalid JSON") from err
        except aiohttp.ContentTypeError as err:
            raise SmappeeProtocolError("Dashboard returned an unexpected content type") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SmappeeConnectionError("Dashboard connection failed") from err

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: DashboardObject | None = None,
        expected: tuple[int, ...] = (200,),
        return_json: bool = False,
        retry_auth: bool = True,
    ) -> Any | None:
        """Run an authenticated Dashboard API request."""
        if self.monitoring_only and method.upper() != "GET":
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="mqtt_only")
        if not await self.async_ensure_auth():
            if method.upper() != "GET":
                raise SmappeeConnectionError("Dashboard authentication unavailable")
            return None

        url = f"{DASHBOARD_API_URL}/{path.lstrip('/')}"
        failed_token = self._token
        try:
            response_context = self._session.request(
                method,
                url,
                json=json,
                params=params,
                headers={"token": str(failed_token), "content-type": "application/json"},
                timeout=self._timeout,
            )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SmappeeConnectionError(
                f"Dashboard request failed ({method} {url}): {err}"
            ) from err

        async with self._response(response_context) as resp:
            if resp.status in (401, 403) and retry_auth:
                async with self._auth_lock:
                    if self._token and self._token != failed_token and self._token_valid():
                        refreshed = True
                    else:
                        self._token = None
                        refreshed = False
                        if self.refresh_token:
                            try:
                                refreshed = await self.async_refresh()
                            except ConfigEntryAuthFailed:
                                if not (self.username and self.password):
                                    raise
                        if not refreshed:
                            refreshed = await self.async_login()
                if refreshed:
                    return await self._request(
                        method,
                        path,
                        json=json,
                        params=params,
                        expected=expected,
                        return_json=return_json,
                        retry_auth=False,
                    )
            if resp.status in (401, 403):
                raise SmappeeAuthenticationError("Dashboard authorization failed")
            if resp.status not in expected:
                raise SmappeeProtocolError(
                    f"Dashboard request failed (HTTP {resp.status}, {method})"
                )
            if not return_json:
                return True
            if resp.content_length == 0:
                return None
            with suppress(aiohttp.ContentTypeError, ValueError):
                return await resp.json()
            return None

    async def async_get_service_locations_full_details(self) -> DashboardObjectList | None:
        data = await self._request(
            "GET", "v11/user/servicelocations?fullDetails=true", return_json=True
        )
        return data if isinstance(data, list) else None

    async def async_get_highlevel_configuration(
        self, service_location_id: int | str
    ) -> DashboardObject | None:
        data = await self._request(
            "GET",
            f"v10/servicelocation/{service_location_id}/highlevelconfiguration",
            return_json=True,
        )
        return data if isinstance(data, dict) else None

    async def async_get_appliances(
        self, service_location_id: int | str
    ) -> DashboardObjectList | None:
        data = await self._request(
            "GET", f"v10/servicelocation/{service_location_id}/appliances", return_json=True
        )
        return data if isinstance(data, list) else None

    async def async_get_charging_station_details(self, serial: str) -> DashboardObject | None:
        data = await self._request(
            "GET", f"v10/chargingstations/{serial}?includeDetails=true", return_json=True
        )
        return data if isinstance(data, dict) else None

    async def async_get_smart_devices(
        self, service_location_id: int | str
    ) -> DashboardObjectList | None:
        data = await self._request(
            "GET",
            f"v10/servicelocation/{service_location_id}/homecontrol/smart/devices",
            params={"excludedCategories": ""},
            return_json=True,
        )
        return data if isinstance(data, list) else None

    async def async_get_load_management(
        self, service_location_id: int | str, device_id: str
    ) -> DashboardObject | None:
        data = await self._request(
            "GET",
            f"v10/servicelocation/{service_location_id}/homecontrol/smart/devices/{device_id}/loadmanagement",
            return_json=True,
        )
        return data if isinstance(data, dict) else None

    async def async_get_recent_sessions(self, serial: str) -> list[RecentSession]:
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - (7 * 24 * 60 * 60 * 1000)
        data = await self._request(
            "GET",
            f"v10/chargingstations/{serial}/sessions",
            params={"range": f"{from_ms},{now_ms}", "rangeMode": "stop_or_start"},
            return_json=True,
        )
        return data if isinstance(data, list) else []

    async def async_get_capacity_protection(
        self, service_location_id: int | str
    ) -> DashboardObject | None:
        data = await self._request(
            "GET",
            f"v10/servicelocation/{service_location_id}/homecontrol/smart/capacityprotection",
            return_json=True,
        )
        return data if isinstance(data, dict) else None

    async def async_set_capacity_protection(
        self,
        service_location_id: int | str,
        active: bool,
        capacity_maximum_power_kw: float,
    ) -> None:
        capacity_maximum_power_kw = round(float(capacity_maximum_power_kw), 1)
        payload = {
            "locationId": int(service_location_id),
            "active": bool(active),
            "capacityMaximumPower": capacity_maximum_power_kw,
            "capacitySuggestedPower": 0,
        }
        await self._request(
            "PUT",
            f"v10/servicelocation/{service_location_id}/homecontrol/smart/capacityprotection",
            json=payload,
            expected=(200, 204),
        )

    async def async_get_overload_protection(
        self, service_location_id: int | str
    ) -> DashboardObject | None:
        data = await self._request(
            "GET",
            f"v10/servicelocation/{service_location_id}/homecontrol/smart/overloadprotection",
            return_json=True,
        )
        return data if isinstance(data, dict) else None

    async def async_set_overload_protection(
        self,
        service_location_id: int | str,
        active: bool,
        maximum_load_a: int,
    ) -> None:
        payload = {"active": bool(active), "maximumLoad": int(maximum_load_a)}
        await self._request(
            "PATCH",
            f"v10/servicelocation/{service_location_id}/homecontrol/smart/overloadprotection",
            json=payload,
            expected=(200, 204),
        )

    async def async_set_charger_availability(self, serial: str, available: bool) -> bool:
        return bool(
            await self._request(
                "PATCH",
                f"v11/chargingstations/{serial}",
                json={"available": bool(available)},
                expected=(200, 201, 204),
            )
        )

    async def async_restart_charging_station(self, serial: str) -> bool:
        return bool(
            await self._request(
                "POST",
                f"v11/chargingstations/{serial}/restart",
                json={},
                expected=(200, 201, 204),
            )
        )

    async def async_set_offline_charging(
        self, serial: str, enabled: bool, failsafe_amps: int
    ) -> bool:
        return bool(
            await self._request(
                "PATCH",
                f"v11/chargingstations/{serial}",
                json={
                    "offlineCharging": {
                        "enabled": bool(enabled),
                        "failSafe": int(failsafe_amps),
                    }
                },
                expected=(200, 201, 204),
            )
        )

    async def async_execute_device_action(
        self,
        service_location_id: int | str,
        device_id: str,
        action_name: str,
        payload: DashboardObjectList,
    ) -> bool:
        return bool(
            await self._request(
                "POST",
                f"v10/servicelocation/{service_location_id}/homecontrol/smart/devices/{device_id}/actions/{action_name}",
                json=payload,
                expected=(200, 201, 204),
            )
        )

    async def async_update_configuration_property(
        self,
        service_location_id: int | str,
        device_id: str,
        property_name: str,
        value_dict: DashboardObject,
    ) -> bool:
        payload = {
            "configurationProperties": [{"spec": {"name": property_name}, "values": [value_dict]}]
        }
        return bool(
            await self._request(
                "PATCH",
                f"v10/servicelocation/{service_location_id}/homecontrol/smart/devices/{device_id}",
                json=payload,
                expected=(200, 201, 204),
            )
        )

    @staticmethod
    def _percentage_limit_payload(percentage: int) -> DashboardObjectList:
        """Build the shared ``percentageLimit`` action payload."""
        return [
            {
                "spec": {
                    "name": "percentageLimit",
                    "species": "Integer",
                    "unit": "%",
                    "required": True,
                },
                "values": [{"Integer": int(percentage)}],
            }
        ]

    async def async_set_percentage_limit(
        self, service_location_id: int | str, device_id: str, percentage: int
    ) -> bool:
        return await self.async_execute_device_action(
            service_location_id,
            device_id,
            "setPercentageLimit",
            self._percentage_limit_payload(percentage),
        )

    async def async_set_led_brightness(
        self, service_location_id: int | str, device_id: str, brightness: int
    ) -> bool:
        return await self.async_update_configuration_property(
            service_location_id,
            device_id,
            "etc.smart.device.type.car.charger.led.config.brightness",
            {"Integer": int(brightness)},
        )

    async def async_set_min_surpluspct(
        self, service_location_id: int | str, device_id: str, min_surpluspct: int
    ) -> bool:
        return await self.async_update_configuration_property(
            service_location_id,
            device_id,
            "etc.smart.device.type.car.charger.config.min.excesspct",
            {"Integer": int(min_surpluspct)},
        )

    async def async_set_connector_max_current(
        self, service_location_id: int | str, device_id: str, max_current_a: int
    ) -> bool:
        return await self.async_update_configuration_property(
            service_location_id,
            device_id,
            "etc.smart.device.type.car.charger.config.max.current",
            {"Quantity": {"value": int(max_current_a), "unit": "A"}},
        )

    async def async_start_charging(
        self, service_location_id: int | str, device_id: str, percentage: int = 100
    ) -> bool:
        return await self.async_execute_device_action(
            service_location_id,
            device_id,
            "startCharging",
            self._percentage_limit_payload(percentage),
        )

    async def async_pause_charging(self, service_location_id: int | str, device_id: str) -> bool:
        return await self.async_execute_device_action(
            service_location_id, device_id, "pauseCharging", []
        )

    async def async_stop_charging(self, service_location_id: int | str, device_id: str) -> bool:
        return await self.async_execute_device_action(
            service_location_id,
            device_id,
            "stopCharging",
            [],
        )

    async def async_set_charging_mode(
        self, service_location_id: int | str, device_id: str, mode: str
    ) -> bool:
        mode_up = mode.upper()
        payload = [
            {
                "spec": {
                    "name": "mode",
                    "species": "String",
                    "required": True,
                    "possibleValues": {
                        "values": [
                            {"String": "STANDARD"},
                            {"String": "SMART"},
                            {"String": "SOLAR"},
                        ],
                        "exhaustive": True,
                    },
                },
                "values": [{"String": mode_up}],
            }
        ]
        return await self.async_execute_device_action(
            service_location_id, device_id, "setChargingMode", payload
        )
