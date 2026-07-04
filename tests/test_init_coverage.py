"""Focused coverage tests for setup helper behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from homeassistant.exceptions import ConfigEntryNotReady
import pytest

from custom_components.smappee_ev import (
    _async_shutdown_runtime_resources,
    _build_mqtt_clients,
    _create_dashboard_client,
    _dashboard_discover_service_locations,
    _dashboard_fetch_devices,
    _dashboard_fetch_highlevel_configs,
    _fallback_dashboard_connector_mapping,
    _fallback_highlevel_connector_mapping,
    _fetch_dashboard_connector_mapping,
    _find_in,
    _group_mqtt_specs_by_credentials,
    _is_connector,
    _load_dashboard_service_locations,
    _load_dashboard_topologies,
    _normalize_connector_mapping_station_keys,
    _prepare_site,
    _prepare_topology,
    _register_runtime_devices,
    _safe_str,
    _station_serial,
    _uuid_from_dashboard_channel,
    async_remove_config_entry_device,
    async_unload_entry,
)
from custom_components.smappee_ev.api.discovery import MqttChannelSpec, SmappeeLocationTopology
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from tests.factories import (
    make_connector_runtime,
    make_led_runtime,
    make_site_runtime,
    make_station_runtime,
)


def _configured_dashboard(**methods):
    dashboard = MagicMock()
    dashboard._token = "token"  # noqa: S105 - fake test token
    dashboard.refresh_token = None
    dashboard.username = None
    dashboard.password = None
    for name, value in methods.items():
        setattr(dashboard, name, value)
    return dashboard


def _spec(
    topic: str,
    *,
    sid: int = 1,
    role: str = "grid",
    username: str | None = None,
    password: str | None = None,
) -> MqttChannelSpec:
    return MqttChannelSpec(
        service_location_id=sid,
        role=role,
        metric="activePower",
        topic=topic,
        username=username,
        password=password,
        aspect_paths=[],
    )


def test_dashboard_device_parsing_helpers_handle_fallback_shapes():
    assert _safe_str(None) is None
    assert _safe_str(" none ") is None
    assert _safe_str(" null ") is None
    assert _safe_str(" SERIAL ") == "SERIAL"

    assert _is_connector({"carCharger": {}}) is True
    assert _is_connector({"type": {"category": "CARCHARGER"}}) is True
    assert _is_connector({"type": "CARCHARGER"}) is True
    assert _is_connector({"type": "SENSOR"}) is False

    assert (
        _find_in(
            {
                "configurationProperties": [
                    {
                        "spec": {"name": "device.serial.number"},
                        "value": {"value": "SERIAL-FROM-CONFIG"},
                    }
                ]
            },
            "serialNumber",
        )
        == "SERIAL-FROM-CONFIG"
    )
    assert (
        _find_in(
            {
                "properties": [
                    {
                        "spec": {"name": "deviceSerial"},
                        "value": "SERIAL-FROM-PROPS",
                    }
                ]
            },
            "serialNumber",
        )
        == "SERIAL-FROM-PROPS"
    )
    assert _find_in({"configurationProperties": [{"spec": {"name": "other"}}]}) is None


def test_dashboard_channel_uuid_helpers_extract_connector_and_station_ids():
    assert _uuid_from_dashboard_channel({"carCharger": "bad"}) is None
    assert (
        _uuid_from_dashboard_channel(
            {
                "carCharger": {
                    "chargingStateUpdateChannel": "servicelocation/site/devices/connector-uuid/property/state"
                }
            }
        )
        == "connector-uuid"
    )
    assert (
        _uuid_from_dashboard_channel(
            {
                "carCharger": {
                    "chargingStateUpdateChannel": {
                        "name": "servicelocation/site/devices/channel-uuid/property/state"
                    }
                }
            }
        )
        == "channel-uuid"
    )
    assert (
        _station_serial(
            {
                "serialNumber": " ",
                "uuid": "station-uuid",
            }
        )
        == "station-uuid"
    )


def test_group_mqtt_specs_by_credentials_uses_topic_uuid_and_explicit_credentials():
    specs = [
        _spec("servicelocation/topic-uuid/power", sid=11),
        _spec("servicelocation/topic-uuid/current", sid=11),
        _spec(
            "servicelocation/other-uuid/power",
            sid=22,
            username="user",
            password="pass",  # noqa: S106 - fake test password
        ),
    ]

    groups = _group_mqtt_specs_by_credentials("fallback-uuid", 99, specs)

    assert len(groups) == 2
    grouped_by_credentials = {
        (group[0][0].username or group[1][0], group[1][0]): group for group in groups
    }
    topic_group = grouped_by_credentials[("topic-uuid", "topic-uuid")]
    explicit_group = grouped_by_credentials[("user", "other-uuid")]
    assert [spec.topic for spec in topic_group[0]] == [
        "servicelocation/topic-uuid/power",
        "servicelocation/topic-uuid/current",
    ]
    assert topic_group[2] == {"topic-uuid": 11}
    assert explicit_group[2] == {"other-uuid": 22}


@pytest.mark.asyncio
async def test_dashboard_discovery_filters_then_falls_back_to_non_charging_locations():
    dashboard = _configured_dashboard(
        async_get_service_locations_full_details=AsyncMock(
            return_value=[
                "malformed",
                {"id": 1, "functionType": "SOLAR"},
                {"id": 2, "functionType": "SOLAR", "serialNumber": "GW-2"},
            ]
        )
    )

    locations = await _dashboard_discover_service_locations(dashboard)

    assert locations == [
        {
            "serviceLocationId": 1,
            "serviceLocationUuid": None,
            "deviceSerialNumber": None,
            "chargingStation": {},
            "functionType": "SOLAR",
            "name": None,
        },
        {
            "serviceLocationId": 2,
            "serviceLocationUuid": None,
            "deviceSerialNumber": "GW-2",
            "chargingStation": {},
            "functionType": "SOLAR",
            "name": None,
        },
    ]


@pytest.mark.asyncio
async def test_dashboard_load_helpers_raise_not_ready_for_missing_or_transient_data():
    unconfigured = MagicMock()
    unconfigured._token = None
    unconfigured.refresh_token = None
    unconfigured.username = None
    unconfigured.password = None

    with pytest.raises(ConfigEntryNotReady, match="Dashboard API is not configured"):
        await _load_dashboard_service_locations(unconfigured)

    failing = _configured_dashboard(
        async_get_service_locations_full_details=AsyncMock(side_effect=ClientError("down"))
    )
    with pytest.raises(ConfigEntryNotReady, match="Loading service locations failed"):
        await _load_dashboard_service_locations(failing)

    empty = _configured_dashboard(
        async_get_service_locations_full_details=AsyncMock(return_value=[])
    )
    with pytest.raises(ConfigEntryNotReady, match="No candidate charging topologies found"):
        await _load_dashboard_topologies(empty)


@pytest.mark.asyncio
async def test_dashboard_fetch_helpers_handle_errors_duplicates_and_bad_shapes():
    dashboard = _configured_dashboard(
        async_get_smart_devices=AsyncMock(side_effect=[RuntimeError("boom"), {"bad": "shape"}]),
        async_get_highlevel_configuration=AsyncMock(
            side_effect=[{"ok": 1}, RuntimeError("skip"), {"duplicate": "not called"}]
        ),
    )

    assert await _dashboard_fetch_devices(dashboard, 1) == []
    assert await _dashboard_fetch_devices(dashboard, 1) == []
    configs = await _dashboard_fetch_highlevel_configs(dashboard, [10, 20, 10])

    assert configs == {10: {"ok": 1}}
    assert dashboard.async_get_highlevel_configuration.await_count == 2


@pytest.mark.asyncio
async def test_fetch_dashboard_connector_mapping_extracts_station_led_and_connectors():
    station = {"serialNumber": "STATION-1", "type": "CHARGINGSTATION"}
    connector_device = {
        "id": "connector-device-id",
        "uuid": "connector-device-uuid",
        "type": {"category": "CARCHARGER"},
        "carCharger": {
            "chargingStateUpdateChannel": {
                "name": "servicelocation/site/etc/devices/connector-channel-uuid/property/chargingstate"
            }
        },
    }
    dashboard = _configured_dashboard(
        async_get_charging_station_details=AsyncMock(
            return_value={
                "name": "Garage Charger",
                "chargingStation": {"model": "EV Wall"},
                "modules": [
                    {
                        "position": 1,
                        "smartDevice": {
                            "id": "led-id",
                            "uuid": "led-uuid",
                            "name": "LED Ring",
                            "type": {"category": "LED"},
                        },
                    },
                    {"position": 2, "smartDevice": connector_device},
                    {"position": 3, "smartDevice": {"type": {"category": "SENSOR"}}},
                ],
            }
        )
    )

    mapping = await _fetch_dashboard_connector_mapping(dashboard, [station])

    assert mapping["STATION-1"]["station_model"] == "EV Wall"
    assert mapping["STATION-1"]["station_name"] == "Garage Charger"
    assert mapping["STATION-1"]["led_devices"]["led-id"]["uuid"] == "led-uuid"
    assert mapping["STATION-1"]["connectors"]["connector-channel-uuid"]["position"] == 2


@pytest.mark.asyncio
async def test_fetch_dashboard_connector_mapping_ignores_incomplete_dashboard_shapes():
    dashboard = _configured_dashboard(
        async_get_charging_station_details=AsyncMock(
            side_effect=[
                {"modules": []},
                ["not-a-dict"],
                {
                    "modules": [
                        "not-a-module",
                        {"position": 1},
                        {"position": 2, "smartDevice": {"type": {"category": "LED"}}},
                        {
                            "position": 3,
                            "smartDevice": {
                                "id": "non-connector",
                                "type": {"category": "SENSOR"},
                            },
                        },
                        {
                            "position": 4,
                            "smartDevice": {
                                "id": "connector-without-uuid",
                                "type": {"category": "CARCHARGER"},
                            },
                        },
                    ]
                },
            ]
        )
    )

    mapping = await _fetch_dashboard_connector_mapping(
        dashboard,
        [
            {"type": "CHARGINGSTATION"},
            {"serialNumber": "STATION-1", "type": "CHARGINGSTATION"},
            {"serialNumber": "STATION-2", "type": "CHARGINGSTATION"},
            {"serialNumber": "STATION-3", "type": "CHARGINGSTATION"},
        ],
    )

    assert mapping == {"STATION-1": {"connectors": {}}, "STATION-3": {"connectors": {}}}
    assert dashboard.async_get_charging_station_details.await_count == 3


def test_fallback_dashboard_connector_mapping_rejects_ambiguous_or_unserialed_stations():
    connector = {"uuid": "connector-uuid", "id": "connector-id", "type": "CARCHARGER"}

    assert (
        _fallback_dashboard_connector_mapping(
            [{"serialNumber": "STATION-1"}, {"serialNumber": "STATION-2"}],
            [connector],
        )
        == {}
    )
    assert _fallback_dashboard_connector_mapping([{"type": "CHARGINGSTATION"}], [connector]) == {}


def test_fallback_dashboard_connector_mapping_position_precedence_and_uuid_skip():
    mapping = _fallback_dashboard_connector_mapping(
        [{"serialNumber": "STATION-1"}],
        [
            {"id": "missing-uuid", "type": "CARCHARGER"},
            {
                "uuid": "connector-number-wins",
                "id": "connector-1",
                "connectorNumber": 2,
                "position": 1,
                "type": "CARCHARGER",
            },
            {
                "uuid": "position-wins-over-index",
                "id": "connector-2",
                "position": 3,
                "type": "CARCHARGER",
            },
            {
                "uuid": "index-fallback",
                "smartDeviceId": "connector-3",
                "type": "CARCHARGER",
            },
        ],
    )

    connectors = mapping["STATION-1"]["connectors"]
    assert set(connectors) == {
        "connector-number-wins",
        "position-wins-over-index",
        "index-fallback",
    }
    assert connectors["connector-number-wins"]["id"] == "connector-1"
    assert connectors["connector-number-wins"]["position"] == 2
    assert connectors["position-wins-over-index"]["position"] == 3
    assert connectors["index-fallback"]["id"] == "connector-3"
    assert connectors["index-fallback"]["position"] == 4


def test_fallback_highlevel_connector_mapping_uses_measurement_position_and_appliance_uuid():
    mapping = _fallback_highlevel_connector_mapping(
        "STATION-1",
        {
            1: {
                "measurements": [
                    {
                        "type": "APPLIANCE",
                        "name": "EV Wall - 2",
                        "appliance": {"type": "CAR_CHARGER", "uuid": "appliance-uuid"},
                    },
                    {"type": "GRID", "appliance": {"type": "CAR_CHARGER", "uuid": "ignored"}},
                ]
            }
        },
    )

    connector = mapping["STATION-1"]["connectors"]["appliance-uuid"]
    assert connector["position"] == 2
    assert connector["station_serial"] == "STATION-1"


def test_fallback_highlevel_connector_mapping_ignores_invalid_measurements():
    mapping = _fallback_highlevel_connector_mapping(
        "STATION-1",
        {
            1: {
                "measurements": [
                    "bad",
                    {"type": "GRID", "uuid": "grid-uuid"},
                    {"type": "APPLIANCE", "appliance": {"type": "FRIDGE", "uuid": "fridge"}},
                    {"type": "APPLIANCE", "category": "CAR_CHARGER"},
                ]
            }
        },
    )

    assert mapping == {}
    assert _fallback_highlevel_connector_mapping(None, {1: {"measurements": []}}) == {}


def test_fallback_highlevel_connector_mapping_uses_expected_uuid_fallback_order():
    def mapping_for(measurement):
        return _fallback_highlevel_connector_mapping(
            "STATION-1",
            {1: {"measurements": [measurement]}},
        )["STATION-1"]["connectors"]

    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "uuid": "measurement-uuid",
                "smartDeviceUuid": "measurement-smart-uuid",
                "appliance": {"type": "CAR_CHARGER", "uuid": "appliance-uuid"},
            }
        )
    ) == {"measurement-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "smartDeviceUuid": "measurement-smart-uuid",
                "deviceUuid": "measurement-device-uuid",
                "appliance": {"type": "CAR_CHARGER", "uuid": "appliance-uuid"},
            }
        )
    ) == {"measurement-smart-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "deviceUuid": "measurement-device-uuid",
                "appliance": {"type": "CAR_CHARGER", "uuid": "appliance-uuid"},
            }
        )
    ) == {"measurement-device-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "appliance": {
                    "type": "CAR_CHARGER",
                    "uuid": "appliance-uuid",
                    "smartDeviceUuid": "appliance-smart-uuid",
                },
            }
        )
    ) == {"appliance-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "appliance": {
                    "type": "CAR_CHARGER",
                    "smartDeviceUuid": "appliance-smart-uuid",
                    "deviceUuid": "appliance-device-uuid",
                },
            }
        )
    ) == {"appliance-smart-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "appliance": {"type": "CAR_CHARGER", "deviceUuid": "appliance-device-uuid"},
            }
        )
    ) == {"appliance-device-uuid"}


def test_normalize_connector_mapping_preserves_station_metadata():
    mapping = {
        "6220017988": {
            "station_name": "Garage charger",
            "station_model": "Smappee EV Wall",
            "led_serial": "5130086592",
            "connectors": {
                1: {"uuid": "connector-uuid"},
            },
        }
    }

    result = _normalize_connector_mapping_station_keys(mapping, None)

    assert result["6220017988"]["station_name"] == "Garage charger"
    assert result["6220017988"]["station_model"] == "Smappee EV Wall"
    assert result["6220017988"]["led_serial"] == "5130086592"
    assert result["6220017988"]["connectors"][1]["uuid"] == "connector-uuid"


def test_normalize_connector_mapping_preserves_orphan_metadata_on_fallback():
    mapping = {
        "": {
            "station_name": "Fallback charger",
            "station_model": "Smappee EV Wall",
            "connectors": {
                1: {"uuid": "connector-uuid"},
            },
        }
    }

    result = _normalize_connector_mapping_station_keys(
        mapping,
        fallback_station_serial="6220017988",
    )

    assert result["6220017988"]["station_name"] == "Fallback charger"
    assert result["6220017988"]["station_model"] == "Smappee EV Wall"
    assert result["6220017988"]["connectors"][1]["uuid"] == "connector-uuid"


def test_normalize_connector_mapping_keeps_existing_station_metadata_over_fallback():
    mapping = {
        "6220017988": {
            "station_name": "Explicit station",
            "connectors": {},
        },
        "": {
            "station_name": "Fallback station",
            "connectors": {
                1: {"uuid": "connector-uuid"},
            },
        },
    }

    result = _normalize_connector_mapping_station_keys(
        mapping,
        fallback_station_serial="6220017988",
    )

    assert result["6220017988"]["station_name"] == "Explicit station"
    assert result["6220017988"]["connectors"][1]["uuid"] == "connector-uuid"


def test_build_mqtt_clients_groups_specs_by_credentials_and_tracks_routes(hass):
    specs = [
        _spec("servicelocation/topic-a/power", sid=10),
        _spec(
            "servicelocation/topic-b/power",
            sid=20,
            username="user-b",
            password="pw-b",  # noqa: S106 - fake test password
        ),
    ]

    with patch("custom_components.smappee_ev.SmappeeMqtt") as mqtt_cls:
        mqtt_cls.side_effect = [MagicMock(name="mqtt-a"), MagicMock(name="mqtt-b")]
        clients = _build_mqtt_clients(
            suuid="fallback",
            serial_str="SERIAL",
            sid=99,
            client_id_prefix="client",
            on_properties=MagicMock(),
            on_connection_change=MagicMock(),
            mqtt_specs=specs,
        )

    assert len(clients) == 2
    assert mqtt_cls.call_args_list[0].kwargs["service_location_uuid"] == "topic-a"
    assert mqtt_cls.call_args_list[0].kwargs["service_location_ids_by_uuid"] == {"topic-a": 10}
    assert mqtt_cls.call_args_list[1].kwargs["service_location_uuid"] == "topic-b"
    assert mqtt_cls.call_args_list[1].kwargs["mqtt_specs"][0].username == "user-b"
    assert mqtt_cls.call_args_list[1].kwargs["mqtt_specs"][0].password == "pw-b"  # noqa: S105


@pytest.mark.asyncio
async def test_prepare_topology_builds_station_metadata_from_dashboard_and_highlevel(hass):
    topology = SmappeeLocationTopology(
        site_location_id=100,
        site_location_uuid="site-uuid",
        site_name="Home",
        site_function_type="ELECTRICITY",
        control_location_id=200,
        control_location_uuid="control-uuid",
        control_name="Charger",
        control_function_type="CHARGINGSTATION",
        measurement_location_ids=[100, 200],
        charging_station_serial="STATION-1",
        site_gateway_serial="GATEWAY-1",
        site_gateway_type="Genius",
        control_gateway_serial=None,
        control_gateway_type=None,
        write_access=True,
    )
    dashboard = _configured_dashboard(
        async_get_highlevel_configuration=AsyncMock(
            side_effect=[
                {
                    "measurements": [
                        {
                            "type": "GRID",
                            "updateChannels": {
                                "activePower": {
                                    "protocol": "MQTT",
                                    "name": "servicelocation/site-uuid/power",
                                    "aspectPaths": [{"path": "$.channelData[0]"}],
                                }
                            },
                        }
                    ]
                },
                {
                    "measurements": [
                        {
                            "type": "APPLIANCE",
                            "appliance": {"type": "CAR_CHARGER"},
                            "updateChannels": {
                                "activePower": {
                                    "protocol": "MQTT",
                                    "name": "servicelocation/control-uuid/power",
                                    "aspectPaths": [{"path": "$.channelData[3]"}],
                                }
                            },
                        }
                    ]
                },
            ]
        ),
        async_get_smart_devices=AsyncMock(
            return_value=[
                {
                    "id": "conn-id",
                    "type": {"category": "CARCHARGER"},
                    "carCharger": {
                        "chargingStateUpdateChannel": {
                            "name": "servicelocation/control-uuid/etc/devices/conn-uuid/property/chargingstate"
                        }
                    },
                }
            ]
        ),
        async_get_charging_station_details=AsyncMock(
            return_value={
                "name": "Garage Charger",
                "chargingStation": {"model": "EV Wall"},
                "modules": [
                    {
                        "position": 0,
                        "smartDevice": {
                            "id": "led-id",
                            "uuid": "led-uuid",
                            "name": "LED Ring",
                            "type": {"category": "LED"},
                        },
                    },
                    {
                        "position": 1,
                        "smartDevice": {
                            "id": "conn-id",
                            "uuid": "conn-uuid",
                            "type": {"category": "CARCHARGER"},
                            "carCharger": {
                                "chargingStateUpdateChannel": {
                                    "name": "servicelocation/site/etc/devices/conn-uuid/property/chargingstate"
                                }
                            },
                        },
                    },
                ],
            }
        ),
    )
    site_coord = MagicMock()
    mqtt = MagicMock()

    with (
        patch(
            "custom_components.smappee_ev._create_site_coordinator",
            AsyncMock(return_value=site_coord),
        ) as create_site,
        patch("custom_components.smappee_ev._create_coordinators", AsyncMock()) as create_stations,
        patch("custom_components.smappee_ev._setup_mqtt", return_value=mqtt) as setup_mqtt,
    ):
        stations, mqtt_result = await _prepare_topology(
            hass,
            topology,
            update_interval=60,
            client_id_prefix="client",
            dashboard_client=dashboard,
            background_tasks=set(),
        )

    assert mqtt_result is mqtt
    assert list(stations) == ["STATION-1"]
    bucket = stations["STATION-1"]
    assert bucket.site_location_id == 100
    assert bucket.control_location_id == 200
    assert bucket.station_name == "Garage Charger"
    assert bucket.charging_station_serial == "STATION-1"
    assert bucket.charging_station_model == "EV Wall"
    assert bucket.led_devices["led-id"].led_device_uuid == "led-uuid"
    assert bucket.led_devices["led-id"].led_device_name == "LED Ring"
    assert bucket.site_coordinator is site_coord
    assert bucket.highlevel_configs.keys() == {100, 200}
    assert create_site.await_args.kwargs["highlevel_configs"]
    assert create_stations.await_args.kwargs["highlevel_configs"]
    assert setup_mqtt.call_args.kwargs["mqtt_specs"]


@pytest.mark.asyncio
async def test_prepare_site_uses_service_serial_when_dashboard_has_only_connectors(hass):
    dashboard = _configured_dashboard(
        async_get_smart_devices=AsyncMock(
            return_value=[
                {
                    "id": "conn-id",
                    "uuid": "conn-uuid",
                    "type": {"category": "CARCHARGER"},
                    "connectorNumber": 1,
                }
            ]
        ),
        async_get_charging_station_details=AsyncMock(
            return_value={
                "modules": [
                    {
                        "position": 1,
                        "smartDevice": {
                            "id": "conn-id",
                            "uuid": "conn-uuid",
                            "type": {"category": "CARCHARGER"},
                            "carCharger": {
                                "chargingStateUpdateChannel": {
                                    "name": "servicelocation/site/etc/devices/conn-uuid/property/chargingstate"
                                }
                            },
                        },
                    }
                ]
            }
        ),
    )
    mqtt = MagicMock()

    with (
        patch("custom_components.smappee_ev._create_coordinators", AsyncMock()) as create_coords,
        patch("custom_components.smappee_ev._setup_mqtt", return_value=mqtt) as setup_mqtt,
    ):
        stations, mqtt_result = await _prepare_site(
            hass,
            MagicMock(),
            {
                "serviceLocationId": 300,
                "serviceLocationUuid": "site-uuid",
                "deviceSerialNumber": "GATEWAY-1",
                "name": "Garage",
            },
            update_interval=60,
            client_id_prefix="client",
            dashboard_client=dashboard,
        )

    assert mqtt_result is mqtt
    assert list(stations) == ["GATEWAY-1"]
    bucket = stations["GATEWAY-1"]
    assert bucket.charging_station_serial == "GATEWAY-1"
    assert set(bucket.connectors) == {"conn-uuid"}
    create_coords.assert_awaited_once()
    setup_mqtt.assert_called_once()


@pytest.mark.asyncio
async def test_prepare_site_skips_when_dashboard_devices_unavailable_or_no_serial(hass):
    dashboard = _configured_dashboard(async_get_smart_devices=AsyncMock(return_value=[]))

    with patch(
        "custom_components.smappee_ev._dashboard_fetch_devices", AsyncMock(return_value=None)
    ):
        assert await _prepare_site(
            hass,
            MagicMock(),
            {"serviceLocationId": 300},
            update_interval=60,
            client_id_prefix="client",
            dashboard_client=dashboard,
        ) == (None, None)

    assert await _prepare_site(
        hass,
        MagicMock(),
        {"serviceLocationId": 301, "name": "No Serial"},
        update_interval=60,
        client_id_prefix="client",
        dashboard_client=dashboard,
    ) == (None, None)


@pytest.mark.asyncio
async def test_prepare_topology_skips_non_charger_topology_without_serial(hass):
    topology = SmappeeLocationTopology(
        site_location_id=100,
        site_location_uuid="site-uuid",
        site_name="Home",
        site_function_type="ELECTRICITY",
        control_location_id=200,
        control_location_uuid="control-uuid",
        control_name="Monitor",
        control_function_type="ELECTRICITY",
        measurement_location_ids=[100],
        charging_station_serial=None,
        site_gateway_serial=None,
        site_gateway_type=None,
        control_gateway_serial=None,
        control_gateway_type=None,
        write_access=False,
    )
    dashboard = _configured_dashboard(
        async_get_highlevel_configuration=AsyncMock(return_value={}),
        async_get_smart_devices=AsyncMock(return_value=[]),
    )

    assert await _prepare_topology(
        hass,
        topology,
        update_interval=60,
        client_id_prefix="client",
        dashboard_client=dashboard,
    ) == (None, None)


def test_create_dashboard_client_persists_refresh_token_updates():
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {"username": "user", "password": "pass"}

    client = _create_dashboard_client(hass, entry, MagicMock())
    client._token_update_callback({"dashboard_refresh_token": "new-refresh"})

    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        data={
            "username": "user",
            "password": "pass",
            "dashboard_refresh_token": "new-refresh",
        },
    )


def test_register_runtime_devices_creates_site_station_and_connector_devices():
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    hass.config_entries.async_get_entry.return_value = entry
    station_client = MagicMock()
    station_client.serial_id = "LEGACY-SERIAL"
    connector_client = MagicMock()
    connector_client.connector_number = 2
    entry.runtime_data = RuntimeData(
        api=MagicMock(),
        mqtt={},
        sites={
            100: make_site_runtime(
                site_location_id=100,
                site_name="Home",
                gateway_serial="GATEWAY-1",
                gateway_type="Genius",
                stations={
                    "station-uuid": make_station_runtime(
                        site_location_id=100,
                        control_location_id=200,
                        station_uuid="station-uuid",
                        serial="STATION-1",
                        station_client=station_client,
                        station_name="Garage Charger",
                        station_model="EV Wall",
                        led_devices={"led-id": make_led_runtime(led_key="led-id")},
                        connectors={
                            "conn-uuid": make_connector_runtime(
                                connector_key="conn-uuid",
                                connector_uuid="conn-uuid",
                                connector_position=2,
                                connector_client=connector_client,
                            )
                        },
                    )
                },
            )
        },
    )
    registry = MagicMock()

    with (
        patch("custom_components.smappee_ev.dr.async_get", return_value=registry),
        patch("custom_components.smappee_ev.dr.async_entries_for_config_entry", return_value=[]),
    ):
        _register_runtime_devices(hass, entry)

    identifiers = [
        call.kwargs["identifiers"] for call in registry.async_get_or_create.call_args_list
    ]
    flattened = {identifier for group in identifiers for _, identifier in group}
    assert "site:100" in flattened
    assert "station:100:200:STATION-1" in flattened
    assert "100:LEGACY-SERIAL:station-uuid" in flattened
    assert "led:100:200:STATION-1:led-id" not in flattened
    assert "connector:100:200:STATION-1:conn-uuid" in flattened


@pytest.mark.asyncio
async def test_shutdown_runtime_resources_stops_mqtt_and_cancels_background_tasks():
    events: list[str] = []

    async def site_shutdown():
        events.append("site-shutdown")
        raise RuntimeError("site shutdown warning")

    async def station_shutdown():
        events.append("station-shutdown")

    async def mqtt_stop():
        events.append("mqtt-stop")
        assert station_coord.async_shutdown.await_count == 1

    def failing_mqtt_stop():
        events.append("failing-mqtt-stop")
        raise OSError("already closed")

    site_coord = MagicMock()
    site_coord.async_shutdown = AsyncMock(side_effect=site_shutdown)
    station_coord = MagicMock()
    station_coord.async_shutdown = AsyncMock(side_effect=station_shutdown)
    async_mqtt = MagicMock()
    async_mqtt.stop = AsyncMock(side_effect=mqtt_stop)
    failing_mqtt = MagicMock()
    failing_mqtt.stop.side_effect = failing_mqtt_stop
    pending = asyncio.create_task(asyncio.sleep(60))
    runtime = RuntimeData(
        api=MagicMock(),
        mqtt={100: [async_mqtt, failing_mqtt]},
        sites={
            100: make_site_runtime(
                site_coordinator=site_coord,
                stations={"station": make_station_runtime(coordinator=station_coord)},
            )
        },
        background_tasks={pending},
    )

    await _async_shutdown_runtime_resources(runtime)

    station_coord.async_shutdown.assert_awaited_once()
    async_mqtt.stop.assert_awaited_once()
    assert events == [
        "site-shutdown",
        "station-shutdown",
        "mqtt-stop",
        "failing-mqtt-stop",
    ]
    assert pending.cancelled()
    assert runtime.background_tasks == set()


@pytest.mark.asyncio
async def test_async_unload_entry_handles_missing_invalid_and_active_runtime(hass):
    class EntryWithoutRuntime:
        entry_id = "missing-runtime"

    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.services.async_register(DOMAIN, "start_charging", MagicMock())

    assert await async_unload_entry(hass, EntryWithoutRuntime()) is True
    assert hass.services.has_service(DOMAIN, "start_charging")

    invalid_entry = MagicMock()
    invalid_entry.entry_id = "invalid-runtime"
    invalid_entry.runtime_data = object()

    assert await async_unload_entry(hass, invalid_entry) is True
    assert hass.services.has_service(DOMAIN, "start_charging")

    hass.config_entries.async_unload_platforms.return_value = False
    assert await async_unload_entry(hass, invalid_entry) is False
    assert hass.services.has_service(DOMAIN, "start_charging")


@pytest.mark.asyncio
async def test_async_remove_config_entry_device_allows_only_stale_station_devices(hass):
    entry = MagicMock()
    station_client = MagicMock()
    station_client.serial_id = "LEGACY-1"
    entry.runtime_data = RuntimeData(
        api=MagicMock(),
        mqtt={},
        sites={
            10: make_site_runtime(
                site_location_id=10,
                stations={
                    "station-uuid": make_station_runtime(
                        site_location_id=10,
                        control_location_id=20,
                        station_uuid="station-uuid",
                        serial="",
                        station_client=station_client,
                    )
                },
            )
        },
    )

    foreign_device = MagicMock(identifiers={("other", "station:10:20:LEGACY-1")})
    current_device = MagicMock(identifiers={(DOMAIN, "station:10:20:LEGACY-1")})
    stale_device = MagicMock(identifiers={(DOMAIN, "station:10:20:OLD")})

    assert await async_remove_config_entry_device(hass, entry, foreign_device) is True
    assert await async_remove_config_entry_device(hass, entry, current_device) is False
    assert await async_remove_config_entry_device(hass, entry, stale_device) is True
