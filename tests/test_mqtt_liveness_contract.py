"""Keep transport, routed telemetry liveness, and field freshness independent."""

from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.smappee_ev.api.discovery import MqttChannelSpec
from custom_components.smappee_ev.binary_sensor import SmappeeMqttConnectivity
from custom_components.smappee_ev.models.state import ConnectorState
from custom_components.smappee_ev.mqtt_bootstrap import build_cached_runtime, snapshot_from_runtime
from custom_components.smappee_ev.mqtt_setup import _setup_mqtt
from custom_components.smappee_ev.runtime_lifecycle import _async_shutdown_runtime_resources
from custom_components.smappee_ev.sensor import (
    ConnectorCurrentASensor,
    ConnectorPowerSensor,
    ConnEnergyImport,
    SmappeeChargingStateSensor,
    StationGridPower,
)
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online
TOPIC = bootstrap.TOPIC
HEARTBEAT = "servicelocation/site-uuid/homeassistant/heartbeat"


def _status_topic(uuid="connector-1"):
    return (
        "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/"
        f"devices/{uuid}/property/chargingstate"
    )


@pytest.fixture
def liveness(hass, online, monkeypatch):
    now = [datetime(2026, 11, 11, 12, tzinfo=UTC)]
    clock = MagicMock(wraps=datetime)
    clock.now.side_effect = lambda *_args: now[0]
    for module in ("coordinator", "mqtt_setup", "coordinators.freshness"):
        monkeypatch.setattr(f"custom_components.smappee_ev.{module}.datetime", clock)
    monkeypatch.setattr("custom_components.smappee_ev.entity._utcnow", lambda: now[0])
    site = online.sites[1].site_coordinator
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    second = online.sites[1].stations["station-2"].connectors["connector-2"]
    bucket.connectors["connector-2"] = second
    coord.connector_clients["connector-2"] = second.connector_client
    coord.data.connectors["connector-2"] = ConnectorState(connector_number=2)

    def group(index):
        return {"power": [index], "current": [index], "energy": [index]}

    site._power_index_maps_by_topic = {TOPIC: {"grid": group(0), "pv": {}, "cars": {}}}
    coord._power_index_maps_by_topic = {
        TOPIC: {
            "grid": {},
            "pv": {},
            "cars": {"connector-1": group(1), "connector-2": group(2)},
        }
    }
    mqtt = _setup_mqtt(
        hass,
        suuid="site-uuid",
        serial_str="gateway",
        sid=1,
        stations={"station-1": bucket},
        site_coordinator=site,
        client_id_prefix="liveness-contract",
        update_interval=30,
        start_clients=False,
    )
    mqtt._on_conn(True)
    return SimpleNamespace(site=site, coord=coord, bucket=bucket, mqtt=mqtt, now=now)


def _sensor(ctx, cls, uuid="connector-1"):
    return cls(ctx.coord, ctx.coord.connector_clients[uuid], 1, "station-1", uuid)


@pytest.mark.parametrize("monitoring", [False, True])
def test_connected_transport_and_heartbeats_do_not_validate_measurements(liveness, monitoring):
    ctx = liveness
    ctx.coord.monitoring_only = ctx.site.monitoring_only = monitoring
    received = ctx.coord.data.station.last_mqtt_rx
    for coord in (ctx.site, ctx.coord):
        assert coord.mqtt_transport_connected
        assert not coord.measurement_freshness.received
        assert coord.last_real_power_rx is None
    assert not StationGridPower(ctx.site, None, 1, "site-1").available
    assert not _sensor(ctx, ConnectorPowerSensor).available
    assert _sensor(ctx, SmappeeChargingStateSensor).available is (not monitoring)
    assert SmappeeMqttConnectivity(ctx.site, 1).is_on
    ctx.now[0] += timedelta(minutes=10)
    ctx.mqtt._on_properties(HEARTBEAT, {})
    for coord in (ctx.site, ctx.coord):
        assert coord.last_heartbeat_rx == ctx.now[0]
        assert coord.last_real_power_rx is None
        assert not coord.measurement_freshness.received
    assert ctx.coord.last_valid_charger_telemetry_rx is None
    assert ctx.coord.last_connector_rx == {}
    assert ctx.coord.data.station.last_mqtt_rx == received
    assert ctx.coord._smartdevice_poll_interval() == timedelta(minutes=5)


@pytest.mark.parametrize("monitoring", [False, True])
def test_status_only_is_valid_telemetry_but_power_does_not_keep_status_live(liveness, monitoring):
    ctx = liveness
    ctx.coord.monitoring_only = monitoring
    ctx.mqtt._on_properties(_status_topic(), {"chargingState": "Started"})
    status_rx = ctx.now[0]
    assert ctx.coord.last_valid_charger_telemetry_rx == status_rx
    assert ctx.coord.last_connector_rx == {"connector-1": status_rx}
    assert ctx.coord.last_real_power_rx is None
    assert ctx.site.last_real_power_rx is None
    assert _sensor(ctx, SmappeeChargingStateSensor).available
    assert not _sensor(ctx, ConnectorPowerSensor).available
    assert ctx.coord._mqtt_reports_charger_state("connector-1")
    assert ctx.coord._smartdevice_poll_interval() == timedelta(minutes=30)

    ctx.now[0] += timedelta(minutes=6)
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [100, 3200]})
    assert ctx.coord.last_real_power_rx == ctx.now[0]
    assert ctx.coord.last_valid_charger_telemetry_rx == ctx.now[0]
    assert _sensor(ctx, ConnectorPowerSensor).available
    assert not ctx.coord._mqtt_reports_charger_state("connector-1")
    assert _sensor(ctx, SmappeeChargingStateSensor).available is (not monitoring)
    assert ctx.coord.measurement_freshness.received["connector-1", "charger_state"] == status_rx


def test_one_connectors_traffic_does_not_keep_the_other_connector_fresh(liveness):
    ctx = liveness
    ctx.coord.monitoring_only = True
    for uuid in ctx.coord.connector_clients:
        ctx.mqtt._on_properties(_status_topic(uuid), {"chargingState": "Started"})
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [100, 1000, 2000]})
    second_rx = ctx.coord.last_connector_rx["connector-2"]
    ctx.now[0] += timedelta(minutes=6)
    ctx.mqtt._on_properties(_status_topic(), {"chargingState": "Started"})
    # The incomplete array accepts connector 1 but cannot invent connector 2.
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [100, 1100]})
    for cls in (ConnectorPowerSensor, SmappeeChargingStateSensor):
        assert _sensor(ctx, cls).available
        assert not _sensor(ctx, cls, "connector-2").available
    assert ctx.coord.last_connector_rx["connector-1"] == ctx.now[0]
    assert ctx.coord.last_connector_rx["connector-2"] == second_rx
    assert ctx.coord.data.connectors["connector-2"].power_total == 2000
    assert not ctx.coord._mqtt_reports_charger_state("connector-2")


@pytest.mark.parametrize(
    ("payload", "accepted"),
    [
        ({}, None),
        ({"activePowerData": [100, None]}, None),
        ({"activePowerData": ["bad", "bad"]}, None),
        ({"currentData": [1000, 4000]}, "current_phases"),
        ({"importActiveEnergyData": [1000, 5000]}, "energy_import_kwh"),
    ],
)
def test_partial_packets_refresh_only_their_accepted_fields(liveness, payload, accepted):
    ctx = liveness
    ctx.mqtt._on_properties(_status_topic(), {"chargingState": "Started"})
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [100, 3200]})
    power_rx = ctx.coord.last_real_power_rx
    fields = dict(ctx.coord.measurement_freshness.received)
    ctx.now[0] += timedelta(minutes=6)
    ctx.mqtt._on_properties(TOPIC, payload)
    assert ctx.coord.last_real_power_rx == power_rx
    assert ctx.coord.data.connectors["connector-1"].power_total == 3200
    assert not ctx.coord._mqtt_reports_charger_state("connector-1")
    assert not _sensor(ctx, ConnectorPowerSensor).available
    assert _sensor(ctx, ConnectorCurrentASensor).available is (accepted == "current_phases")
    for key, timestamp in fields.items():
        assert ctx.coord.measurement_freshness.received[key] == timestamp
    expected_rx = ctx.now[0] if accepted else power_rx
    assert ctx.coord.last_valid_charger_telemetry_rx == expected_rx
    assert ctx.coord.last_connector_rx["connector-1"] == expected_rx
    if accepted:
        assert ctx.coord.measurement_freshness.received["connector-1", accepted] == ctx.now[0]


def test_unchanged_zero_power_renews_clocks_then_expires_despite_heartbeats(liveness):
    ctx = liveness
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [0, 0]})
    ctx.now[0] += timedelta(minutes=6)
    assert not _sensor(ctx, ConnectorPowerSensor).available
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [0, 0]})
    assert _sensor(ctx, ConnectorPowerSensor).native_value == 0
    assert _sensor(ctx, ConnectorPowerSensor).available
    assert ctx.coord.last_real_power_rx == ctx.now[0]
    assert ctx.coord.last_connector_rx["connector-1"] == ctx.now[0]
    assert ctx.coord.measurement_freshness.received["connector-1", "power_total"] == ctx.now[0]
    ctx.now[0] += timedelta(minutes=6)
    ctx.mqtt._on_properties(HEARTBEAT, {})
    assert ctx.coord.mqtt_transport_connected
    assert not _sensor(ctx, ConnectorPowerSensor).available
    assert not StationGridPower(ctx.site, None, 1, "site-1").available
    assert ctx.coord._smartdevice_poll_interval() == timedelta(minutes=5)


async def test_reconnect_resync_and_notifications_cannot_hide_rest_failure(hass, liveness):
    ctx = liveness
    ctx.coord._async_fetch_data = AsyncMock(side_effect=UpdateFailed("REST unavailable"))
    remove = ctx.coord.async_add_listener(MagicMock())
    try:
        ctx.mqtt._on_conn(False)
        await hass.async_block_till_done()
        assert not ctx.coord.last_update_success
        ctx.mqtt._on_conn(True)
        await hass.async_block_till_done()
        assert ctx.coord._async_fetch_data.await_count == 2
        deadline = ctx.coord._unsub_refresh
        attempt = ctx.coord._last_smartdevice_refresh
        for _ in range(3):
            ctx.mqtt._on_conn(True)
            ctx.mqtt._on_properties(HEARTBEAT, {})
            ctx.mqtt._on_properties(TOPIC, {"activePowerData": [100, 3200]})
        assert ctx.coord.data.connectors["connector-1"].power_total == 3200
        assert not ctx.coord.last_update_success
        assert not _sensor(ctx, ConnectorPowerSensor).available
        assert ctx.coord._unsub_refresh is deadline
        assert ctx.coord._last_smartdevice_refresh == attempt
        assert ctx.coord._smartdevice_poll_interval() == timedelta(minutes=5)
        assert ctx.coord._async_fetch_data.await_count == 2
    finally:
        remove()


def test_partial_client_connectivity_is_not_recovered_by_valid_payloads(hass, liveness):
    ctx = liveness
    ctx.coord.monitoring_only = ctx.site.monitoring_only = True
    clients = _setup_mqtt(
        hass,
        suuid="site-uuid",
        serial_str="gateway",
        sid=1,
        stations={"station-1": ctx.bucket},
        site_coordinator=ctx.site,
        client_id_prefix="split-liveness",
        update_interval=30,
        mqtt_specs=[
            MqttChannelSpec(1, "grid", "activePower", TOPIC, "first", "test-one", []),
            MqttChannelSpec(
                1, "car_charger", "activePower", "other/power", "second", "test-two", []
            ),
        ],
        start_clients=False,
    )
    assert len(clients) == 2
    clients[0]._on_conn(True)
    clients[0]._on_properties(_status_topic(), {"chargingState": "Started"})
    assert ctx.coord.data.station.mqtt_connected  # Legacy published hint.
    assert ctx.coord.measurement_freshness.is_fresh("charger_state", "connector-1")
    assert not ctx.coord.mqtt_transport_connected
    assert not _sensor(ctx, SmappeeChargingStateSensor).available
    clients[1]._on_conn(True)
    assert ctx.coord.mqtt_transport_connected
    assert _sensor(ctx, SmappeeChargingStateSensor).available
    clients[0]._on_conn(False)
    clients[1]._on_properties(_status_topic(), {"chargingState": "Started"})
    assert ctx.coord.data.station.mqtt_connected
    assert not ctx.coord.mqtt_transport_connected
    assert not _sensor(ctx, SmappeeChargingStateSensor).available


async def test_restored_energy_value_does_not_restore_mqtt_freshness(
    hass, entry, online, monkeypatch
):
    online.sites[1].stations["station-1"].station_coordinator._power_index_maps_by_topic[TOPIC][
        "cars"
    ]["connector-1"]["energy"] = [1]
    cached = build_cached_runtime(
        hass, entry, bootstrap.dashboard(), snapshot_from_runtime(online, entry)
    )
    component = EntityComponent(logging.getLogger(__name__), "sensor", hass)
    component._platforms["sensor"].config_entry = entry
    try:
        bucket = cached.sites[1].stations["station-1"]
        coord = bucket.station_coordinator
        client = bucket.connectors["connector-1"].connector_client
        entity = ConnEnergyImport(coord, client, 1, "station-1", "connector-1")
        original = ConnEnergyImport(
            online.sites[1].stations["station-1"].station_coordinator,
            client,
            1,
            "station-1",
            "connector-1",
        )
        entity.hass = hass
        entity.entity_id = "sensor.restored_liveness_energy"
        entity.async_get_last_sensor_data = AsyncMock(
            return_value=SimpleNamespace(native_value=12.5)
        )
        monkeypatch.setattr(entity, "async_write_ha_state", MagicMock())
        await component.async_add_entities([entity])
        assert entity.unique_id == original.unique_id
        assert entity.native_value == 12.5
        assert not entity.available
        assert coord.monitoring_only
        assert coord.update_interval is None
        assert not coord.measurement_freshness.received
        cached.mqtt[1]._on_conn(True)
        cached.mqtt[1]._on_properties(HEARTBEAT, {})
        assert not entity.available
        cached.mqtt[1]._on_properties(TOPIC, {"activePowerData": [100, 3200]})
        assert not entity.available  # Power cannot validate an energy reading.
        cached.mqtt[1]._on_properties(TOPIC, {"importActiveEnergyData": [1000, 13000]})
        assert entity.available
        assert entity.native_value == 13
    finally:
        await component._async_reset()
        await _async_shutdown_runtime_resources(cached)


@pytest.mark.parametrize("age", [-1, 299, 300, 301])
def test_field_freshness_and_poll_selection_keep_their_distinct_boundaries(liveness, age):
    ctx = liveness
    ctx.mqtt._on_properties(_status_topic(), {"chargingState": "Started"})
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [100, 3200]})
    ctx.now[0] += timedelta(seconds=age)
    assert _sensor(ctx, ConnectorPowerSensor).available is (0 <= age <= 300)
    assert ctx.coord._mqtt_reports_charger_state("connector-1") is (0 <= age <= 300)
    interval = 30 if 0 <= age < 300 else 5
    assert ctx.coord._smartdevice_poll_interval() == timedelta(minutes=interval)


def test_site_traffic_and_station_traffic_have_separate_liveness_owners(liveness):
    ctx = liveness
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [100]})
    assert ctx.site.last_real_power_rx == ctx.now[0]
    assert ctx.coord.last_valid_charger_telemetry_rx is None
    assert ctx.coord.last_real_power_rx is None
    # The existing broad station clock also accepts station grid telemetry;
    # its name must not be narrowed to only charging-state or connector data.
    ctx.coord._power_index_maps_by_topic[TOPIC]["grid"] = {"power": [0]}
    ctx.now[0] += timedelta(seconds=1)
    ctx.mqtt._on_properties(TOPIC, {"activePowerData": [100]})
    assert ctx.coord.last_valid_charger_telemetry_rx == ctx.now[0]
    assert ctx.coord.last_real_power_rx == ctx.now[0]
    assert ctx.coord.last_connector_rx == {}
    assert ctx.coord._smartdevice_poll_interval() == timedelta(minutes=30)


def test_direct_application_keeps_field_freshness_separate_from_route_liveness(liveness):
    ctx = liveness
    ctx.coord.apply_mqtt_properties(TOPIC, {"activePowerData": [100, 3200]})
    assert _sensor(ctx, ConnectorPowerSensor).available
    assert ctx.coord.measurement_freshness.is_fresh("power_total", "connector-1")
    assert ctx.coord.last_real_power_rx is None
    assert ctx.coord.last_valid_charger_telemetry_rx is None
    assert ctx.coord.last_connector_rx == {}
    assert ctx.coord._smartdevice_poll_interval() == timedelta(minutes=5)
