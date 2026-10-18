"""Tests for SmappeeStatusCoordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.smappee_ev.coordinators.status import (
    SmappeeStatusCoordinator,
    SmappeeStatusData,
)


@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.loop = MagicMock()
    return hass


def _make_coordinator(mock_hass):
    with patch(
        "custom_components.smappee_ev.coordinators.status.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coord = SmappeeStatusCoordinator.__new__(SmappeeStatusCoordinator)
        coord.hass = mock_hass
        coord._logged_unknown_subsystems = set()
        coord._logged_unknown_statuses = set()
        return coord


@pytest.mark.asyncio
async def test_successful_fetch_parses_subsystems_and_incidents(mock_hass):
    coord = _make_coordinator(mock_hass)
    payload = {
        "subsystems": [
            {"subsystem": "api", "status": "ok"},
            {"subsystem": "cloud", "status": "errors"},
        ],
        "incidents": [
            {"subsystem": "cloud", "resolved": False, "level": "minor"},
            {"subsystem": "api", "resolved": True},
        ],
    }
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=payload)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=mock_session,
    ):
        result = await coord._async_update_data()

    assert result.subsystems == {"api": "ok", "cloud": "errors"}
    assert len(result.active_incidents) == 1
    assert result.active_incidents[0]["subsystem"] == "cloud"


@pytest.mark.asyncio
async def test_network_error_raises_update_failed(mock_hass):
    coord = _make_coordinator(mock_hass)
    mock_resp = AsyncMock()
    mock_resp.__aenter__ = AsyncMock(side_effect=OSError("timeout"))
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with (
        patch(
            "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
            return_value=mock_session,
        ),
        pytest.raises(UpdateFailed, match="Error fetching Smappee status"),
    ):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_unknown_subsystem_logs_warning_once(mock_hass):
    coord = _make_coordinator(mock_hass)
    payload = {
        "subsystems": [{"subsystem": "newfeature", "status": "ok"}],
        "incidents": [],
    }
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=payload)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with (
        patch(
            "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
            return_value=mock_session,
        ),
        patch("custom_components.smappee_ev.coordinators.status._LOGGER.warning") as mock_warn,
    ):
        await coord._async_update_data()
        await coord._async_update_data()

    # Warning logged only once for the same unknown subsystem
    subsystem_warnings = [c for c in mock_warn.call_args_list if "newfeature" in str(c)]
    assert len(subsystem_warnings) == 1


@pytest.mark.asyncio
async def test_unknown_status_logs_warning_once(mock_hass):
    coord = _make_coordinator(mock_hass)
    payload = {
        "subsystems": [{"subsystem": "api", "status": "degraded"}],
        "incidents": [],
    }
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=payload)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with (
        patch(
            "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
            return_value=mock_session,
        ),
        patch("custom_components.smappee_ev.coordinators.status._LOGGER.warning") as mock_warn,
    ):
        await coord._async_update_data()
        await coord._async_update_data()

    status_warnings = [c for c in mock_warn.call_args_list if "degraded" in str(c)]
    assert len(status_warnings) == 1


@pytest.mark.asyncio
async def test_empty_response_returns_empty_data(mock_hass):
    coord = _make_coordinator(mock_hass)
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value={})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=mock_session,
    ):
        result = await coord._async_update_data()

    assert result.subsystems == {}
    assert result.active_incidents == []


@pytest.mark.asyncio
async def test_resolved_incidents_are_excluded(mock_hass):
    coord = _make_coordinator(mock_hass)
    payload = {
        "subsystems": [],
        "incidents": [
            {"subsystem": "api", "resolved": True},
            {"subsystem": "cloud", "resolved": False},
            {"subsystem": "livedata"},  # missing resolved → treated as resolved
        ],
    }
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=payload)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=mock_session,
    ):
        result = await coord._async_update_data()

    assert len(result.active_incidents) == 1
    assert result.active_incidents[0]["subsystem"] == "cloud"


def test_smappee_status_data_defaults():
    data = SmappeeStatusData()
    assert data.subsystems == {}
    assert data.active_incidents == []
