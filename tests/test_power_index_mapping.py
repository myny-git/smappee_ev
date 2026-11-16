"""MQTT power index maps: building them from the configuration and applying payloads."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.coordinator import (
    SmappeeSiteCoordinator,
    SmappeeStationCoordinator,
)
from custom_components.smappee_ev.coordinators.power import (
    _active_power_values,
    _indexes_and_field_from_aspect_paths,
    _indexes_from_aspect_paths,
    _mqtt_channel_topic,
    _volts_from_dv,
)
from custom_components.smappee_ev.models.state import (
    ConnectorState,
    IntegrationData,
    SiteData,
    SiteState,
    StationState,
)


def _channel(topic: str, field: str, *indexes: int) -> dict:
    return {
        "protocol": "MQTT",
        "name": topic,
        "aspectPaths": [{"path": f"$.{field}[{index}]"} for index in indexes],
    }


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


def test_mqtt_channel_helpers_ignore_invalid_shapes_and_pick_first_field():
    channel = {
        "protocol": "MQTT",
        "name": " topic ",
        "aspectPaths": [
            {"path": "$.ignored[0]"},
            {"path": "$.channelData[3]"},
            {"path": "$.activePowerData[4]"},
            {"path": "$.channelData[5]"},
            "bad",
        ],
    }

    assert _mqtt_channel_topic(None) is None
    assert _mqtt_channel_topic({"protocol": "HTTP", "name": "topic"}) is None
    assert _mqtt_channel_topic({"protocol": "MQTT", "name": " "}) is None
    assert _indexes_and_field_from_aspect_paths(None, "channelData") == ([], None)
    assert _indexes_from_aspect_paths(channel, "channelData") == [3, 5]
    assert _indexes_and_field_from_aspect_paths(channel, "channelData", "activePowerData") == (
        [3, 5],
        "channelData",
    )
    assert _active_power_values({"custom": [1]}, "custom") == [1]
    assert _active_power_values({"custom": "bad"}, "custom") == []
    assert _active_power_values({"activePowerData": [2]}) == [2]
    assert _active_power_values({"channelData": [3]}) == [3]
    assert _active_power_values({"noPower": [4]}) == []
    assert _volts_from_dv([2301, 2299]) == [230, 230]


@pytest.mark.asyncio
async def test_site_coordinator_lazy_update_builds_power_map_and_default_state(hass):
    topic = "servicelocation/site/power"
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
        highlevel_configs={
            100: {
                "measurements": [
                    {
                        "type": "GRID",
                        "updateChannels": {"activePower": _channel(topic, "channelData", 0, 1, 2)},
                    }
                ]
            }
        },
    )

    data = await coord._async_update_data()

    assert isinstance(data, SiteData)
    assert topic in coord._power_index_maps_by_topic

    cached = coord._power_index_maps_by_topic
    await coord._ensure_power_index_map()
    assert coord._power_index_maps_by_topic is cached


def test_site_coordinator_builds_and_applies_highlevel_grid_and_pv_maps(hass):
    topic = "custom/realtime/grid-values"
    cfg = {
        "measurements": [
            "bad",
            {"type": "GRID", "updateChannels": "bad"},
            {
                "type": "GRID",
                "updateChannels": {
                    "activePower": _channel(topic, "channelData", 0, 1, 2),
                    "current": _channel(topic, "currentData", 0, 1, 2),
                    "meterReadings": _channel(topic, "importActiveEnergyData", 0, 1, 2),
                },
            },
            {
                "type": "PRODUCTION",
                "updateChannels": {
                    "activePower": _channel(topic, "channelData", 3, 4, 5),
                    "current": _channel(topic, "currentData", 3, 4, 5),
                    "meterReadings": _channel(topic, "importActiveEnergyData", 3, 4, 5),
                },
            },
        ]
    }
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
        highlevel_configs={100: cfg},
    )
    coord.data = SiteData(site=SiteState())

    mapping = coord._build_measurement_index_maps_by_topic_from_highlevel_configs({100: cfg})
    coord._power_index_maps_by_topic = mapping
    coord.apply_mqtt_properties(
        topic,
        {
            "channelData": [100, 200, -50, 10, 20, 30],
            "currentData": [1000, 2000, 3000, 400, 500, 600],
            "phaseVoltageData": [2300, 2310, 2320],
            "importActiveEnergyData": [1000, 2000, 3000, 4000, 5000, 6000],
            "exportActiveEnergyData": [100, 200, 300],
            "consumptionPower": 456,
            "solarPower": 789,
            "alwaysOn": 111,
        },
    )

    site = coord.data.site
    assert site.mqtt_connected is True
    assert site.grid_power_phases == [100, 200, -50]
    assert site.grid_power_total == 250
    assert site.grid_current_phases == [1.0, 2.0, 3.0]
    assert site.grid_voltage_phases == [230, 231, 232]
    assert site.grid_energy_import_kwh == 6.0
    assert site.grid_energy_export_kwh == 0.6
    assert site.pv_power_phases == [10, 20, 30]
    assert site.pv_energy_import_kwh == 15.0
    assert site.house_consumption_power == 456
    assert site.pv_power_total == 789
    assert site.always_on_power == 111


def test_site_aggregates_ignore_child_payloads_and_accept_parent_zero(hass):
    """Child service-location aggregates must not overwrite the parent site."""
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=263713,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
    )
    coord.data = SiteData(site=SiteState())

    coord.apply_mqtt_properties(
        "servicelocation/parent/power",
        {
            "serviceLocationId": 263713,
            "consumptionPower": 364,
            "solarPower": 1461,
            "alwaysOn": 188,
        },
    )
    coord.apply_mqtt_properties(
        "servicelocation/child/power",
        {
            "serviceLocationId": 263703,
            "consumptionPower": 0,
            "solarPower": 0,
            "alwaysOn": 0,
        },
    )

    assert coord.data.site.house_consumption_power == 364
    assert coord.data.site.pv_power_total == 1461
    assert coord.data.site.always_on_power == 188

    coord.apply_mqtt_properties(
        "servicelocation/parent/power",
        {
            "serviceLocationId": "263713",
            "consumptionPower": 0,
            "solarPower": 0,
            "alwaysOn": 0,
        },
    )

    assert coord.data.site.house_consumption_power == 0
    assert coord.data.site.pv_power_total == 0
    assert coord.data.site.always_on_power == 0


@pytest.mark.parametrize(
    ("indexes", "payload", "expected_phases", "expected_total", "expected_currents"),
    [
        (
            (0,),
            {"channelData": [321], "currentData": [1000]},
            [321],
            321,
            [1.0],
        ),
        (
            (2, 0, 1),
            {"channelData": [100, 200, 300], "currentData": [1000, 2000, 3000]},
            [300, 100, 200],
            600,
            [3.0, 1.0, 2.0],
        ),
        (
            (0, 2, 5),
            {"channelData": [10, 20, 30], "currentData": [1000, 2000, 3000]},
            None,
            None,
            None,
        ),
    ],
)
def test_site_power_mapping_matrix_handles_phase_shapes(
    hass, indexes, payload, expected_phases, expected_total, expected_currents
):
    topic = "servicelocation/site/power"
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
        highlevel_configs={},
    )
    coord.data = SiteData(site=SiteState())
    coord._power_index_maps_by_topic = coord._build_measurement_index_maps_by_topic_from_highlevel(
        {
            "measurements": [
                {
                    "type": "GRID",
                    "updateChannels": {
                        "activePower": _channel(topic, "channelData", *indexes),
                        "current": _channel(topic, "currentData", *indexes),
                    },
                }
            ]
        }
    )

    assert coord._handle_power(topic, payload) is (expected_total is not None)

    site = coord.data.site
    assert site.grid_power_phases == expected_phases
    assert site.grid_power_total == expected_total
    assert site.grid_current_phases == expected_currents


def test_site_power_mapping_empty_arrays_preserve_existing_state(hass):
    topic = "servicelocation/site/power"
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
        highlevel_configs={},
    )
    coord.data = SiteData(site=SiteState(grid_power_total=999, grid_power_phases=[9, 9, 9]))
    coord._power_index_maps_by_topic = coord._build_measurement_index_maps_by_topic_from_highlevel(
        {
            "measurements": [
                {
                    "type": "GRID",
                    "updateChannels": {
                        "activePower": _channel(topic, "channelData", 0, 1, 2),
                    },
                }
            ]
        }
    )

    assert coord._handle_power(topic, {"channelData": []}) is False
    assert coord.data.site.grid_power_total == 999
    assert coord.data.site.grid_power_phases == [9, 9, 9]


def test_site_power_index_map_empty_or_invalid_highlevel_config_is_safe(hass):
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
        highlevel_configs={},
    )
    coord.data = SiteData(site=SiteState(grid_power_total=123))

    assert coord._build_measurement_index_maps_by_topic_from_highlevel_configs({}) is None
    mapping = coord._build_measurement_index_maps_by_topic_from_highlevel_configs(
        {
            100: {
                "measurements": [
                    "bad",
                    {"type": "GRID", "updateChannels": "bad"},
                    {
                        "type": "GRID",
                        "updateChannels": {
                            "activePower": {
                                "protocol": "MQTT",
                                "name": "servicelocation/site/power",
                                "aspectPaths": [{"path": "$.wrong"}],
                            }
                        },
                    },
                ]
            }
        }
    )
    assert mapping["servicelocation/site/power"]["grid"]["power"] == []
    coord._power_index_maps_by_topic = mapping
    assert coord._handle_power("servicelocation/site/power", {"channelData": [999]}) is False
    assert coord.data.site.grid_power_total == 123
    assert coord.data.site.grid_power_total == 123


def test_power_apply_debug_line_only_logs_new_payload_shapes(hass, caplog):
    """Power frames arrive every ~5 s; repeat frames must not repeat the debug line."""
    coord = _station_coordinator(hass)
    coord._power_index_maps_by_topic = {}
    caplog.set_level(logging.DEBUG, logger="custom_components.smappee_ev.coordinators.power")

    def apply_count(payload: dict) -> int:
        caplog.clear()
        coord._handle_power("servicelocation/site/power", payload)
        return caplog.text.count("Smappee MQTT power apply")

    assert apply_count({"channelData": [1]}) == 1
    assert apply_count({"channelData": [2]}) == 0
    assert apply_count({"channelData": [3], "solarPower": 5}) == 1


def test_station_coordinator_highlevel_power_map_applies_station_and_connector_values(hass):
    coord = _station_coordinator(hass)
    topic = "servicelocation/control/power"
    cfg = {
        "measurements": [
            {
                "type": "GRID",
                "updateChannels": {
                    "activePower": _channel(topic, "channelData", 0, 1, 2),
                    "current": _channel(topic, "currentData", 0, 1, 2),
                    "meterReadings": _channel(topic, "importActiveEnergyData", 0, 1, 2),
                },
            },
            {
                "type": "PRODUCTION",
                "updateChannels": {
                    "activePower": _channel(topic, "channelData", 3, 4, 5),
                    "current": _channel(topic, "currentData", 3, 4, 5),
                    "meterReadings": _channel(topic, "importActiveEnergyData", 3, 4, 5),
                },
            },
            {
                "type": "APPLIANCE",
                "category": "CAR_CHARGER",
                "name": "EV Wall - 2",
                "updateChannels": {
                    "activePower": _channel(topic, "channelData", 6, 7, 8),
                    "current": _channel(topic, "currentData", 6, 7, 8),
                    "meterReadings": _channel(topic, "importActiveEnergyData", 6, 7, 8),
                },
            },
        ]
    }

    coord._power_index_maps_by_topic = (
        coord._build_measurement_index_maps_by_topic_from_highlevel_configs({200: cfg})
    )
    changed = coord._handle_power(
        topic,
        {
            "channelData": [100, 200, 300, 10, 20, 30, 700, 800, 900],
            "currentData": [1000, 2000, 3000, 400, 500, 600, 7000, 8000, 9000],
            "phaseVoltageData": [2300, 2310, 2320],
            "importActiveEnergyData": [1000, 2000, 3000, 4000, 5000, 6000, 1234, 1234, 1234],
            "exportActiveEnergyData": [100, 200, 300],
            "consumptionPower": 0,
            "solarPower": 0,
        },
    )

    assert changed is True
    station = coord.data.station
    conn = coord.data.connectors["conn-2"]
    assert station.grid_power_total == 600
    assert station.pv_power_total == 0
    assert station.house_consumption_power == 0
    assert conn.power_phases == [700, 800, 900]
    assert conn.power_total == 2400
    assert conn.current_phases == [7.0, 8.0, 9.0]
    assert conn.energy_import_kwh == 1.234


def test_station_shared_topic_merges_grid_pv_and_connector_without_overwrite(hass):
    coord = _station_coordinator(hass)
    topic = "servicelocation/shared/power"
    coord._power_index_maps_by_topic = (
        coord._build_measurement_index_maps_by_topic_from_highlevel_configs(
            {
                100: {
                    "measurements": [
                        {
                            "type": "GRID",
                            "updateChannels": {
                                "activePower": _channel(topic, "channelData", 0, 1, 2),
                            },
                        }
                    ]
                },
                200: {
                    "measurements": [
                        {
                            "type": "PRODUCTION",
                            "updateChannels": {
                                "activePower": _channel(topic, "channelData", 3, 4, 5),
                            },
                        },
                        {
                            "type": "APPLIANCE",
                            "category": "CAR_CHARGER",
                            "name": "EV Wall - 1",
                            "updateChannels": {
                                "activePower": _channel(topic, "activePowerData", 0, 1, 2),
                            },
                        },
                    ]
                },
            }
        )
    )

    changed = coord._handle_power(
        topic,
        {
            "channelData": [100, 200, 300, 10, 20, 30],
            "activePowerData": [700, 800, 900],
        },
    )

    assert changed is True
    assert coord.data.station.grid_power_total == 600
    assert coord.data.station.pv_power_total == 60
    assert coord.data.connectors["conn-1"].power_total == 2400
    assert coord.data.connectors["conn-2"].power_total is None


def test_station_power_payload_preserves_missing_current_and_energy(hass):
    coord = _station_coordinator(hass)
    topic = "servicelocation/control/power"
    coord.data.connectors["conn-1"].current_phases = [1.0, 2.0, 3.0]
    coord.data.connectors["conn-1"].energy_import_kwh = 12.3
    coord._power_index_maps_by_topic = (
        coord._build_measurement_index_maps_by_topic_from_highlevel_configs(
            {
                200: {
                    "measurements": [
                        {
                            "type": "APPLIANCE",
                            "category": "CAR_CHARGER",
                            "name": "EV Wall - 1",
                            "updateChannels": {
                                "activePower": _channel(topic, "activePowerData", 0, 1, 2),
                                "current": _channel(topic, "currentData", 0, 1, 2),
                                "meterReadings": _channel(topic, "importActiveEnergyData", 0, 1, 2),
                            },
                        }
                    ]
                }
            }
        )
    )

    assert coord._handle_power(topic, {"activePowerData": [100, 200, 300]}) is True

    connector = coord.data.connectors["conn-1"]
    assert connector.power_total == 600
    assert connector.current_phases == [1.0, 2.0, 3.0]
    assert connector.energy_import_kwh == 12.3


def test_station_highlevel_ignores_ambiguous_connector_and_uses_single_fallback(hass):
    coord = _station_coordinator(hass)
    topic = "servicelocation/control/power"
    mapping = coord._build_measurement_index_maps_by_topic_from_highlevel_configs(
        {
            200: {
                "measurements": [
                    "bad",
                    {"type": "APPLIANCE", "category": "CAR_CHARGER", "updateChannels": "bad"},
                    {
                        "type": "APPLIANCE",
                        "category": "CAR_CHARGER",
                        "name": "EV Wall",
                        "updateChannels": {"activePower": _channel(topic, "channelData", 0, 1, 2)},
                    },
                ]
            }
        }
    )

    assert mapping is None

    only_client = MagicMock(spec=SmappeeDeviceHandle)
    only_client.connector_number = None
    coord_one = SmappeeStationCoordinator(
        hass,
        station_client=coord.station_client,
        connector_clients={"sole-connector": only_client},
        update_interval=60,
    )
    coord_one.data = IntegrationData(
        station=StationState(),
        connectors={"sole-connector": ConnectorState(connector_number=1)},
    )

    mapping = coord_one._build_measurement_index_maps_by_topic_from_highlevel_configs(
        {
            200: {
                "measurements": [
                    {
                        "type": "APPLIANCE",
                        "category": "CAR_CHARGER",
                        "name": "EV Wall",
                        "updateChannels": {"activePower": _channel(topic, "channelData", 0, 1, 2)},
                    }
                ]
            }
        }
    )

    car_map = mapping[topic]["cars"]["sole-connector"]
    assert car_map["power"] == [0, 1, 2]
    assert car_map["position"] is None


def test_station_highlevel_connector_name_match_activates_after_dashboard_merge(hass):
    """Regression test for #251's real multi-connector, multi-location case.

    Reproduces the exact production sequence: (1) car-charger measurements
    carry no identifiers and no recognizable position, so with 2 known
    connectors neither the identifier nor the single-connector fallback
    applies and the mapping stays completely empty (matches the real bug
    reporter's ``topics: []``); (2) once ``ConnectorState.dashboard_device_name``
    is populated (as `_merge_dashboard_station_details` does from the
    charging-station-details REST call) and the highlevel mapping is rebuilt,
    the name-based resolver matches both measurements to the correct
    connector via an exact, unambiguous device-name match.
    """
    coord = _station_coordinator(hass)
    topic = "servicelocation/control/power"
    cfg = {
        "measurements": [
            {
                "type": "APPLIANCE",
                "category": "CAR_CHARGER",
                "name": "Garage Charger",
                "updateChannels": {"activePower": _channel(topic, "channelData", 0, 1, 2)},
            },
            {
                "type": "APPLIANCE",
                "category": "CAR_CHARGER",
                "name": "Driveway Charger",
                "updateChannels": {"activePower": _channel(topic, "channelData", 3, 4, 5)},
            },
        ]
    }

    # Step 1: no identifiers, no matchable position, 2 connectors known, and
    # dashboard_device_name not populated yet -> fully unresolved, no topic.
    mapping = coord._build_measurement_index_maps_by_topic_from_highlevel_configs({200: cfg})
    assert mapping is None
    for meas in cfg["measurements"]:
        resolution = coord._resolve_highlevel_measurement(meas)
        assert resolution.method == "unresolved"
        assert resolution.connector_uuid is None

    # Step 2: Dashboard details merge populates device names per connector.
    coord.data.connectors["conn-1"].dashboard_device_name = "Garage Charger"
    coord.data.connectors["conn-2"].dashboard_device_name = "Driveway Charger"

    # Step 3: rebuild the highlevel mapping -> name-based resolution kicks in.
    resolution_1 = coord._resolve_highlevel_measurement(cfg["measurements"][0])
    assert resolution_1.method == "unique_dashboard_name"
    assert resolution_1.connector_uuid == "conn-1"
    assert resolution_1.name_match_count == 1

    resolution_2 = coord._resolve_highlevel_measurement(cfg["measurements"][1])
    assert resolution_2.method == "unique_dashboard_name"
    assert resolution_2.connector_uuid == "conn-2"

    coord._power_index_maps_by_topic = (
        coord._build_measurement_index_maps_by_topic_from_highlevel_configs({200: cfg})
    )
    cars = coord._power_index_maps_by_topic[topic]["cars"]
    assert set(cars) == {"conn-1", "conn-2"}
    assert cars["conn-1"]["power"] == [0, 1, 2]
    assert cars["conn-2"]["power"] == [3, 4, 5]


def test_station_highlevel_connector_name_match_rejects_ambiguous_duplicate_names(hass):
    """An identical Dashboard device name on two connectors must not match."""
    coord = _station_coordinator(hass)
    coord.data.connectors["conn-1"].dashboard_device_name = "Charger"
    coord.data.connectors["conn-2"].dashboard_device_name = "Charger"

    resolution = coord._resolve_highlevel_measurement({"type": "APPLIANCE", "name": "Charger"})

    assert resolution.method == "unresolved"
    assert resolution.connector_uuid is None
    assert resolution.name_match_count == 2


def test_station_full_production_chain_two_connectors_dashboard_name_resolution(hass):
    """End-to-end regression test for #251 through the *real* production chain.

    Reproduces the exact real-world fixture shape reported by the bug
    reporter: 2 ``ConnectorState``s with Dashboard device names populated (as
    `_merge_dashboard_station_details` does), 2 highlevel APPLIANCE
    measurements carrying only a matching ``name`` - no uuid/smartDeviceUuid/
    deviceUuid, no explicit position/connectorNumber field, and no trailing
    number in the name - and connector 2 additionally using a non-sequential
    phase-label order for its ``current`` channel (aspectPaths listed
    B, C, A instead of A, B, C, as seen on some real Smappee accounts).

    Drives the whole thing through the exact live code path: the highlevel
    config is fed into
    ``_build_measurement_index_maps_by_topic_from_highlevel`` (which calls the
    shared resolver and writes the resolved channel indexes into
    ``topic_map["cars"]``), the resulting map is applied to an actual MQTT
    payload via ``_handle_power``, and the resulting per-connector
    power/current/energy values are asserted - so this test would fail if
    *any* step of the real chain regressed, not just the resolver in
    isolation.
    """
    coord = _station_coordinator(hass)
    coord.data.connectors["conn-1"].dashboard_device_name = "Garage Charger"
    coord.data.connectors["conn-2"].dashboard_device_name = "Driveway Charger"

    topic = "servicelocation/control/power"
    cfg = {
        "measurements": [
            {
                "type": "APPLIANCE",
                "category": "CAR_CHARGER",
                "name": "Garage Charger",
                "updateChannels": {
                    "activePower": _channel(topic, "channelData", 0, 1, 2),
                    "current": _channel(topic, "currentData", 0, 1, 2),
                    "meterReadings": _channel(topic, "importActiveEnergyData", 0, 1, 2),
                },
            },
            {
                "type": "APPLIANCE",
                "category": "CAR_CHARGER",
                "name": "Driveway Charger",
                "updateChannels": {
                    "activePower": _channel(topic, "channelData", 3, 4, 5),
                    # Real-world phase-label order mismatch: B, C, A instead of A, B, C.
                    "current": _channel(topic, "currentData", 4, 5, 3),
                    "meterReadings": _channel(topic, "importActiveEnergyData", 3, 4, 5),
                },
            },
        ]
    }

    # No identifiers, no explicit/trailing position on either measurement -> only
    # the unique-Dashboard-name resolver strategy can resolve these.
    for meas in cfg["measurements"]:
        resolution = coord._resolve_highlevel_measurement(meas)
        assert resolution.method == "unique_dashboard_name"
        assert resolution.name_match_count == 1

    mapping = coord._build_measurement_index_maps_by_topic_from_highlevel_configs({200: cfg})
    cars = mapping[topic]["cars"]
    assert set(cars) == {"conn-1", "conn-2"}
    assert cars["conn-1"]["power"] == [0, 1, 2]
    assert cars["conn-2"]["power"] == [3, 4, 5]
    # Channel indexes are written in the exact (mismatched) aspectPath order.
    assert cars["conn-2"]["current"] == [4, 5, 3]

    coord._power_index_maps_by_topic = mapping

    payload = {
        "channelData": [500, 510, 520, 700, 710, 720],
        "currentData": [111, 112, 113, 3333, 1111, 2222],
        "importActiveEnergyData": [5000, 5000, 5000, 8000, 8000, 8000],
    }
    assert coord._handle_power(topic, payload) is True

    conn1 = coord.data.connectors["conn-1"]
    conn2 = coord.data.connectors["conn-2"]

    assert conn1.power_phases == [500, 510, 520]
    assert conn1.power_total == 1530
    assert conn1.current_phases == [0.111, 0.112, 0.113]
    assert conn1.energy_import_kwh == 5.0

    assert conn2.power_phases == [700, 710, 720]
    assert conn2.power_total == 2130
    # Picked in the exact order the (mismatched) aspectPaths declared: idx 4, 5, 3.
    assert conn2.current_phases == [1.111, 2.222, 3.333]
    assert conn2.energy_import_kwh == 8.0


@pytest.mark.asyncio
async def test_station_power_index_map_cache_and_dashboard_loading(hass):
    coord = _station_coordinator(hass)
    dashboard = MagicMock()
    dashboard.async_get_highlevel_configuration = AsyncMock(return_value=None)
    coord.dashboard_client = dashboard
    coord._power_index_maps_by_topic = {"cached": {}}

    await coord._ensure_power_index_map()
    dashboard.async_get_highlevel_configuration.assert_not_awaited()

    coord._power_index_maps_by_topic = None
    coord.dashboard_client = None
    await coord._ensure_power_index_map()
    assert coord._power_index_maps_by_topic is None

    coord.dashboard_client = dashboard
    await coord._ensure_power_index_map()
    assert coord._power_index_maps_by_topic is None

    topic = "servicelocation/control/power"
    dashboard.async_get_highlevel_configuration.return_value = {
        "measurements": [
            {
                "type": "GRID",
                "updateChannels": {"activePower": _channel(topic, "channelData", 0)},
            }
        ]
    }
    coord._power_map_retry_after = 0.0
    await coord._ensure_power_index_map()

    assert topic in coord._power_index_maps_by_topic
