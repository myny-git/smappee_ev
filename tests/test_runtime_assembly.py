"""Runtime assembly: grouping MQTT specs and preparing site topologies."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.smappee_ev.api.discovery import MqttChannelSpec, SmappeeLocationTopology
from custom_components.smappee_ev.mqtt_setup import _build_mqtt_clients
from custom_components.smappee_ev.mqtt_specs import _group_mqtt_specs_by_credentials
from custom_components.smappee_ev.runtime_assembly import _prepare_site_topologies
from tests.factories import make_station_runtime


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

    groups = _group_mqtt_specs_by_credentials("fallback-uuid", specs)

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

    with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mqtt_cls:
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
async def test_prepare_site_topologies_builds_station_metadata_from_dashboard_and_highlevel(hass):
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
            "custom_components.smappee_ev.runtime_assembly._create_site_coordinator",
            AsyncMock(return_value=site_coord),
        ) as create_site,
        patch(
            "custom_components.smappee_ev.runtime_assembly._create_coordinators", AsyncMock()
        ) as create_stations,
        patch(
            "custom_components.smappee_ev.runtime_assembly._setup_mqtt", return_value=mqtt
        ) as setup_mqtt,
    ):
        site, mqtt_result = await _prepare_site_topologies(
            hass,
            [topology],
            update_interval=60,
            client_id_prefix="client",
            dashboard_client=dashboard,
            background_tasks=set(),
        )

    assert mqtt_result is mqtt
    assert site is not None
    stations = site.stations
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
    assert bucket.highlevel_configs.keys() == {200}
    assert create_site.await_args.kwargs["highlevel_configs"]
    assert create_stations.await_args.kwargs["highlevel_configs"]
    assert setup_mqtt.call_args.kwargs["mqtt_specs"]


@pytest.mark.asyncio
async def test_prepare_site_topologies_skips_non_charger_topology_without_serial(hass):
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

    assert await _prepare_site_topologies(
        hass,
        [topology],
        update_interval=60,
        client_id_prefix="client",
        dashboard_client=dashboard,
    ) == (None, None)


@pytest.mark.asyncio
async def test_prepare_site_topologies_mqtt_construction_failure_rolls_back_coordinators(hass):
    topology = SmappeeLocationTopology(
        site_location_id=100,
        site_location_uuid="site-uuid",
        site_name="Home",
        site_function_type="ELECTRICITY",
        control_location_id=200,
        control_location_uuid="control-uuid",
        control_name="Charger",
        control_function_type="CHARGINGSTATION",
        measurement_location_ids=[100],
        charging_station_serial="STATION-1",
        site_gateway_serial="GATEWAY-1",
        site_gateway_type="Genius",
        control_gateway_serial=None,
        control_gateway_type=None,
        write_access=True,
    )
    station_coordinator = MagicMock()
    station_coordinator.async_shutdown = AsyncMock(side_effect=RuntimeError("station cleanup"))
    site_coordinator = MagicMock()
    site_coordinator.async_shutdown = AsyncMock(side_effect=RuntimeError("site cleanup"))
    station = make_station_runtime(
        station_uuid="station-uuid",
        serial="STATION-1",
        coordinator=station_coordinator,
    )

    with (
        patch(
            "custom_components.smappee_ev.runtime_assembly._dashboard_fetch_highlevel_configs",
            AsyncMock(return_value={}),
        ),
        patch(
            "custom_components.smappee_ev.runtime_assembly._dashboard_fetch_devices",
            AsyncMock(return_value=[{"serialNumber": "STATION-1", "type": "CHARGINGSTATION"}]),
        ),
        patch(
            "custom_components.smappee_ev.runtime_assembly._fetch_dashboard_connector_mapping",
            AsyncMock(return_value={"STATION-1": {"connectors": {}}}),
        ),
        patch(
            "custom_components.smappee_ev.runtime_assembly."
            "_make_station_clients_with_mapping_fallback",
            return_value={"station-uuid": station},
        ),
        patch(
            "custom_components.smappee_ev.runtime_assembly._create_site_coordinator",
            AsyncMock(return_value=site_coordinator),
        ),
        patch(
            "custom_components.smappee_ev.runtime_assembly._create_coordinators",
            AsyncMock(),
        ),
        patch(
            "custom_components.smappee_ev.runtime_assembly._setup_mqtt",
            side_effect=RuntimeError("mqtt construction failed"),
        ),
        pytest.raises(RuntimeError, match="mqtt construction failed"),
    ):
        await _prepare_site_topologies(hass, [topology], 60, "client")

    station_coordinator.async_shutdown.assert_awaited_once()
    site_coordinator.async_shutdown.assert_awaited_once()
