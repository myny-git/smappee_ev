"""Tests for Dashboard topology and highlevel MQTT parsing."""

from custom_components.smappee_ev.discovery import (
    build_topologies_from_full_details,
    parse_mqtt_channel_specs_from_highlevel,
    unique_mqtt_channel_specs,
)


def _mqtt_channel(topic: str, path: str, username: str | None = "user") -> dict:
    channel = {
        "protocol": "MQTT",
        "name": topic,
        "userName": username,
        "password": "secret",
        "aspectPaths": [{"path": path}],
    }
    if username is None:
        channel.pop("userName")
        channel.pop("password")
    return channel


def test_build_topology_default_with_charging_station():
    topologies = build_topologies_from_full_details(
        [
            {
                "id": 236259,
                "uuid": "site-uuid",
                "name": "Home",
                "functionType": "DEFAULT",
                "chargingStation": {"serialNumber": "STATION123"},
                "deviceSerialNumber": "GATEWAY123",
                "writeAccess": True,
            }
        ]
    )

    assert len(topologies) == 1
    topology = topologies[0]
    assert topology.site_location_id == 236259
    assert topology.control_location_id == 236259
    assert topology.measurement_location_ids == [236259]
    assert topology.charging_station_serial == "STATION123"
    assert topology.site_location_uuid == "site-uuid"
    assert topology.write_access is True


def test_build_topology_charging_park_parent_and_child():
    topologies = build_topologies_from_full_details(
        [
            {
                "id": 317418,
                "uuid": "park-uuid",
                "name": "Park",
                "functionType": "CHARGINGPARK",
                "deviceSerialNumber": "PARKGW",
            },
            {
                "id": 317443,
                "uuid": "station-uuid",
                "parentId": 317418,
                "name": "Station",
                "functionType": "CHARGINGSTATION",
                "chargingStation": {"serialNumber": "STATION123"},
                "deviceSerialNumber": "STGW",
            },
        ]
    )

    assert len(topologies) == 1
    topology = topologies[0]
    assert topology.site_location_id == 317418
    assert topology.control_location_id == 317443
    assert topology.measurement_location_ids == [317418, 317443]
    assert topology.site_location_uuid == "park-uuid"
    assert topology.control_location_uuid == "station-uuid"
    assert topology.site_gateway_serial == "PARKGW"
    assert topology.control_gateway_serial == "STGW"


def test_build_topology_uses_main_gateway_device_type():
    topologies = build_topologies_from_full_details(
        [
            {
                "id": 317418,
                "uuid": "park-uuid",
                "name": "Park",
                "type": "RESIDENTIAL",
                "functionType": "CHARGINGPARK",
                "gateways": [
                    {"serialNumber": "SECONDARY", "deviceType": "WIFI_CONNECT"},
                    {"serialNumber": "PARKGW", "deviceType": "P1S1", "role": "MAIN"},
                ],
            },
            {
                "id": 317443,
                "uuid": "station-uuid",
                "parentId": 317418,
                "type": "RESIDENTIAL",
                "functionType": "CHARGINGSTATION",
                "chargingStation": {"serialNumber": "STATION123"},
                "gateways": [
                    {
                        "serialNumber": "STGW",
                        "deviceType": "ETHERNET_CONNECT",
                        "role": "MAIN",
                    }
                ],
            },
        ]
    )

    topology = topologies[0]
    assert topology.site_gateway_serial == "PARKGW"
    assert topology.site_gateway_type == "P1S1"
    assert topology.control_gateway_serial == "STGW"
    assert topology.control_gateway_type == "ETHERNET_CONNECT"


def test_build_topology_missing_parent_uses_charger_as_site():
    topologies = build_topologies_from_full_details(
        [
            {
                "id": 317443,
                "parentId": 999999,
                "functionType": "CHARGINGSTATION",
                "chargingStation": {"serialNumber": "STATION123"},
            }
        ]
    )

    assert len(topologies) == 1
    assert topologies[0].site_location_id == 317443
    assert topologies[0].measurement_location_ids == [317443]


def test_build_topology_empty_uuid_is_none():
    topologies = build_topologies_from_full_details(
        [
            {
                "id": 236259,
                "uuid": "",
                "functionType": "DEFAULT",
                "chargingStation": {"serialNumber": "STATION123"},
            }
        ]
    )

    assert topologies[0].site_location_uuid is None


def test_build_topology_multiple_charging_stations_under_same_parent():
    topologies = build_topologies_from_full_details(
        [
            {"id": 1, "functionType": "CHARGINGPARK", "name": "Park"},
            {
                "id": 2,
                "parentId": 1,
                "functionType": "CHARGINGSTATION",
                "chargingStation": {"serialNumber": "A"},
            },
            {
                "id": 3,
                "parentId": 1,
                "functionType": "CHARGINGSTATION",
                "chargingStation": {"serialNumber": "B"},
            },
        ]
    )

    assert [topology.control_location_id for topology in topologies] == [2, 3]
    assert all(topology.site_location_id == 1 for topology in topologies)


def test_build_topology_accepts_alternate_ids_gateways_and_write_access():
    topologies = build_topologies_from_full_details(
        [
            {
                "serviceLocationId": "10",
                "uuid": "site-uuid",
                "name": "Site",
                "canWrite": True,
                "gatewayDevice": {"serial": "SITEGW", "type": "P1S1"},
            },
            {
                "locationId": "20",
                "parentId": "10",
                "name": "Station",
                "chargingstations": [{"serial": "STATION-ALT"}],
                "device": {"deviceSerialNumber": "CTRL-GW", "deviceType": "Connect"},
            },
            {"id": "bad"},
            "not-a-location",
        ]
    )

    assert len(topologies) == 1
    topology = topologies[0]
    assert topology.site_location_id == 10
    assert topology.control_location_id == 20
    assert topology.charging_station_serial == "STATION-ALT"
    assert topology.site_gateway_serial == "SITEGW"
    assert topology.site_gateway_type == "P1S1"
    assert topology.control_gateway_serial == "CTRL-GW"
    assert topology.control_gateway_type == "Connect"
    assert topology.write_access is True


def test_build_topology_ignores_invalid_parent_and_missing_station_payloads():
    topologies = build_topologies_from_full_details(
        [
            {"id": 1, "parentId": "not-an-int", "chargingStation": {"serial": "A"}},
            {"id": 2, "chargingStations": ["bad"]},
            {"id": 3, "chargingstation": "bad"},
        ]
    )

    assert len(topologies) == 1
    assert topologies[0].site_location_id == 1
    assert topologies[0].control_name == "Smappee 1"


def test_parse_highlevel_mqtt_specs_for_measurements_and_update_specs():
    config = {
        "measurements": [
            {
                "type": "GRID",
                "updateChannels": {
                    "activePower": _mqtt_channel("grid/power", "$.activePowerData[0]")
                },
            },
            {
                "type": "PRODUCTION",
                "actuals": [
                    {
                        "updateChannels": {
                            "activePower": _mqtt_channel("pv/power", "$.activePowerData[1]")
                        }
                    }
                ],
            },
            {
                "type": "APPLIANCE",
                "appliance": {"type": "CAR_CHARGER"},
                "updateChannels": {
                    "activePower": _mqtt_channel("car/power", "$.activePowerData[2]", None)
                },
            },
        ],
        "updateSpecs": {
            "consumption": {"channel": _mqtt_channel("consumption", "$.consumptionPower")},
            "production": {"channel": _mqtt_channel("production", "$.solarPower")},
            "alwaysOn": {"channel": _mqtt_channel("always-on", "$.alwaysOn")},
        },
    }

    specs = parse_mqtt_channel_specs_from_highlevel(123, config)
    assert [(spec.role, spec.metric, spec.topic) for spec in specs] == [
        ("grid", "activePower", "grid/power"),
        ("production", "activePower", "pv/power"),
        ("car_charger", "activePower", "car/power"),
        ("consumption", "consumption", "consumption"),
        ("production_total", "production", "production"),
        ("always_on", "alwaysOn", "always-on"),
    ]
    assert specs[2].username is None
    assert specs[2].password is None
    assert specs[0].aspect_paths == [{"path": "$.activePowerData[0]"}]


def test_unique_mqtt_channel_specs_deduplicates_topics():
    config = {
        "measurements": [
            {
                "type": "GRID",
                "updateChannels": {
                    "activePower": _mqtt_channel("same-topic", "$.activePowerData[0]"),
                    "current": _mqtt_channel("same-topic", "$.currentData[0]"),
                },
            }
        ]
    }

    specs = unique_mqtt_channel_specs(parse_mqtt_channel_specs_from_highlevel(123, config))

    assert len(specs) == 1
    assert specs[0].topic == "same-topic"


def test_parse_highlevel_mqtt_specs_filters_invalid_channels_and_duplicates():
    assert parse_mqtt_channel_specs_from_highlevel(1, None) == []

    config = {
        "measurements": [
            "bad",
            {"type": "UNKNOWN", "updateChannels": {"activePower": _mqtt_channel("x", "$.x")}},
            {
                "type": "GRID",
                "updateChannels": {
                    "activePower": _mqtt_channel("grid", "$.channelData[0]"),
                    "duplicatePower": _mqtt_channel("grid", "$.channelData[0]"),
                    "httpOnly": {"protocol": "HTTP", "name": "ignored"},
                    "missingTopic": {"protocol": "MQTT", "name": ""},
                    "badShape": "ignored",
                },
                "actuals": [
                    "bad",
                    {"updateChannels": {"current": _mqtt_channel("current", "$.i")}},
                ],
            },
            {
                "type": "APPLIANCE",
                "category": "OTHER",
                "updateChannels": {"activePower": _mqtt_channel("ignored-car", "$.x")},
            },
        ],
        "updateSpecs": {
            "consumption": "bad",
            "production": {"channel": "bad"},
            "alwaysOn": {"channel": _mqtt_channel("always", "$.always")},
        },
    }

    specs = parse_mqtt_channel_specs_from_highlevel(1, config)

    assert [(spec.role, spec.metric, spec.topic) for spec in specs] == [
        ("grid", "activePower", "grid"),
        ("grid", "duplicatePower", "grid"),
        ("grid", "current", "current"),
        ("always_on", "alwaysOn", "always"),
    ]
