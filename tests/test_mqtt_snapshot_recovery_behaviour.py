"""Corrupted MQTT snapshots are never used, and cached monitoring stays honest.

The stored MQTT bootstrap snapshot holds broker credentials and the routing
that maps raw MQTT payload indexes to entities. It is what keeps monitoring
alive while the Dashboard is down, so a corrupted or inconsistent snapshot must
be rejected as a whole instead of producing a runtime that routes measurements
to the wrong entity (or crashes in an MQTT callback). Likewise an incomplete
online runtime must never overwrite the last known-good snapshot.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.smappee_ev.api.errors import (
    SmappeeAuthorizationError,
    SmappeeError,
    SmappeeServerError,
)
from custom_components.smappee_ev.mqtt_bootstrap import (
    async_load_snapshot,
    async_save_snapshot,
    bootstrap_store,
    build_cached_runtime,
)
from custom_components.smappee_ev.mqtt_recovery import _async_recover, start_recovery
from custom_components.smappee_ev.runtime_lifecycle import (
    _async_shutdown_runtime_resources,
    _begin_runtime_shutdown,
)
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online
snapshot = bootstrap.snapshot
TOPIC = bootstrap.TOPIC
LOAD_WARNING = "Ignoring unavailable or invalid MQTT bootstrap snapshot"
SAVE_WARNING = "MQTT bootstrap snapshot could not be saved; retaining previous snapshot"

type Change = Callable[[dict[str, Any]], None]


def _site(s: dict[str, Any]) -> dict[str, Any]:
    return s["sites"][0]


def _station(s: dict[str, Any], key: str = "station-1") -> dict[str, Any]:
    return _site(s)["stations"][key]


def _car_group(s: dict[str, Any]) -> dict[str, Any]:
    return _station(s)["power_maps"][TOPIC]["cars"]["connector-1"]


def _rename_station(s: dict[str, Any], new_key: str) -> None:
    stations = _site(s)["stations"]
    stations[new_key] = stations.pop("station-1")


def _reuse_connector_key(s: dict[str, Any]) -> None:
    """station-2 claims connector-1, which station-1 already owns."""
    station = _station(s, "station-2")
    connector = station["connectors"].pop("connector-2")
    connector["metadata"]["connector_key"] = "connector-1"
    station["connectors"]["connector-1"] = connector
    cars = station["power_maps"][TOPIC]["cars"]
    cars["connector-1"] = cars.pop("connector-2")


def _add_led(**overrides: Any) -> Change:
    def change(s: dict[str, Any]) -> None:
        led = {
            "led_key": "led-1",
            "led_device_id": "led-1",
            "led_device_uuid": "led-uuid",
            "led_device_name": "LED",
        }
        _station(s)["leds"] = {"led-1": {**led, **overrides}}

    return change


def _private_topic_without_credentials(s: dict[str, Any]) -> None:
    _site(s)["specs"][0].update(topic="private/site-uuid/power", username=None, password=None)


async def _store_and_load(hass, entry, value):
    """Persist through the real (JSON round-tripping) store, then load as setup does."""
    await bootstrap_store(hass, entry).async_save(value)
    return await async_load_snapshot(hass, entry)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda s: None, id="unmodified"),
        pytest.param(
            lambda s: _site(s)["specs"][0].update(username=None, password=None),
            id="public-servicelocation-topic-needs-no-credentials",
        ),
        pytest.param(lambda s: _car_group(s).update(position=None), id="unknown-position"),
        pytest.param(lambda s: _car_group(s).update(serial="EV-123"), id="connector-serial"),
        pytest.param(lambda s: _car_group(s).update(power_field="channelData"), id="channel-data"),
        pytest.param(_add_led(led_device_uuid=None, led_device_name=None), id="led"),
    ],
)
async def test_consistent_snapshot_is_loaded(hass, entry, snapshot, hass_storage, caplog, change):
    """Valid variants must survive storage; otherwise outages would silently lose monitoring.

    This is also the control for the rejection test below: each corruption there
    starts from this exact snapshot.
    """
    change(snapshot)
    loaded = await _store_and_load(hass, entry, snapshot)
    assert loaded == snapshot
    assert LOAD_WARNING not in caplog.text


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda s: _site(s).update(power_maps=[]), id="site-power-maps-not-dict"),
        pytest.param(
            lambda s: _site(s)["power_maps"].update({TOPIC: "grid"}), id="topic-mapping-not-dict"
        ),
        pytest.param(
            lambda s: _site(s)["power_maps"][TOPIC].update(grid=[0]),
            id="measurement-group-not-dict",
        ),
        pytest.param(
            lambda s: _station(s)["power_maps"][TOPIC]["cars"].update({"connector-1": [1]}),
            id="car-group-not-dict",
        ),
        pytest.param(lambda s: _car_group(s).update(position="1"), id="position-text"),
        pytest.param(lambda s: _car_group(s).update(position=True), id="position-bool"),
        pytest.param(lambda s: _car_group(s).update(serial=12345), id="serial-not-text"),
        pytest.param(lambda s: _car_group(s).update(power_field="voltageData"), id="power-field"),
        pytest.param(
            lambda s: s.update(fetched_at=datetime(2026, 1, 1, 12).isoformat()),
            id="fetched-at-without-timezone",
        ),
        pytest.param(_private_topic_without_credentials, id="private-topic-without-credentials"),
        pytest.param(lambda s: _rename_station(s, ""), id="empty-station-key"),
        pytest.param(lambda s: _station(s).update(connectors=[]), id="connectors-not-dict"),
        pytest.param(_reuse_connector_key, id="connector-key-reused-across-stations"),
        pytest.param(lambda s: _station(s).update(leds=[]), id="leds-not-dict"),
        pytest.param(_add_led(led_device_id=None), id="led-without-device-id"),
    ],
)
async def test_corrupted_snapshot_is_rejected(hass, entry, snapshot, hass_storage, caplog, change):
    """A corrupted snapshot must not reach MQTT routing or build a cached runtime.

    Prevents e.g. a reused connector key routing one charger's power to another
    charger's entities, an unauthenticated subscription to a private topic, or an
    MQTT callback crashing on a malformed index map during a Dashboard outage.
    """
    change(snapshot)
    with caplog.at_level(logging.WARNING):
        assert await _store_and_load(hass, entry, snapshot) is None
    assert LOAD_WARNING in caplog.text
    assert "mqtt-secret" not in caplog.text  # Rejection must not leak the credentials.


@pytest.mark.parametrize(
    "break_runtime",
    [
        pytest.param(
            lambda rt, mp: mp.setattr(
                rt.sites[1].stations["station-1"], "station_coordinator", None
            ),
            id="station-without-coordinator",
        ),
        pytest.param(
            lambda rt, mp: mp.setattr(
                rt.sites[1].stations["station-1"].station_coordinator, "data", None
            ),
            id="station-without-first-data",
        ),
        pytest.param(
            lambda rt, mp: mp.setattr(rt.sites[1], "site_coordinator", None),
            id="site-without-coordinator",
        ),
    ],
)
async def test_incomplete_runtime_never_overwrites_good_snapshot(
    hass, entry, online, hass_storage, caplog, monkeypatch, break_runtime
):
    """A half-initialised setup must keep the last good snapshot for the next outage.

    Overwriting it (or failing setup) would leave the user without MQTT monitoring
    exactly when the Dashboard goes down next.
    """
    await async_save_snapshot(hass, entry, online)
    good = await async_load_snapshot(hass, entry)
    assert good is not None
    stored = bootstrap_store(hass, entry).key
    raw_before = hass_storage[stored]

    break_runtime(online, monkeypatch)
    with caplog.at_level(logging.WARNING):
        await async_save_snapshot(hass, entry, online)  # Must not raise.

    assert SAVE_WARNING in caplog.text
    assert hass_storage[stored] is raw_before
    assert await async_load_snapshot(hass, entry) == good


def _listeners(runtime) -> list[MagicMock]:
    """Attach one listener per site/station coordinator, as entities would."""
    listeners = []
    for site in runtime.sites.values():
        coordinators = [site.site_coordinator] + [
            bucket.station_coordinator for bucket in site.stations.values()
        ]
        for coordinator in coordinators:
            listener = MagicMock()
            coordinator.async_add_listener(listener)
            # Isolate the runtime-level recovery tick from the coordinator's own
            # per-listener freshness timer, so only the former is under test.
            coordinator.cancel_freshness_timer()
            listeners.append(listener)
    return listeners


async def test_cached_runtime_freshness_tick_refreshes_entities_until_shutdown(
    hass, entry, snapshot, monkeypatch
):
    """MQTT-only entities must re-evaluate availability every 30 s without REST polling.

    Without the tick, entities would keep showing the last MQTT value as
    available forever after MQTT goes silent; after shutdown the tick must stop
    so an unloaded entry does not keep touching its coordinators.
    """
    cached = build_cached_runtime(hass, entry, bootstrap.dashboard(), snapshot)
    entry.runtime_data = cached
    monkeypatch.setattr(
        "custom_components.smappee_ev.mqtt_recovery._create_dashboard_client",
        MagicMock(return_value=cached.api),
    )
    listeners = _listeners(cached)
    assert len(listeners) == 3
    # The Dashboard stays down: the recovery loop may probe, but never recovers.
    prepare = AsyncMock(side_effect=SmappeeServerError("offline"))
    try:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
        await hass.async_block_till_done()
        assert not any(listener.called for listener in listeners)  # No tick before recovery.

        start_recovery(hass, entry, cached, prepare)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
        await hass.async_block_till_done()
        assert all(listener.call_count == 1 for listener in listeners)

        _begin_runtime_shutdown(cached)
        assert not cached.cleanup_callbacks
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
        await hass.async_block_till_done()
        assert all(listener.call_count == 1 for listener in listeners)
    finally:
        await _async_shutdown_runtime_resources(cached)


@pytest.mark.parametrize("stop", ["runtime", "homeassistant"])
async def test_recovery_does_not_probe_dashboard_while_stopping(hass, entry, online, stop):
    """A stop that happens during the retry wait must not trigger a Dashboard login.

    Probing during shutdown would create clients/coordinators that nobody closes
    and could schedule a reload of an entry that is being unloaded.
    """

    async def wait(_delay):
        if stop == "runtime":
            online.stopping = True
        else:
            hass.set_state(CoreState.stopping)

    prepare = AsyncMock()
    try:
        with (
            patch("custom_components.smappee_ev.mqtt_recovery.asyncio.sleep", new=wait),
            patch(
                "custom_components.smappee_ev.mqtt_recovery._create_dashboard_client"
            ) as create_client,
        ):
            await _async_recover(hass, entry, online, prepare)
    finally:
        hass.set_state(CoreState.running)

    create_client.assert_not_called()
    prepare.assert_not_awaited()
    assert online.recovery.attempts == 0
    assert online.recovery.status == "stopped"
    assert online.recovery.next_attempt is None


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(SmappeeError("PRIVATE"), id="generic-smappee-error"),
        pytest.param(SmappeeAuthorizationError("PRIVATE"), id="authorization-denied"),
    ],
)
async def test_unclassified_smappee_error_reports_api_error_and_keeps_retrying(
    hass, entry, online, error
):
    """Diagnostics must say "api_error" (not "unexpected") and recovery must retry.

    A Dashboard-side refusal is an expected remote failure: classifying it as
    "unexpected" would send users chasing an integration bug, and giving up
    would leave the entry stuck in monitoring-only mode after the Dashboard
    recovers. An authorization denial is deliberately not a reauth trigger.
    """
    waits: list[float] = []

    async def wait(delay):
        waits.append(delay)
        if len(waits) == 2:
            online.stopping = True

    prepare = AsyncMock(side_effect=error)
    with (
        patch("custom_components.smappee_ev.mqtt_recovery.asyncio.sleep", new=wait),
        patch("custom_components.smappee_ev.mqtt_recovery.random.uniform", return_value=1),
        patch(
            "custom_components.smappee_ev.mqtt_recovery._create_dashboard_client",
            return_value=online.api,
        ),
        patch.object(entry, "async_start_reauth_if_available") as reauth,
    ):
        await _async_recover(hass, entry, online, prepare)

    assert prepare.await_count == 1
    assert waits == [30.0, 60.0]  # Backed off and scheduled another attempt.
    assert online.recovery.last_error_category == "api_error"
    reauth.assert_not_called()
