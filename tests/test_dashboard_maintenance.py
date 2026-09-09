"""Explicit maintenance detection, setup retries, and transition logging."""

import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.translation import async_get_translations
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smappee_ev import async_setup_entry
from custom_components.smappee_ev.api.dashboard_client import SmappeeDashboardClient
from custom_components.smappee_ev.api.errors import (
    SmappeeConnectionError,
    SmappeeMaintenanceError,
    SmappeeProtocolError,
    SmappeeServerError,
    SmappeeTransientError,
)
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.dashboard_discovery import (
    _dashboard_fetch_highlevel_configs,
    _fetch_dashboard_connector_mapping,
    _load_dashboard_topologies,
)
from tests.factories import make_site_runtime
from tests.test_dashboard_client import _Response, _Session

NOTICE = "<html>The Smappee dashboard is currently under maintenance.</html>"
MESSAGE = "Smappee Dashboard under maintenance. Home Assistant will retry automatically."


def make_client(session, *, refresh=False):
    return SmappeeDashboardClient(
        username="test-user",
        password="test-password",  # noqa: S106 - synthetic test credential
        refresh_token="test-refresh" if refresh else None,
        session=session,
        token_update_callback=MagicMock(),
    )


@pytest.mark.parametrize("status", [200, 502, 503])
@pytest.mark.parametrize("refresh", [False, True], ids=["login", "refresh"])
async def test_maintenance_precedes_json_and_preserves_credentials(status, refresh, caplog):
    body = NOTICE.upper() + "private-html-marker"
    response = _Response(status, text=body, json_exc=AssertionError("JSON must not be parsed"))
    session = _Session(posts=[response, response])
    client = make_client(session, refresh=refresh)
    caplog.set_level(logging.DEBUG)
    for _ in range(2):
        with pytest.raises(SmappeeMaintenanceError, match="under maintenance") as caught:
            await client.async_ensure_auth()
        assert not isinstance(caught.value, ConfigEntryAuthFailed)
    assert len(session.post_calls) == 2  # No password fallback during refresh maintenance.
    assert all(("refreshToken" in url) == refresh for url, _ in session.post_calls)
    assert client.username == "test-user"
    assert client.password == "test-password"  # noqa: S105 - synthetic test credential
    assert client.refresh_token == ("test-refresh" if refresh else None)
    client._token_update_callback.assert_not_called()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    for sensitive in (body, "private-html-marker", "test-user", "test-password", "test-refresh"):
        assert sensitive not in caplog.text


@pytest.mark.parametrize("status", [502, 503])
@pytest.mark.parametrize("refresh", [False, True], ids=["login", "refresh"])
async def test_generic_server_error_is_not_maintenance(status, refresh, caplog):
    client = make_client(
        _Session(posts=[_Response(status, text="<html>Bad Gateway</html>")]), refresh=refresh
    )
    authenticate = client.async_refresh if refresh else client.async_login
    with pytest.raises(SmappeeServerError, match=str(status)):
        await authenticate()
    assert "maintenance" not in caplog.text.lower()
    client._token_update_callback.assert_not_called()


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("refresh", [False, True], ids=["login", "refresh"])
async def test_real_authentication_errors_still_raise(status, refresh):
    client = make_client(_Session(posts=[_Response(status, text="Unauthorized")]), refresh=refresh)
    if refresh:
        with pytest.raises(ConfigEntryAuthFailed):
            await client.async_refresh()
    else:
        with pytest.raises(ConfigEntryAuthFailed):
            await client.async_ensure_auth()


async def test_generic_login_502_reaches_setup_as_temporary_outage():
    client = make_client(_Session(posts=[_Response(502, text="Bad Gateway")]))
    with pytest.raises(SmappeeServerError):
        await client.async_ensure_auth()


@pytest.mark.parametrize("refresh", [False, True], ids=["login", "refresh"])
async def test_recovery_requires_successful_authentication_and_logs_once(refresh, caplog):
    session = _Session(
        posts=[
            _Response(502, text=NOTICE),
            _Response(200, payload={}),
            _Response(
                200,
                payload={
                    "token": "access",
                    "tokenExpirationTimestamp": int(time.time() * 1000) + 300_000,
                },
            ),
            _Response(502, text=NOTICE),
        ]
    )
    client = make_client(session, refresh=refresh)
    authenticate = client.async_refresh if refresh else client.async_login
    caplog.set_level(logging.INFO)
    with pytest.raises(SmappeeMaintenanceError):
        await authenticate()
    with pytest.raises(SmappeeProtocolError):
        await authenticate()
    assert "recovered" not in caplog.text
    assert await authenticate() is True
    assert await client.async_ensure_auth() is True
    assert len([r for r in caplog.records if "recovered" in r.message]) == 1
    with pytest.raises(SmappeeMaintenanceError):
        await authenticate()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


@pytest.mark.parametrize(
    "error",
    [SmappeeMaintenanceError, SmappeeTransientError, SmappeeConnectionError, SmappeeServerError],
)
async def test_optional_discovery_does_not_swallow_outage(error):
    client = MagicMock()
    client.username = "user"
    client.password = "password"  # noqa: S105 - synthetic test credential
    for method in (
        "async_get_highlevel_configuration",
        "async_get_charging_station_details",
        "async_get_service_locations_full_details",
    ):
        setattr(client, method, AsyncMock(side_effect=error("outage")))
    with pytest.raises(error):
        await _dashboard_fetch_highlevel_configs(client, [100])
    with pytest.raises(error):
        await _fetch_dashboard_connector_mapping(client, [{"serialNumber": "station"}])
    with pytest.raises(error):
        await _load_dashboard_topologies(client)


async def test_setup_retry_reason_survives_client_recreation_and_recovers(
    hass, caplog, enable_custom_integrations
):
    entry = MockConfigEntry(
        domain=DOMAIN, version=6, data={"username": "user", "password": "password"}
    )
    entry.add_to_hass(hass)
    await async_get_translations(hass, "en", "exceptions", {DOMAIN})
    session = _Session(
        posts=[
            _Response(502, text=NOTICE),
            _Response(200, text=NOTICE),
            _Response(
                200,
                payload={
                    "token": "access",
                    "tokenExpirationTimestamp": int(time.time() * 1000) + 300_000,
                },
            ),
        ],
        requests=[_Response(200, payload=[])],
    )
    caplog.set_level(logging.INFO)
    data_before = dict(entry.data)
    with (
        patch("custom_components.smappee_ev.async_get_clientsession", return_value=session),
        patch(
            "custom_components.smappee_ev.dashboard_discovery.build_topologies_from_full_details",
            return_value=[MagicMock(site_location_id=100)],
        ),
        patch(
            "custom_components.smappee_ev._prepare_site_topologies",
            new=AsyncMock(return_value=(make_site_runtime(site_location_id=100), None)),
        ),
        patch("custom_components.smappee_ev._register_runtime_devices") as register_devices,
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()) as forward,
        patch.object(entry, "async_start_reauth_if_available") as reauth,
    ):
        for _ in range(2):
            async with entry.setup_lock:
                await entry.async_setup(hass)
            assert entry.state is ConfigEntryState.SETUP_RETRY
            assert entry.error_reason_translation_key == "dashboard_maintenance"
            assert entry.reason == MESSAGE
            assert entry._async_cancel_retry_setup is not None
            assert entry.data == data_before
            register_devices.assert_not_called()
            forward.assert_not_awaited()
            reauth.assert_not_called()
        assert (
            len(
                [
                    r
                    for r in caplog.records
                    if r.levelno == logging.WARNING and "under maintenance" in r.message
                ]
            )
            == 1
        )
        async with entry.setup_lock:
            await entry.async_setup(hass)
        assert entry.state is ConfigEntryState.LOADED
        assert entry.error_reason_translation_key is None
        assert entry._async_cancel_retry_setup is None
        assert entry.data == data_before
        reauth.assert_not_called()
        forward.assert_awaited_once()
    assert len([r for r in caplog.records if "recovered from maintenance" in r.message]) == 1


async def test_setup_converts_maintenance_during_site_preparation(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, version=6, data={"username": "user", "password": "password"}
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.smappee_ev._load_dashboard_topologies",
            new=AsyncMock(return_value=[SimpleNamespace(site_location_id=100)]),
        ),
        patch(
            "custom_components.smappee_ev._prepare_site_topologies",
            new=AsyncMock(side_effect=SmappeeMaintenanceError("maintenance")),
        ),
        patch(
            "custom_components.smappee_ev._async_shutdown_runtime_resources", new=AsyncMock()
        ) as cleanup,
    ):
        with pytest.raises(ConfigEntryNotReady) as caught:
            await async_setup_entry(hass, entry)
        assert caught.value.translation_domain == DOMAIN
        assert caught.value.translation_key == "dashboard_maintenance"
        cleanup.assert_awaited_once()
