"""Tests for SmappeeStatusCoordinator."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.smappee_ev.coordinators.status import (
    SmappeeStatusCoordinator,
    SmappeeStatusData,
)
from tests.factories import make_site_runtime, make_station_runtime


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
        coord = SmappeeStatusCoordinator(mock_hass)
        coord.hass = mock_hass
        coord.config_entry = None
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
    assert coord._last_status_fetch_success


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

    assert not coord._last_status_fetch_success


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
async def test_empty_response_is_a_controlled_failure(mock_hass):
    coord = _make_coordinator(mock_hass)
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value={})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)

    with (
        patch(
            "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
            return_value=mock_session,
        ),
        pytest.raises(UpdateFailed, match="no subsystem status"),
    ):
        await coord._async_update_data()
    assert not coord._last_status_fetch_success


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


async def test_mqtt_health_is_checked_every_five_minutes(hass):
    coord = SmappeeStatusCoordinator(hass)
    assert coord.update_interval == timedelta(minutes=5)
    await coord.async_shutdown()


@pytest.mark.parametrize(
    "missing_data", [{}, {"subsystems": [], "incidents": []}, OSError("offline")]
)
async def test_failed_status_response_does_not_throttle_recovery(mock_hass, missing_data):
    coord = _make_coordinator(mock_hass)
    response = AsyncMock()
    response.raise_for_status = MagicMock()
    response.__aenter__.return_value = response
    response.json.side_effect = [
        missing_data,
        {"subsystems": [{"subsystem": "cloud", "status": "ok"}]},
    ]
    session = MagicMock()
    session.get.return_value = response
    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=session,
    ):
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()
        assert not coord._last_status_fetch_success

        recovered = await coord._async_update_data()

    assert recovered.subsystems == {"cloud": "ok"}
    assert coord._last_status_fetch_success


@pytest.mark.parametrize("source", ["site", "station"])
@pytest.mark.parametrize("mqtt_state", ["fresh", "stale", "disconnected", "heartbeat", "future"])
def test_only_connected_recent_measurements_count_as_live_data(mock_hass, source, mqtt_state):
    coord = _make_coordinator(mock_hass)
    now = datetime.now(UTC)
    timestamp = now - timedelta(minutes=5) if mqtt_state == "stale" else now
    if mqtt_state == "future":
        timestamp = now + timedelta(minutes=1)
    live = SimpleNamespace(
        mqtt_transport_connected=mqtt_state != "disconnected",
        measurement_freshness=SimpleNamespace(
            received={} if mqtt_state == "heartbeat" else {(None, "power_total"): timestamp}
        ),
        last_heartbeat_rx=now,
    )
    coord._sites = {
        1: make_site_runtime(
            site_coordinator=live if source == "site" else None,
            stations={"station": make_station_runtime(coordinator=live)}
            if source == "station"
            else {},
        )
    }

    assert coord._has_live_mqtt_data() is (mqtt_state == "fresh")


async def test_healthy_mqtt_fetches_every_fifteen_minutes_and_loss_accelerates(
    mock_hass, monkeypatch
):
    coord = _make_coordinator(mock_hass)
    clock = [0.0]
    monkeypatch.setattr(
        "custom_components.smappee_ev.coordinators.status.monotonic", lambda: clock[0]
    )
    live = True
    monkeypatch.setattr(coord, "_has_live_mqtt_data", lambda: live)
    response = AsyncMock()
    response.__aenter__.return_value = response
    response.raise_for_status = MagicMock()
    response.json.return_value = {"subsystems": [{"subsystem": "cloud", "status": "ok"}]}
    session = MagicMock()
    session.get.return_value = response

    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=session,
    ):
        coord.data = await coord._async_update_data()
        for elapsed in (300, 600):
            clock[0] = elapsed
            assert await coord._async_update_data() is coord.data
            assert session.get.call_count == 1
        clock[0] = 900
        coord.data = await coord._async_update_data()
        assert session.get.call_count == 2

        live = False
        clock[0] = 1200
        coord.data = await coord._async_update_data()
        assert session.get.call_count == 3
        clock[0] = 1500
        coord.data = await coord._async_update_data()
        assert session.get.call_count == 4

        live = True
        for elapsed in (1800, 2100):
            clock[0] = elapsed
            assert await coord._async_update_data() is coord.data
            assert session.get.call_count == 4
        clock[0] = 2400
        coord.data = await coord._async_update_data()
        assert session.get.call_count == 5


def test_one_silent_site_keeps_status_checks_fast(mock_hass):
    coord = _make_coordinator(mock_hass)
    live = SimpleNamespace(
        mqtt_transport_connected=True,
        measurement_freshness=SimpleNamespace(received={(None, "power_total"): datetime.now(UTC)}),
    )
    coord._sites = {
        1: make_site_runtime(site_coordinator=live),
        2: make_site_runtime(site_location_id=2),
    }

    assert not coord._has_live_mqtt_data()
