import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed
import pytest

from custom_components.smappee_ev.dashboard_client import SmappeeDashboardClient


class _Response:
    def __init__(
        self,
        status: int,
        payload=None,
        *,
        text: str = "",
        content_length: int | None = 1,
        json_exc: Exception | None = None,
    ):
        self.status = status
        self._payload = payload
        self._text = text
        self.content_length = content_length
        self._json_exc = json_exc

    async def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._payload

    async def text(self):
        return self._text


class _ResponseContext:
    def __init__(self, response: _Response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Session:
    def __init__(self, *, posts=None, requests=None):
        self.posts = list(posts or [])
        self.requests = list(requests or [])
        self.post_calls = []
        self.request_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _ResponseContext(self.posts.pop(0))

    def request(self, method, url, **kwargs):
        self.request_calls.append((method, url, kwargs))
        return _ResponseContext(self.requests.pop(0))


def _client(session=None, **kwargs) -> SmappeeDashboardClient:
    return SmappeeDashboardClient(
        username=kwargs.pop("username", None),
        password=kwargs.pop("password", None),
        refresh_token=kwargs.pop("refresh_token", None),
        session=session or MagicMock(),
        token_update_callback=kwargs.pop("token_update_callback", MagicMock()),
    )


def test_dashboard_token_update_handles_missing_and_invalid_expiration():
    token_callback = MagicMock()
    client = _client(token_update_callback=token_callback)

    client._update_token_data(
        {"token": "access", "refreshToken": "refresh", "tokenExpirationTimestamp": "bad"}
    )

    assert client._token == "access"  # noqa: S105 - fake test token
    assert client.refresh_token == "refresh"  # noqa: S105 - fake test token
    assert client._token_expires_at_ms == 0
    token_callback.assert_called_once_with({"dashboard_refresh_token": "refresh"})
    assert client._headers() == {"token": "access", "content-type": "application/json"}

    client._update_token_data({"token": "", "tokenExpirationTimestamp": 1234})
    assert client._token is None
    assert client._token_expires_at_ms == 1234


@pytest.mark.asyncio
async def test_set_capacity_protection_uses_one_decimal_kw_payload():
    """Capacity protection writes use one decimal kW."""
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock()

    await client.async_set_capacity_protection(236259, True, 5.06)

    client._request.assert_awaited_once_with(
        "PUT",
        "v10/servicelocation/236259/homecontrol/smart/capacityprotection",
        json={
            "locationId": 236259,
            "active": True,
            "capacityMaximumPower": 5.1,
            "capacitySuggestedPower": 0,
        },
        expected=(200, 204),
    )


@pytest.mark.asyncio
async def test_set_overload_protection_uses_confirmed_patch_payload():
    """Overload writes use the Dashboard PATCH payload confirmed from manual testing."""
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock()

    await client.async_set_overload_protection(236259, True, 25)

    client._request.assert_awaited_once_with(
        "PATCH",
        "v10/servicelocation/236259/homecontrol/smart/overloadprotection",
        json={"active": True, "maximumLoad": 25},
        expected=(200, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_charging_mode_action_payload():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_charging_mode(
        236259, "CARCHARGER-acchargingcontroller-123", "smart"
    )

    assert ok is True
    client._request.assert_awaited_once_with(
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/CARCHARGER-acchargingcontroller-123/actions/setChargingMode",
        json=[
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
                "values": [{"String": "SMART"}],
            }
        ],
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_solar_mode_does_not_write_min_surplus():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_charging_mode(
        236259, "CARCHARGER-acchargingcontroller-123", "solar"
    )

    assert ok is True
    client._request.assert_awaited_once()
    assert client._request.await_args.args == (
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/CARCHARGER-acchargingcontroller-123/actions/setChargingMode",
    )


@pytest.mark.asyncio
async def test_dashboard_start_charging_action_payload():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_start_charging(236259, "CARCHARGER-acchargingcontroller-123", 40)

    assert ok is True
    client._request.assert_awaited_once_with(
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/CARCHARGER-acchargingcontroller-123/actions/startCharging",
        json=[
            {
                "spec": {
                    "name": "percentageLimit",
                    "species": "Integer",
                    "unit": "%",
                    "required": True,
                },
                "values": [{"Integer": 40}],
            }
        ],
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_percentage_limit_action_payload_and_false_result():
    client = _client()
    client._request = AsyncMock(side_effect=[True, None])

    assert await client.async_set_percentage_limit(236259, "device-1", "75") is True
    assert await client.async_execute_device_action(236259, "device-1", "custom", []) is False

    first_call = client._request.await_args_list[0]
    assert first_call.args == (
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1/actions/setPercentageLimit",
    )
    assert first_call.kwargs["json"][0]["values"] == [{"Integer": 75}]


@pytest.mark.asyncio
async def test_dashboard_led_brightness_configuration_patch_payload():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_led_brightness(236259, "LED-controller-123", 42)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v10/servicelocation/236259/homecontrol/smart/devices/LED-controller-123",
        json={
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.led.config.brightness"},
                    "values": [{"Integer": 42}],
                }
            ]
        },
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_min_surplus_configuration_patch_payload():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_min_surpluspct(236259, "device-1", 84)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1",
        json={
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.min.excesspct"},
                    "values": [{"Integer": 84}],
                }
            ]
        },
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_connector_max_current_configuration_patch_payload():
    """Connector max current writes use the Dashboard smart device config PATCH."""
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_connector_max_current(236259, "device-1", 16)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1",
        json={
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.max.current"},
                    "values": [{"Quantity": {"value": 16, "unit": "A"}}],
                }
            ]
        },
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_pause_and_stop_action_payloads():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    assert await client.async_pause_charging(236259, "device-1")
    assert await client.async_stop_charging(236259, "device-1")

    client._request.assert_any_await(
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1/actions/pauseCharging",
        json=[],
        expected=(200, 201, 204),
    )
    client._request.assert_any_await(
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1/actions/stopCharging",
        json=[],
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_smart_devices_uses_v10_homecontrol_endpoint():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=[{"id": "device-1"}])

    devices = await client.async_get_smart_devices(236259)

    assert devices == [{"id": "device-1"}]
    client._request.assert_awaited_once_with(
        "GET",
        "v10/servicelocation/236259/homecontrol/smart/devices",
        params={"excludedCategories": ""},
        return_json=True,
    )


@pytest.mark.asyncio
async def test_dashboard_recent_sessions_uses_v10_range_mode():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=[{"id": "session-1"}])

    sessions = await client.async_get_recent_sessions("STATIONSERIAL")

    assert sessions == [{"id": "session-1"}]
    method, path = client._request.await_args.args
    kwargs = client._request.await_args.kwargs
    assert method == "GET"
    assert path == "v10/chargingstations/STATIONSERIAL/sessions"
    assert kwargs["params"]["rangeMode"] == "stop_or_start"
    assert "," in kwargs["params"]["range"]
    assert kwargs["return_json"] is True


@pytest.mark.asyncio
async def test_dashboard_request_reauthenticates_and_retries_after_unauthorized():
    """A stale access token should be refreshed once and the API call retried."""
    token_updates = MagicMock()
    expires_at = int(time.time() * 1000) + 300_000
    session = _Session(
        posts=[
            _Response(
                200,
                {
                    "token": "new-token",
                    "refreshToken": "new-refresh",
                    "tokenExpirationTimestamp": expires_at,
                },
            )
        ],
        requests=[
            _Response(401, text="expired"),
            _Response(200, {"ok": True}),
        ],
    )
    client = _client(
        session,
        refresh_token="old-refresh",  # noqa: S106 - fake token value for retry behavior
        token_update_callback=token_updates,
    )
    client._token = "old-token"  # noqa: S105 - fake token value for retry behavior
    client._token_expires_at_ms = expires_at

    data = await client._request("GET", "v11/example", return_json=True)

    assert data == {"ok": True}
    assert [call[2]["headers"]["token"] for call in session.request_calls] == [
        "old-token",
        "new-token",
    ]
    token_updates.assert_called_once_with({"dashboard_refresh_token": "new-refresh"})


@pytest.mark.asyncio
async def test_dashboard_request_uses_new_token_if_another_task_refreshed_it():
    expires_at = int(time.time() * 1000) + 300_000
    session = _Session(requests=[_Response(401), _Response(200, {"ok": True})])
    client = _client(session)
    client._token = "old-token"  # noqa: S105 - fake token value for retry behavior
    client._token_expires_at_ms = expires_at

    class MutatingAuthLock:
        async def __aenter__(self):
            client._token = "new-token"  # noqa: S105 - fake token value for retry behavior
            client._token_expires_at_ms = expires_at

        async def __aexit__(self, exc_type, exc, tb):
            return False

    client._auth_lock = MutatingAuthLock()
    client.async_ensure_auth = AsyncMock(return_value=True)
    client.async_refresh = AsyncMock()
    client.async_login = AsyncMock()

    assert await client._request("GET", "v11/example", return_json=True) == {"ok": True}

    client.async_refresh.assert_not_awaited()
    client.async_login.assert_not_awaited()
    assert [call[2]["headers"]["token"] for call in session.request_calls] == [
        "old-token",
        "new-token",
    ]


@pytest.mark.asyncio
async def test_dashboard_request_returns_none_when_auth_unavailable():
    """No credentials means no network request is attempted."""
    session = _Session(requests=[_Response(200, {"ok": True})])
    client = _client(session)

    assert await client._request("GET", "v11/example", return_json=True) is None
    assert session.request_calls == []


@pytest.mark.asyncio
async def test_dashboard_login_rejects_bad_credentials():
    session = _Session(posts=[_Response(401, text="nope")])
    client = _client(session, username="user", password="wrong")  # noqa: S106

    with pytest.raises(ConfigEntryAuthFailed, match="Dashboard credentials rejected"):
        await client.async_login()


@pytest.mark.asyncio
async def test_dashboard_login_and_refresh_handle_bad_payloads_and_statuses():
    login_session = _Session(posts=[_Response(500, text="server error")])
    client = _client(login_session, username="user", password="pass")  # noqa: S106

    with pytest.raises(RuntimeError, match="Dashboard login failed 500"):
        await client.async_login()

    bad_payload = _client(
        _Session(posts=[_Response(200, ["not", "a", "dict"])]),
        username="user",
        password="pass",  # noqa: S106
    )
    assert await bad_payload.async_login() is False

    refresh = _client(
        _Session(posts=[_Response(500, text="no refresh"), _Response(200, ["bad"])]),
        refresh_token="refresh",  # noqa: S106 - fake refresh token
    )
    assert await refresh.async_refresh() is False
    assert await refresh.async_refresh() is False


@pytest.mark.asyncio
async def test_dashboard_ensure_auth_refresh_rejected_falls_back_to_login():
    expires_at = int(time.time() * 1000) + 300_000
    session = _Session(
        posts=[
            _Response(401, text="refresh rejected"),
            _Response(
                200,
                {
                    "token": "login-token",
                    "refreshToken": "login-refresh",
                    "tokenExpirationTimestamp": expires_at,
                },
            ),
        ]
    )
    client = _client(
        session,
        username="user",
        password="pass",  # noqa: S106
        refresh_token="refresh",  # noqa: S106
    )

    assert await client.async_ensure_auth() is True
    assert client._token == "login-token"  # noqa: S105 - fake test token


@pytest.mark.asyncio
async def test_dashboard_request_raises_for_http_error_and_empty_json_body():
    expires_at = int(time.time() * 1000) + 300_000
    error_client = _client(_Session(requests=[_Response(503, text="unavailable")]))
    error_client._token = "token"  # noqa: S105
    error_client._token_expires_at_ms = expires_at

    with pytest.raises(RuntimeError, match="Dashboard request failed 503"):
        await error_client._request("GET", "v11/example", return_json=True)

    empty_client = _client(_Session(requests=[_Response(200, content_length=0)]))
    empty_client._token = "token"  # noqa: S105
    empty_client._token_expires_at_ms = expires_at
    assert await empty_client._request("GET", "v11/example", return_json=True) is None


@pytest.mark.asyncio
async def test_dashboard_request_non_json_body_returns_none():
    expires_at = int(time.time() * 1000) + 300_000
    content_error = aiohttp.ContentTypeError(MagicMock(), ())
    session = _Session(requests=[_Response(200, json_exc=content_error)])
    client = _client(session)
    client._token = "token"  # noqa: S105 - fake token value for auth header
    client._token_expires_at_ms = expires_at

    assert await client._request("GET", "v11/example", return_json=True) is None


@pytest.mark.asyncio
async def test_dashboard_list_and_dict_methods_ignore_malformed_response_shapes():
    client = _client()
    client._request = AsyncMock(side_effect=({"unexpected": "dict"}, ["unexpected-list"]))

    assert await client.async_get_service_locations_full_details() is None
    assert await client.async_get_charging_station_details("SERIAL") is None


@pytest.mark.asyncio
async def test_dashboard_charger_availability_uses_v11_patch():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_charger_availability("STATIONSERIAL", False)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v11/chargingstations/STATIONSERIAL",
        json={"available": False},
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_restart_charging_station_uses_v11_post():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_restart_charging_station("6220017988")

    assert ok is True
    client._request.assert_awaited_once_with(
        "POST",
        "v11/chargingstations/6220017988/restart",
        json={},
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_offline_charging_uses_v11_patch():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_offline_charging("STATIONSERIAL", True, 6)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v11/chargingstations/STATIONSERIAL",
        json={"offlineCharging": {"enabled": True, "failSafe": 6}},
        expected=(200, 201, 204),
    )
