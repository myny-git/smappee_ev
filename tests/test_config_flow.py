"""Config flow tests for Dashboard-only Smappee EV auth."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

from custom_components.smappee_ev import config_flow as config_flow_module
from custom_components.smappee_ev.config_flow import SmappeeEvConfigFlow
from custom_components.smappee_ev.const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    CONF_NEEDS_DASHBOARD_REAUTH,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

REQ_KEYS = (CONF_USERNAME, CONF_PASSWORD)


@pytest.fixture(autouse=True)
def _patch_clientsession():
    """Patch async_get_clientsession to avoid real DNS / aiodns on Windows."""
    with patch("custom_components.smappee_ev.config_flow.async_get_clientsession") as mock_func:
        mock_func.return_value = MagicMock()
        yield


def _assert_schema(result):
    ds = result.get("data_schema")
    assert ds is not None
    schema = getattr(ds, "schema", {})
    for key in REQ_KEYS:
        assert key in schema
    assert "client_id" not in schema
    assert "client_secret" not in schema


async def _dashboard_login_success(self):
    self._token_update_callback({CONF_DASHBOARD_REFRESH_TOKEN: "dashboard_refresh"})
    return True


@pytest.mark.asyncio
async def test_user_flow_success(hass):
    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {}  # type: ignore[attr-defined]

    first = await flow.async_step_user()
    assert first.get("type") == FlowResultType.FORM
    assert first.get("step_id") == "user"
    _assert_schema(first)

    with patch(
        "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
        _dashboard_login_success,
    ):
        result = await flow.async_step_user(
            {
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "test_pass",
            }
        )

    assert result.get("type") == FlowResultType.CREATE_ENTRY
    assert result.get("title") == "Smappee EV - test_user"
    data = result.get("data") or {}
    assert data == {
        CONF_USERNAME: "test_user",
        CONF_PASSWORD: "test_pass",
        CONF_DASHBOARD_REFRESH_TOKEN: "dashboard_refresh",
    }


@pytest.mark.asyncio
async def test_user_flow_auth_failed(hass):
    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {}  # type: ignore[attr-defined]

    with patch(
        "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
        side_effect=ConfigEntryAuthFailed("Dashboard credentials rejected"),
    ):
        result = await flow.async_step_user(
            {
                CONF_USERNAME: "bad",
                CONF_PASSWORD: "bad",
            }
        )

    assert result.get("type") == FlowResultType.FORM
    assert (result.get("errors") or {})["base"] == "auth_failed"


@pytest.mark.asyncio
async def test_user_flow_cannot_connect_on_dashboard_api_failure(hass):
    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {}  # type: ignore[attr-defined]

    with patch(
        "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
        side_effect=RuntimeError("Dashboard login failed 503"),
    ):
        result = await flow.async_step_user(
            {
                CONF_USERNAME: "bad",
                CONF_PASSWORD: "bad",
            }
        )

    assert result.get("type") == FlowResultType.FORM
    assert (result.get("errors") or {})["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_user_flow_unknown_on_unexpected_dashboard_response(hass):
    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {}  # type: ignore[attr-defined]

    with patch(
        "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
        return_value=False,
    ):
        result = await flow.async_step_user(
            {
                CONF_USERNAME: "bad",
                CONF_PASSWORD: "bad",
            }
        )

    assert result.get("type") == FlowResultType.FORM
    assert (result.get("errors") or {})["base"] == "unknown"


@pytest.mark.asyncio
async def test_user_flow_unknown_on_unexpected_exception(hass):
    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {}  # type: ignore[attr-defined]

    with (
        patch(
            "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
            side_effect=Exception("malformed response"),
        ),
        patch("custom_components.smappee_ev.config_flow._LOGGER.exception") as mock_exception,
    ):
        result = await flow.async_step_user(
            {
                CONF_USERNAME: "bad",
                CONF_PASSWORD: "bad",
            }
        )

    assert result.get("type") == FlowResultType.FORM
    assert (result.get("errors") or {})["base"] == "unknown"
    mock_exception.assert_called_once_with("Unexpected error during authentication")


@pytest.mark.asyncio
async def test_user_flow_already_configured(hass):
    existing = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "test_user",
            CONF_DASHBOARD_REFRESH_TOKEN: "dashboard_refresh",
        },
        unique_id="smappee_ev:test_user",
    )
    existing.add_to_hass(hass)
    mock_integration(hass, MockModule(DOMAIN), built_in=False)
    mock_platform(hass, f"{DOMAIN}.config_flow", config_flow_module, built_in=False)

    with (
        mock_config_flow(DOMAIN, SmappeeEvConfigFlow),
        patch(
            "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
            _dashboard_login_success,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
            data={
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "test_pass",
            },
        )

    assert result.get("type") == FlowResultType.ABORT
    assert result.get("reason") == "already_configured"


@pytest.mark.asyncio
async def test_reauth_flow_success(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "test_user",
            CONF_DASHBOARD_REFRESH_TOKEN: "old_dashboard_refresh",
        },
        unique_id="smappee_ev:test_user",
    )
    entry.add_to_hass(hass)
    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id}  # type: ignore[attr-defined]

    first = await flow.async_step_reauth(dict(entry.data))
    assert first.get("type") == FlowResultType.FORM
    assert first.get("step_id") == "reauth_confirm"

    with (
        patch(
            "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
            _dashboard_login_success,
        ),
        patch.object(hass.config_entries, "async_update_entry", return_value=True) as update_entry,
        patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload,
    ):
        second = await flow.async_step_reauth_confirm(
            {
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "fresh",
            }
        )

    assert second.get("type") == FlowResultType.ABORT
    assert second.get("reason") == "reauth_successful"
    _, kwargs = update_entry.call_args
    assert kwargs["entry"] == entry
    assert kwargs["unique_id"] == "smappee_ev:test_user"
    assert kwargs["data"] == {
        CONF_USERNAME: "test_user",
        CONF_PASSWORD: "fresh",
        CONF_DASHBOARD_REFRESH_TOKEN: "dashboard_refresh",
    }
    schedule_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_reauth_flow_removes_old_registry_entries_before_reload(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "test_user",
            CONF_DASHBOARD_REFRESH_TOKEN: "old_dashboard_refresh",
            CONF_NEEDS_DASHBOARD_REAUTH: True,
        },
        unique_id="smappee_ev:test_user",
    )
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    old_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "old_device")},
    )
    entity_registry = er.async_get(hass)
    old_entity = entity_registry.async_get_or_create(
        "button",
        DOMAIN,
        "old_action_button",
        config_entry=entry,
        device_id=old_device.id,
    )

    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id}  # type: ignore[attr-defined]

    await flow.async_step_reauth(dict(entry.data))

    with (
        patch(
            "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
            _dashboard_login_success,
        ),
        patch.object(hass.config_entries, "async_update_entry", return_value=True),
        patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload,
    ):
        result = await flow.async_step_reauth_confirm(
            {
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "fresh",
            }
        )

    assert result.get("type") == FlowResultType.ABORT
    assert result.get("reason") == "reauth_successful"
    assert entity_registry.async_get(old_entity.entity_id) is None
    assert device_registry.async_get(old_device.id) is None
    schedule_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_reauth_flow_auth_failed(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "test_user"},
        unique_id="smappee_ev:test_user",
    )
    entry.add_to_hass(hass)
    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id}
    first = await flow.async_step_reauth(dict(entry.data))
    assert first.get("type") == FlowResultType.FORM

    with (
        patch(
            "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
            side_effect=ConfigEntryAuthFailed("Dashboard credentials rejected"),
        ),
        patch.object(hass.config_entries, "async_update_entry") as update_entry,
        patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload,
    ):
        result = await flow.async_step_reauth_confirm(
            {
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "wrong",
            }
        )

    assert result.get("type") == FlowResultType.FORM
    assert (result.get("errors") or {})["base"] == "auth_failed"
    update_entry.assert_not_called()
    schedule_reload.assert_not_called()


def test_config_flow_properties():
    flow = SmappeeEvConfigFlow()
    assert flow.VERSION == 6


@pytest.mark.asyncio
async def test_reconfigure_flow_success(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "test_user",
            CONF_DASHBOARD_REFRESH_TOKEN: "old_dashboard_refresh",
        },
        options={
            CONF_USERNAME: "legacy_options_user",
            CONF_PASSWORD: "legacy_options_pass",
        },
        unique_id="smappee_ev:test_user",
    )
    entry.add_to_hass(hass)
    flow = SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id}
    first = await flow.async_step_reconfigure()
    assert first.get("type") == FlowResultType.FORM
    assert first.get("step_id") == "reconfigure"
    _assert_schema(first)

    with (
        patch(
            "custom_components.smappee_ev.config_flow.SmappeeDashboardClient.async_login",
            _dashboard_login_success,
        ),
        patch.object(hass.config_entries, "async_update_entry", return_value=True) as update_entry,
        patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload,
    ):
        result = await flow.async_step_reconfigure(
            {
                CONF_USERNAME: "test_user",
                CONF_PASSWORD: "updated_pass",
            }
        )

    assert result.get("type") == FlowResultType.ABORT
    assert result.get("reason") == "reconfigure_successful"
    _, kwargs = update_entry.call_args
    assert kwargs["options"] == {}
    assert kwargs["data"] == {
        CONF_USERNAME: "test_user",
        CONF_PASSWORD: "updated_pass",
        CONF_DASHBOARD_REFRESH_TOKEN: "dashboard_refresh",
    }
    schedule_reload.assert_called_once_with(entry.entry_id)
