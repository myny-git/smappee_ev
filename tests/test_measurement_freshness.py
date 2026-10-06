"""MQTT traffic must not make absent, invalid or unrelated measurements fresh."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.smappee_ev.mqtt_setup import _setup_mqtt
from custom_components.smappee_ev.sensor import (
    ConnectorCurrentASensor,
    ConnectorPowerSensor,
    SmappeeChargingStateSensor,
    StationGridCurrents,
    StationGridPower,
)
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


@pytest.mark.parametrize("scope", ["site", "connector"])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"activePowerData": []},
        {"activePowerData": [None, None]},
        {"activePowerData": ["bad", "bad"]},
        {"currentData": [4000, 4000]},
        {"importActiveEnergyData": [5000, 5000]},
    ],
)
async def test_only_valid_power_renews_power_freshness(hass, online, monkeypatch, scope, payload):
    site = online.sites[1]
    bucket = site.stations["station-1"]
    coord = site.site_coordinator if scope == "site" else bucket.station_coordinator
    group = {"power": [0], "current": [0], "energy": [0], "power_field": "activePowerData"}
    coord._power_index_maps_by_topic = {
        bootstrap.TOPIC: {
            "grid": group if scope == "site" else {},
            "pv": {},
            "cars": {"connector-1": group} if scope == "connector" else {},
        }
    }
    mqtt = _setup_mqtt(
        hass,
        suuid="site-uuid",
        serial_str="gateway",
        sid=1,
        stations={"station-1": bucket} if scope == "connector" else {},
        client_id_prefix="freshness-test",
        update_interval=30,
        site_coordinator=coord if scope == "site" else None,
        start_clients=False,
    )
    mqtt._on_conn(True)
    if scope == "site":
        power = StationGridPower(coord, None, 1, "site-1")
        current = StationGridCurrents(coord, None, 1, "site-1")
    else:
        client = bucket.connectors["connector-1"].connector_client
        power = ConnectorPowerSensor(coord, client, 1, "station-1", "connector-1")
        current = ConnectorCurrentASensor(coord, client, 1, "station-1", "connector-1")
    mqtt._on_properties(bootstrap.TOPIC, {"activePowerData": [3200, 3200]})
    assert power.available
    assert power.native_value == 3200
    original_rx = coord.last_real_power_rx
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = datetime.now(UTC) + timedelta(minutes=10)
    monkeypatch.setattr("custom_components.smappee_ev.coordinators.freshness.datetime", clock)
    assert not power.available
    mqtt._on_properties(bootstrap.TOPIC, payload)
    assert not power.available
    assert power.native_value == 3200
    assert coord.last_real_power_rx == original_rx
    assert current.available is ("currentData" in payload)
    # Recovery also works with an unchanged value and with an explicit zero.
    mqtt._on_properties(bootstrap.TOPIC, {"activePowerData": [3200, 3200]})
    assert power.available
    mqtt._on_properties(bootstrap.TOPIC, {"activePowerData": [0, 0]})
    assert power.available
    assert power.native_value == 0


@pytest.mark.parametrize(
    "payload", [{}, {"unknown": "Started"}, {"chargingState": None}, {"chargingState": ""}]
)
async def test_empty_charger_message_does_not_refresh_state(hass, online, payload):
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    topic = "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/connector-1/property/chargingstate"
    mqtt = _setup_mqtt(
        hass,
        suuid="site-uuid",
        serial_str="gateway",
        sid=1,
        stations={"station-1": bucket},
        client_id_prefix="state-test",
        update_interval=30,
        start_clients=False,
    )
    mqtt._on_properties(topic, payload)
    assert coord.last_valid_charger_telemetry_rx is None
    assert not coord.measurement_freshness.is_fresh("charger_state", "connector-1")
    mqtt._on_properties(topic, {"chargingState": "Charging"})
    assert coord.last_valid_charger_telemetry_rx is not None
    assert coord.measurement_freshness.is_fresh("charger_state", "connector-1")


@pytest.mark.parametrize("suffix", ["state", "property/unknown"])
async def test_unhandled_charging_fields_do_not_make_state_fresh(online, suffix):
    coord = online.sites[1].stations["station-1"].station_coordinator
    topic = f"servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/connector-1/{suffix}"
    result = coord.apply_mqtt_properties(topic, {"chargingState": "Started"})
    assert not result.connector_fields
    assert not coord.measurement_freshness.is_fresh("charger_state", "connector-1")


async def test_charging_state_keeps_rest_fallback_but_cache_requires_mqtt(online, monkeypatch):
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    client = bucket.connectors["connector-1"].connector_client
    state = SmappeeChargingStateSensor(coord, client, 1, "station-1", "connector-1")
    coord.mqtt_transport_connected = True
    assert state.available  # Normal mode can use its REST charging-state snapshot.
    coord.monitoring_only = True
    assert not state.available
    topic = "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/connector-1/property/chargingstate"
    coord.apply_mqtt_properties(topic, {"chargingState": "Started"})
    assert state.available
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = datetime.now(UTC) + timedelta(minutes=10)
    monkeypatch.setattr("custom_components.smappee_ev.coordinators.freshness.datetime", clock)
    coord.apply_mqtt_properties(bootstrap.TOPIC, {"activePowerData": [0, 3200]})
    assert not state.available  # Live power does not confirm an older charging state.


@pytest.mark.parametrize("scope", ["site", "connector"])
async def test_measurements_expire_without_traffic_and_remove_timer(
    hass, online, monkeypatch, scope
):
    site = online.sites[1]
    bucket = site.stations["station-1"]
    coord = site.site_coordinator if scope == "site" else bucket.station_coordinator
    coord.monitoring_only = True
    coord.mqtt_transport_connected = True
    coord.update_interval = None
    coord.apply_mqtt_properties(bootstrap.TOPIC, {"activePowerData": [0, 3200]})
    if scope == "site":
        entity = StationGridPower(coord, None, 1, "site-1")
    else:
        client = bucket.connectors["connector-1"].connector_client
        entity = ConnectorPowerSensor(coord, client, 1, "station-1", "connector-1")
    entity.hass = hass
    entity.entity_id = f"sensor.test_{scope}_freshness"
    writes = []
    monkeypatch.setattr(entity, "async_write_ha_state", lambda: writes.append(entity.available))
    await entity.async_added_to_hass()
    assert entity.available
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = datetime.now(UTC) + timedelta(minutes=6)
    monkeypatch.setattr("custom_components.smappee_ev.coordinators.freshness.datetime", clock)
    async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert writes == [False]
    await entity.async_remove(force_remove=True)
    async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=65))
    await hass.async_block_till_done()
    assert writes == [False]


@pytest.mark.parametrize("scope", ["site", "connector"])
async def test_coordinator_shares_freshness_timer_and_stops_on_shutdown(
    hass, online, monkeypatch, scope
):
    site = online.sites[1]
    coord = (
        site.site_coordinator if scope == "site" else site.stations["station-1"].station_coordinator
    )
    coord.update_interval = None
    updates = [MagicMock(), MagicMock()]
    cancel = MagicMock()
    timer = MagicMock(return_value=cancel)
    monkeypatch.setattr(
        "custom_components.smappee_ev.coordinators.freshness_coordinator.async_track_time_interval",
        timer,
    )
    fetch = AsyncMock()
    monkeypatch.setattr(coord, "_async_update_data", fetch)
    remove_first = coord.async_add_listener(updates[0])
    remove_second = coord.async_add_listener(updates[1])
    assert timer.call_count == 1
    tick = timer.call_args.args[1]
    assert timer.call_args.args[2] == timedelta(seconds=30)
    tick(datetime.now(UTC))
    for update in updates:
        update.assert_called_once_with()
    fetch.assert_not_awaited()
    remove_first()
    cancel.assert_not_called()
    remove_second()
    cancel.assert_called_once_with()
    remove = coord.async_add_listener(updates[0])
    assert timer.call_count == 2
    await coord.async_shutdown()
    assert cancel.call_count == 2
    tick(datetime.now(UTC))
    updates[0].assert_called_once_with()
    remove()
    assert cancel.call_count == 2
