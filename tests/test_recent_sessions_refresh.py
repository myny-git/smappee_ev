"""Recent-session queries: filtering, throttling, reauthentication and shutdown."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from homeassistant.exceptions import ConfigEntryAuthFailed
import pytest

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.coordinator import SmappeeStationCoordinator
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState


def _station_coordinator(hass) -> SmappeeStationCoordinator:
    station_client = MagicMock(spec=SmappeeDeviceHandle)
    station_client.service_location_id = 200
    station_client.site_location_id = 100
    station_client.serial = "SERIAL"
    station_client.charging_station_serial = "STATION-1"
    connector_one = MagicMock(spec=SmappeeDeviceHandle)
    connector_one.connector_number = 1
    connector_two = MagicMock(spec=SmappeeDeviceHandle)
    connector_two.connector_number = 2
    coord = SmappeeStationCoordinator(
        hass,
        station_client=station_client,
        connector_clients={"conn-1": connector_one, "conn-2": connector_two},
        update_interval=60,
    )
    coord.data = IntegrationData(
        station=StationState(),
        connectors={
            "conn-1": ConnectorState(connector_number=1),
            "conn-2": ConnectorState(connector_number=2),
        },
    )
    return coord


@pytest.mark.asyncio
async def test_recent_sessions_filter_malformed_results_and_handle_errors(hass):
    coord = _station_coordinator(hass)
    coord.connector_clients["conn-1"].async_get_recent_sessions = AsyncMock(
        return_value=[{"session": 1}, "bad"]
    )
    coord.connector_clients["conn-2"].async_get_recent_sessions = AsyncMock(
        side_effect=RuntimeError("connector down")
    )

    sessions = await coord._async_get_recent_sessions()

    assert sessions == [{"session": 1}]
    assert coord._connector_session_available == {"conn-1": True, "conn-2": False}

    coord.connector_clients["conn-1"].async_get_recent_sessions.side_effect = ClientError("down")
    coord.connector_clients["conn-2"].async_get_recent_sessions.side_effect = RuntimeError("down")
    with pytest.raises(ClientError):
        await coord._async_get_recent_sessions()

    coord.connector_clients["conn-1"].async_get_recent_sessions.side_effect = ConfigEntryAuthFailed(
        "reauth"
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_get_recent_sessions()

    coord.connector_clients = {}
    assert await coord._async_get_recent_sessions() == []


@pytest.mark.asyncio
async def test_recent_session_refresh_lock_throttle_reauth_and_success(hass, monkeypatch):
    coord = _station_coordinator(hass)
    coord.async_update_listeners = MagicMock()
    coord._async_get_recent_sessions = AsyncMock(return_value=[{"session": 1}])

    await coord._session_refresh_lock.acquire()
    try:
        await coord._async_refresh_recent_sessions("locked")
    finally:
        coord._session_refresh_lock.release()
    coord._async_get_recent_sessions.assert_not_awaited()

    monkeypatch.setattr(
        "custom_components.smappee_ev.coordinators.session_tracking._now", lambda: 1000.0
    )
    coord._last_session_api_attempt = 999.0
    await coord._async_refresh_recent_sessions("throttled")
    coord._async_get_recent_sessions.assert_not_awaited()

    coord._last_session_api_attempt = 0
    coord.config_entry = MagicMock()
    coord._async_get_recent_sessions.side_effect = ConfigEntryAuthFailed("reauth")
    await coord._async_refresh_recent_sessions("reauth", force=True)
    coord.config_entry.async_start_reauth_if_available.assert_called_once_with(hass)

    coord._async_get_recent_sessions.side_effect = RuntimeError("offline")
    await coord._async_refresh_recent_sessions("offline", force=True)

    coord._async_get_recent_sessions.side_effect = None
    coord._async_get_recent_sessions.return_value = [{"session": 2}]
    await coord._async_refresh_recent_sessions("success", force=True)
    coord.async_update_listeners.assert_called_once()
    assert coord.data.recent_sessions == [{"session": 2}]


@pytest.mark.asyncio
async def test_session_tracking_shutdown_cancels_pending_scheduled_refreshes(hass):
    coord = _station_coordinator(hass)
    coord.connector_clients["conn-1"].async_get_recent_sessions = AsyncMock(return_value=[])
    scheduled = []
    unsubs = []
    active_loop_unsub = MagicMock()

    def fake_call_later(_hass, delay, callback):
        unsub = MagicMock(name=f"unsub-{delay}")
        scheduled.append((delay, callback))
        unsubs.append(unsub)
        return unsub

    with (
        patch(
            "custom_components.smappee_ev.coordinators.session_tracking."
            "SESSION_FINAL_REFRESH_DELAYS",
            (30, 120),
        ),
        patch(
            "custom_components.smappee_ev.coordinators.session_tracking.async_call_later",
            side_effect=fake_call_later,
        ),
        patch(
            "custom_components.smappee_ev.coordinators.session_tracking.async_track_time_interval",
            return_value=active_loop_unsub,
        ),
    ):
        coord.data.connectors["conn-1"].session_state = "CHARGING"
        coord.async_start_session_tracking()
        coord.apply_mqtt_properties(
            "servicelocation/site/etc/carcharger/acchargingcontroller/v1"
            "/devices/conn-1/property/chargingstate",
            {"chargingState": "STOPPED"},
        )

        assert [delay for delay, _ in scheduled] == [0, 30, 120]

        await coord.async_shutdown()
        await scheduled[-1][1](None)

    assert all(unsub.called for unsub in unsubs)
    active_loop_unsub.assert_called_once()
    coord.connector_clients["conn-1"].async_get_recent_sessions.assert_not_awaited()
