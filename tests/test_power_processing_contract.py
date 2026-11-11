"""Characterize shared power arithmetic and the intentional scope differences."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.smappee_ev import sensor
from custom_components.smappee_ev.api.discovery import MqttChannelSpec
from custom_components.smappee_ev.coordinators.freshness import MqttApplyResult
from custom_components.smappee_ev.models.state import ConnectorState
from custom_components.smappee_ev.mqtt_setup import _setup_mqtt
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online
TOPIC = bootstrap.TOPIC


def _measurement(kind, indexes, **identity):
    channels = {}
    for metric, field in (
        ("activePower", "channelData"),
        ("current", "currentData"),
        ("meterReadings", "importActiveEnergyData"),
    ):
        channels[metric] = {
            "protocol": "MQTT",
            "name": TOPIC,
            "aspectPaths": [{"path": f"$.{field}[{i}]"} for i in indexes],
        }
    return {"type": kind, "updateChannels": channels, **identity}


@pytest.fixture
def power_pair(online):
    site = online.sites[1].site_coordinator
    bucket = online.sites[1].stations["station-1"]
    station = bucket.station_coordinator
    config = {
        "measurements": [
            _measurement("GRID", [0, 1, 2]),
            _measurement("PRODUCTION", [3, 4, 5]),
            _measurement("APPLIANCE", [6, 7, 8], category="CAR_CHARGER", uuid="connector-1"),
        ]
    }
    for coord in (site, station):
        coord._power_index_maps_by_topic = (
            coord._build_measurement_index_maps_by_topic_from_highlevel_configs({1: config})
        )
    return site, station, bucket


@pytest.fixture
def power_payload():
    return {
        "channelData": [100, 200, -50, 10, 20, 30, 700, 800, 900],
        "currentData": [1001, 2002, 3003, 4004, 5005, 6006, 7007, 8008, 9009],
        "phaseVoltageData": [2300, 2310, 2320, 9999],
        "importActiveEnergyData": [1000, 1000, 1000, 2000, 2000, 2000, 3000, 3000, 3000],
        "exportActiveEnergyData": [100, 200, 300],
        "consumptionPower": 456.8,
        "solarPower": 789.9,
        "alwaysOn": 111.6,
    }


SITE_SENSORS = [
    (sensor.StationGridPower, 250),
    (sensor.StationPvPower, 789),
    (sensor.StationHouseConsumptionPower, 456),
    (sensor.StationAlwaysOnPower, 111),
    (sensor.StationGridEnergyImport, 3),
    (sensor.StationGridEnergyExport, 0.6),
    (sensor.StationPvEnergyImport, 6),
    (sensor.StationGridCurrents, 6.006),
    (sensor.StationPvCurrents, 15.015),
    (sensor.StationGridCurrentL1, 1.001),
    (sensor.StationGridCurrentL2, 2.002),
    (sensor.StationGridCurrentL3, 3.003),
    (sensor.StationPvCurrentL1, 4.004),
    (sensor.StationPvCurrentL2, 5.005),
    (sensor.StationPvCurrentL3, 6.006),
    (sensor.StationGridVoltageL1, 230),
    (sensor.StationGridVoltageL2, 231),
    (sensor.StationGridVoltageL3, 232),
]
CONNECTOR_SENSORS = [
    (sensor.ConnectorPowerSensor, 2400),
    (sensor.ConnectorCurrentASensor, 24.024),
    (sensor.ConnCurrentL1, 7.007),
    (sensor.ConnCurrentL2, 8.008),
    (sensor.ConnCurrentL3, 9.009),
    (sensor.ConnEnergyImport, 3),
]


@pytest.mark.parametrize("scope", ["site", "station"])
def test_power_messages_project_to_all_existing_measurement_sensors(
    power_pair, power_payload, scope
):
    site, station, _bucket = power_pair
    coord = site if scope == "site" else station
    entities = [(cls(coord, None, 1, "site-1"), expected) for cls, expected in SITE_SENSORS]
    if scope == "station":
        client = station.connector_clients["connector-1"]
        entities += [
            (cls(station, client, 1, "station-1", "connector-1"), expected)
            for cls, expected in CONNECTOR_SENSORS
        ]
    metadata = [
        (entity.unique_id, entity.device_info, entity.native_unit_of_measurement)
        for entity, _expected in entities
    ]
    coord.apply_mqtt_properties(TOPIC, power_payload)
    for (entity, expected), before in zip(entities, metadata, strict=True):
        assert entity.native_value == expected, type(entity).__name__
        assert entity.available, type(entity).__name__
        assert (entity.unique_id, entity.device_info, entity.native_unit_of_measurement) == before
    state = coord.data.site if scope == "site" else coord.data.station
    # The scalar solar reading overrides the total, but not its phase readings.
    assert state.pv_power_phases == [10, 20, 30]
    assert state.grid_voltage_phases == [230, 231, 232]


@pytest.mark.parametrize("scope", ["site", "station"])
@pytest.mark.parametrize("bad", [None, [], "bad", {}, 42, [None] * 9, ["bad"] * 9, [1]])
def test_invalid_or_incomplete_groups_preserve_values_and_accept_no_measurements(
    power_pair, power_payload, scope, bad
):
    site, station, _bucket = power_pair
    coord = site if scope == "site" else station
    coord.apply_mqtt_properties(TOPIC, power_payload)
    before = deepcopy(coord.data)
    result = MqttApplyResult()
    assert not coord._handle_power(
        TOPIC,
        dict.fromkeys(
            ("channelData", "currentData", "importActiveEnergyData", "exportActiveEnergyData"),
            bad,
        ),
        result,
    )
    assert coord.data == before
    assert not result.site_fields
    assert not any(result.connector_fields.values())


@pytest.mark.parametrize("scope", ["site", "station"])
def test_partial_current_message_does_not_refresh_power_or_energy(
    power_pair, power_payload, scope, monkeypatch
):
    site, station, _bucket = power_pair
    coord = site if scope == "site" else station
    coord.apply_mqtt_properties(TOPIC, power_payload)
    state = coord.data.site if scope == "site" else coord.data.station
    before = deepcopy(state)
    now = datetime.now(UTC) + timedelta(minutes=10)
    clock = MagicMock(wraps=datetime)
    clock.now.return_value = now
    monkeypatch.setattr("custom_components.smappee_ev.coordinators.freshness.datetime", clock)
    result = coord.apply_mqtt_properties(TOPIC, {"currentData": [4000] * 9})
    assert result.site_fields == {"grid_current_phases", "pv_current_phases"}
    assert state.grid_power_total == before.grid_power_total
    assert state.pv_power_total == before.pv_power_total
    assert state.grid_energy_import_kwh == before.grid_energy_import_kwh
    assert state.grid_current_phases == [4, 4, 4]
    assert not sensor.StationGridPower(coord, None, 1, "site-1").available
    assert sensor.StationGridCurrents(coord, None, 1, "site-1").available
    if scope == "station":
        assert result.connector_fields == {"connector-1": {"current_phases"}}
        assert station.data.connectors["connector-1"].power_total == 2400
        assert station.data.connectors["connector-1"].energy_import_kwh == 3


@pytest.mark.parametrize("scope", ["site", "station"])
@pytest.mark.parametrize("solar_override", [False, True])
def test_duplicate_measurements_renew_freshness_and_preserve_publication_contract(
    power_pair, power_payload, scope, solar_override, monkeypatch
):
    site, station, _bucket = power_pair
    coord = site if scope == "site" else station
    if not solar_override:
        power_payload.pop("solarPower")
    listener = MagicMock()
    remove = coord.async_add_listener(listener)
    try:
        deadline = coord._unsub_refresh
        coord.last_update_success = False
        first = coord.apply_mqtt_properties(TOPIC, power_payload)
        listener.reset_mock()
        before_version = station._state_confirmations.version
        second = coord.apply_mqtt_properties(TOPIC, power_payload)
        assert second == first
        # Reapplying a conflicting scalar solar total changes the value twice,
        # so even a duplicate packet notifies the site in that case.
        assert listener.call_count == (scope == "station" or solar_override)
        if scope == "station":
            assert station._state_confirmations.version > before_version
        clock = MagicMock(wraps=datetime)
        clock.now.return_value = datetime.now(UTC) + timedelta(minutes=10)
        monkeypatch.setattr("custom_components.smappee_ev.coordinators.freshness.datetime", clock)
        assert not sensor.StationGridPower(coord, None, 1, "site-1").available
        listener.reset_mock()
        coord.apply_mqtt_properties(TOPIC, power_payload)
        listener.assert_called_once_with()
        assert coord.measurement_freshness.is_fresh("grid_power_total")
        # Fresh telemetry must not hide the coordinator's REST failure.
        assert not sensor.StationGridPower(coord, None, 1, "site-1").available
        assert coord.last_update_success is False
        assert coord._unsub_refresh is deadline
    finally:
        remove()


@pytest.mark.parametrize("scope", ["site", "station"])
@pytest.mark.parametrize("location", [None, "1", 999, "bad"])
def test_scalar_aggregate_location_filter_does_not_filter_mapped_arrays(
    power_pair, scope, location
):
    site, station, _bucket = power_pair
    coord = site if scope == "site" else station
    result = coord.apply_mqtt_properties(
        TOPIC,
        {
            "serviceLocationId": location,
            "channelData": [10] * 9,
            "consumptionPower": 0,
            "solarPower": 0,
            "alwaysOn": 0,
        },
    )
    state = coord.data.site if scope == "site" else coord.data.station
    assert state.grid_power_total == 30
    allowed = scope == "station" or location in (None, "1")
    assert state.house_consumption_power == (0 if allowed else None)
    assert state.pv_power_total == (0 if allowed else 30)
    assert ("house_consumption_power" in result.site_fields) is allowed


@pytest.mark.parametrize("scope", ["site", "station"])
@pytest.mark.parametrize(
    ("value", "expected", "error"),
    [
        (None, 30, None),
        ("12", 30, None),
        ({}, 30, None),
        (True, 1, None),
        (0, 0, None),
        (-2.9, -2, None),
        (float("nan"), 30, ValueError),
        (float("inf"), 30, OverflowError),
    ],
)
def test_scalar_coercion_and_error_order_are_preserved(power_pair, scope, value, expected, error):
    site, station, _bucket = power_pair
    coord = site if scope == "site" else station
    payload = {"channelData": [10] * 9, "consumptionPower": 123, "solarPower": value}
    if error:
        with pytest.raises(error):
            coord._handle_power(TOPIC, payload)
    else:
        coord._handle_power(TOPIC, payload)
    state = coord.data.site if scope == "site" else coord.data.station
    assert state.house_consumption_power == 123
    assert state.pv_power_total == expected


@pytest.mark.parametrize("scope", ["site", "station"])
@pytest.mark.parametrize(
    ("voltage", "expected"),
    [
        (None, [230, 231, 232]),
        ("bad", [230, 231, 232]),
        ([], [230, 231, 232]),
        ([2310], [231]),
        ([2310, 2320], [231, 232]),
        ([2310, 2320, 2330, 2340], [231, 232, 233]),
        ([2310, None], [230, 231, 232]),
    ],
)
def test_voltage_accepts_available_phases_but_rejects_malformed_groups(
    power_pair, power_payload, scope, voltage, expected
):
    site, station, _bucket = power_pair
    coord = site if scope == "site" else station
    coord.apply_mqtt_properties(TOPIC, power_payload)
    coord.apply_mqtt_properties(TOPIC, {"phaseVoltageData": voltage})
    state = coord.data.site if scope == "site" else coord.data.station
    assert state.grid_voltage_phases == expected
    assert state.grid_power_total == 250


@pytest.mark.parametrize("identity", ["identifier", "position", "name", "single", "ambiguous"])
def test_connector_resolution_and_energy_rules_remain_distinct(power_pair, online, identity):
    _site, coord, bucket = power_pair
    if identity != "single":
        client = online.sites[1].stations["station-2"].connectors["connector-2"].connector_client
        client.connector_number = 2
        coord.connector_clients["connector-2"] = client
        coord.data.connectors["connector-2"] = ConnectorState(
            connector_number=2, dashboard_device_name="Driveway"
        )
    identity_fields = {
        "identifier": {"uuid": "connector-2", "position": 1},
        "position": {"position": 2},
        "name": {"name": "Driveway"},
        "single": {},
        "ambiguous": {},
    }[identity]
    measurements = [
        _measurement("GRID", [0, 1, 2]),
        _measurement("APPLIANCE", [0, 1, 2], category="CAR_CHARGER", **identity_fields),
    ]
    coord._power_index_maps_by_topic = (
        coord._build_measurement_index_maps_by_topic_from_highlevel_configs(
            {1: {"measurements": measurements}}
        )
    )
    coord.apply_mqtt_properties(
        TOPIC,
        {"channelData": [100, 200, 300], "importActiveEnergyData": [5000] * 3},
    )
    assert coord.data.station.grid_energy_import_kwh == 15
    target = "connector-1" if identity == "single" else "connector-2"
    for uuid, state in coord.data.connectors.items():
        matched = uuid == target and identity != "ambiguous"
        assert state.power_total == (600 if matched else None)
        assert state.energy_import_kwh == (5 if matched else None)
    assert bucket.station_coordinator is coord


@pytest.mark.parametrize("routing", ["legacy", "configured"])
def test_shared_topic_routes_to_site_and_station_once_with_separate_state(
    hass, power_pair, power_payload, routing
):
    site, station, bucket = power_pair
    specs = None
    if routing == "configured":
        specs = [
            MqttChannelSpec(1, role, "activePower", TOPIC, None, None, [])
            for role in ("grid", "production", "car_charger", "car_charger")
        ]
    mqtt = _setup_mqtt(
        hass,
        suuid="site-uuid",
        serial_str="gateway",
        sid=1,
        stations={"station-1": bucket},
        site_coordinator=site,
        client_id_prefix="power-contract",
        update_interval=30,
        mqtt_specs=specs,
        start_clients=False,
    )
    site.async_update_listeners = MagicMock()
    station.async_update_listeners = MagicMock()
    site_state, station_state = site.data.site, station.data.station
    mqtt._on_properties(TOPIC, power_payload)
    assert site.data.site is site_state
    assert station.data.station is station_state
    assert site_state.grid_power_total == station_state.grid_power_total == 250
    assert station.data.connectors["connector-1"].power_total == 2400
    assert site.last_real_power_rx is not None
    assert station.last_real_power_rx is not None
    site.async_update_listeners.assert_called_once_with()
    station.async_update_listeners.assert_called_once_with()
