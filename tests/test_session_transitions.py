"""MQTT and REST session-state changes share one transition path."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev.coordinators.session_tracking import (
    SESSION_ACTIVE_REFRESH_INTERVAL,
    SESSION_FINAL_REFRESH_DELAYS,
    SESSION_START_REFRESH_DELAY,
)
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online

CONNECTOR = "connector-1"
CHARGING_STATE_TOPIC = (
    "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/"
    f"{CONNECTOR}/property/chargingstate"
)


class _Harness:
    """One real station coordinator with recorded timers and a scripted REST poll."""

    def __init__(self, coord, monkeypatch):
        self.coord = coord
        self.timers: list[dict] = []
        self.loops: list[dict] = []
        self.rest_state = "Initialize"
        coord._async_refresh_recent_sessions = AsyncMock()
        coord._fetch_station_state = AsyncMock(return_value=StationState(api_available=True))
        coord._fetch_connector_state = AsyncMock(side_effect=self._connector_rest_state)
        coord._ensure_power_index_map = AsyncMock()
        coord._maybe_refresh_dashboard_data = AsyncMock(return_value=False)
        module = "custom_components.smappee_ev.coordinators.session_tracking"
        monkeypatch.setattr(f"{module}.async_call_later", self._call_later)
        monkeypatch.setattr(f"{module}.async_track_time_interval", self._track_interval)

    async def _connector_rest_state(self, *_args, **_kwargs):
        return ConnectorState(connector_number=1, session_state=self.rest_state)

    def _call_later(self, _hass, delay, callback):
        timer = {"delay": delay, "callback": callback, "cancel": MagicMock()}
        self.timers.append(timer)
        return timer["cancel"]

    def _track_interval(self, _hass, callback, interval):
        loop = {"interval": interval.total_seconds(), "callback": callback, "cancel": MagicMock()}
        self.loops.append(loop)
        return loop["cancel"]

    @property
    def connector(self) -> ConnectorState:
        return self.coord.data.connectors[CONNECTOR]

    def charge(self) -> None:
        """Put the published state in a running session with live readings."""
        conn = self.connector
        conn.session_state = "Started"
        conn.power_total = 7400
        conn.power_phases = [2400, 2500, 2500]
        conn.current_phases = [10.5, 10.9, 10.9]

    def mqtt(self, state: str) -> None:
        self.coord.apply_mqtt_properties(CHARGING_STATE_TOPIC, {"chargingState": state})

    async def poll(self, state: str) -> IntegrationData:
        """Run one REST poll and publish its result like the coordinator does."""
        self.rest_state = state
        data = await self.coord._async_fetch_data()
        self.coord.data = data
        return data

    def final_delays(self) -> list[int]:
        return [timer["delay"] for timer in self.timers]


@pytest.fixture
def harness(online, monkeypatch):
    coord = online.sites[1].stations["station-1"].station_coordinator
    coord._session_tracking_started = True
    return _Harness(coord, monkeypatch)


async def test_mqtt_session_end_clears_power_and_stops_loop(harness):
    harness.charge()
    harness.coord._ensure_active_session_loop()
    harness.mqtt("Stopped")
    assert harness.connector.power_total == 0
    assert harness.connector.power_phases is None
    assert harness.connector.current_phases is None
    assert harness.loops[0]["cancel"].called
    assert harness.final_delays() == list(SESSION_FINAL_REFRESH_DELAYS)


async def test_mqtt_session_start_starts_loop_and_one_start_refresh(harness):
    harness.connector.session_state = "Stopped"
    harness.mqtt("Started")
    harness.mqtt("Started")
    assert [loop["interval"] for loop in harness.loops] == [SESSION_ACTIVE_REFRESH_INTERVAL]
    assert harness.final_delays() == [SESSION_START_REFRESH_DELAY]


async def test_rest_session_end_without_mqtt_matches_the_mqtt_transition(harness):
    harness.charge()
    harness.coord._ensure_active_session_loop()
    previous = harness.connector

    data = await harness.poll("Stopped")

    conn = data.connectors[CONNECTOR]
    assert conn.session_state == "Stopped"
    assert (conn.power_total, conn.power_phases, conn.current_phases) == (0, None, None)
    # The poll builds a new snapshot; the still-published one is not rewritten.
    assert previous.power_total == 7400
    assert harness.loops[0]["cancel"].called
    assert harness.coord._session_active_loop_unsub is None
    assert harness.final_delays() == list(SESSION_FINAL_REFRESH_DELAYS)

    # Later polls and the retained message after a reconnect report the same
    # terminal state; neither may schedule the final refreshes again.
    await harness.poll("Stopped")
    harness.coord.mqtt_transport_connected = True
    harness.mqtt("Stopped")
    assert harness.final_delays() == list(SESSION_FINAL_REFRESH_DELAYS)
    assert all(not timer["cancel"].called for timer in harness.timers)


async def test_rest_session_start_without_mqtt_starts_tracking(harness):
    harness.connector.session_state = "Stopped"

    data = await harness.poll("Started")

    assert data.connectors[CONNECTOR].session_state == "Started"
    assert [loop["interval"] for loop in harness.loops] == [SESSION_ACTIVE_REFRESH_INTERVAL]
    assert harness.final_delays() == [SESSION_START_REFRESH_DELAY]
    await harness.poll("Started")
    harness.coord.mqtt_transport_connected = True
    harness.mqtt("Started")
    assert len(harness.loops) == 1
    assert harness.final_delays() == [SESSION_START_REFRESH_DELAY]


async def test_rest_cannot_contradict_live_mqtt_charger_state(harness):
    harness.charge()
    harness.coord._ensure_active_session_loop()
    harness.coord.mqtt_transport_connected = True
    harness.mqtt("Started")

    data = await harness.poll("Stopped")

    conn = data.connectors[CONNECTOR]
    # A lagging poll must not publish a state that contradicts live MQTT.
    assert conn.session_state == "Started"
    assert conn.paused is False
    assert conn.power_total == 7400
    assert harness.coord._is_session_active(conn)
    assert not harness.loops[0]["cancel"].called
    assert harness.timers == []

    # The active-session loop keeps running on its own tick ...
    await harness.loops[0]["callback"](None)
    assert harness.coord._session_active_loop_unsub is not None
    # ... and the next MQTT message is not mistaken for a new session.
    harness.mqtt("Started")
    assert len(harness.loops) == 1
    assert harness.timers == []


async def test_live_mqtt_status_and_cause_survive_a_rest_poll(harness):
    """REST supplies neither field, so they cannot change session activity."""
    harness.coord.mqtt_transport_connected = True
    harness.coord.apply_mqtt_properties(
        CHARGING_STATE_TOPIC,
        {"chargingState": "Started", "status": {"current": "CHARGING"}},
    )

    conn = (await harness.poll("Stopped")).connectors[CONNECTOR]

    assert (conn.session_state, conn.status_current, conn.session_cause) == (
        "Started",
        "CHARGING",
        "CHARGING",
    )
    assert harness.coord._is_session_active(conn)


async def test_rest_drives_sessions_when_connected_mqtt_state_went_stale(harness):
    harness.charge()
    harness.coord._ensure_active_session_loop()
    harness.coord.mqtt_transport_connected = True

    data = await harness.poll("Stopped")

    assert data.connectors[CONNECTOR].power_total == 0
    assert harness.final_delays() == list(SESSION_FINAL_REFRESH_DELAYS)


async def test_rest_session_end_clears_power_before_tracking_has_started(harness):
    harness.coord._session_tracking_started = False
    harness.charge()

    data = await harness.poll("Stopped")

    assert data.connectors[CONNECTOR].power_total == 0
    assert harness.timers == []
    assert harness.loops == []


async def test_reload_during_active_session_keeps_one_loop(harness):
    """Startup resumes a running session; later observations are not new sessions."""
    harness.coord._session_tracking_started = False
    harness.charge()

    harness.coord.async_start_session_tracking()
    harness.coord.async_start_session_tracking()
    assert len(harness.loops) == 1
    assert harness.final_delays() == [0]

    await harness.poll("Started")
    harness.coord.mqtt_transport_connected = True
    harness.mqtt("Started")
    await harness.poll("Started")
    assert len(harness.loops) == 1
    assert not harness.loops[0]["cancel"].called
    assert harness.final_delays() == [0]


async def test_rest_transitions_need_a_previous_state_per_connector(harness):
    coord = harness.coord
    active = IntegrationData(
        station=StationState(),
        connectors={
            CONNECTOR: ConnectorState(connector_number=1, session_state="Stopped"),
            "new": ConnectorState(connector_number=2, session_state="Started"),
        },
    )
    # The first snapshot after startup is a baseline, as is a connector that
    # was not known before; session tracking resumes from it without an edge.
    coord._apply_rest_session_transitions(None, active)
    coord._apply_rest_session_transitions(coord.data, active)
    assert harness.timers == []
    assert harness.loops == []
