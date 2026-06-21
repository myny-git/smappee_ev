"""Additional behavior coverage for coordinator mapping and merge helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from homeassistant.exceptions import ConfigEntryAuthFailed
import pytest

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.coordinator import (
    SmappeeSiteCoordinator,
    SmappeeStationCoordinator,
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


def test_site_mqtt_connection_change_handles_no_data_and_up_down_transitions(hass):
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
    )
    coord.async_set_updated_data = MagicMock()

    coord.apply_mqtt_connection_change(True)
    coord.async_set_updated_data.assert_not_called()

    coord.data = SiteData(site=SiteState())
    coord.apply_mqtt_connection_change(True)
    assert coord.data.site.mqtt_connected is True
    assert coord.data.site.last_mqtt_rx is not None
    coord.async_set_updated_data.assert_called_once_with(coord.data)

    coord.async_set_updated_data.reset_mock()
    coord.apply_mqtt_connection_change(True)
    coord.async_set_updated_data.assert_not_called()

    coord.apply_mqtt_connection_change(False)
    assert coord.data.site.mqtt_connected is False
    coord.async_set_updated_data.assert_called_once_with(coord.data)


def test_site_apply_mqtt_properties_without_data_is_safe(hass):
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
    )
    coord.async_set_updated_data = MagicMock()

    coord.apply_mqtt_properties("servicelocation/site/power", {"channelData": [1]})

    coord.async_set_updated_data.assert_not_called()


def test_site_coordinator_builds_and_applies_highlevel_grid_and_pv_maps(hass):
    topic = "servicelocation/site/power"
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
            "alwaysOnPower": 111,
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
            [10, 30, 0],
            40,
            [1.0, 3.0, 0.0],
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

    assert coord._handle_power(topic, payload) is True

    site = coord.data.site
    assert site.grid_power_phases == expected_phases
    assert site.grid_power_total == expected_total
    assert site.grid_current_phases == expected_currents


def test_site_power_mapping_empty_arrays_zero_fill_existing_state(hass):
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

    assert coord._handle_power(topic, {"channelData": []}) is True
    assert coord.data.site.grid_power_total == 0
    assert coord.data.site.grid_power_phases == [0, 0, 0]


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
    assert coord._handle_power("servicelocation/site/power", {"channelData": [999]}) is True
    assert coord.data.site.grid_power_total == 123


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


def test_station_power_payload_missing_current_and_energy_arrays_zero_fills_safely(hass):
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
    assert connector.current_phases == [0.0, 0.0, 0.0]
    assert connector.energy_import_kwh == 0.0


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
    await coord._ensure_power_index_map()

    assert topic in coord._power_index_maps_by_topic


def test_station_rest_merge_helpers_preserve_mqtt_rich_state():
    prev_station = StationState(led_brightness=25, dashboard_led_device_id="led-id")
    rest_station = StationState(led_brightness=None, api_available=False)
    merged_station = SmappeeStationCoordinator._merge_station_rest_state(prev_station, rest_station)

    assert SmappeeStationCoordinator._merge_station_rest_state(None, rest_station) is rest_station
    assert merged_station.led_brightness == 25
    assert merged_station.dashboard_led_device_id == "led-id"
    assert merged_station.api_available is False

    prev_connector = ConnectorState(
        connector_number=1,
        session_state="Charging",
        selected_current_limit=16,
        selected_mode="STANDARD",
        min_current=6,
        max_current=32,
        power_total=123,
    )
    rest_connector = ConnectorState(
        connector_number=2,
        session_state="Paused",
        selected_current_limit=None,
        selected_percentage_limit=80,
        selected_mode=None,
        min_current=8,
        max_current=24,
        support_grid=10,
        api_available=False,
    )
    merged_connector = SmappeeStationCoordinator._merge_connector_rest_state(
        prev_connector, rest_connector
    )

    assert (
        SmappeeStationCoordinator._merge_connector_rest_state(None, rest_connector)
        is rest_connector
    )
    assert merged_connector.connector_number == 2
    assert merged_connector.session_state == "Paused"
    assert merged_connector.selected_current_limit == 16
    assert merged_connector.selected_percentage_limit == 80
    assert merged_connector.selected_mode == "STANDARD"
    assert merged_connector.power_total == 123
    assert merged_connector.api_available is False


def test_station_dashboard_details_merge_led_connector_and_fallback_state(hass):
    coord = _station_coordinator(hass)
    details = {
        "available": False,
        "features": ["MAX_CURRENT"],
        "maximumCapacity": "32",
        "offlineCharging": {"enabled": True, "failSafe": "10"},
        "modules": [
            {
                "smartDevice": {
                    "id": "led-device",
                    "type": {"category": "LED"},
                    "configurationProperties": [
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.led.config.brightness"
                            },
                            "values": [{"Integer": 42}],
                        }
                    ],
                }
            },
            {
                "position": 1,
                "smartDevice": {
                    "id": "conn-device",
                    "uuid": "conn-1",
                    "name": "Cable 1",
                    "type": {"category": "CARCHARGER"},
                    "configurationProperties": [
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.config.max.current"
                            },
                            "values": [{"Quantity": {"value": "24", "unit": "A"}}],
                        },
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.config.min.current"
                            },
                            "value": {"value": "7"},
                        },
                        {
                            "spec": {
                                "name": "etc.smart.device.type.car.charger.config.min.excesspct"
                            },
                            "values": [{"Integer": "66"}],
                        },
                    ],
                    "carCharger": {
                        "connectionStatus": "CONNECTED",
                        "iecStatus": "C1",
                        "chargingMode": "SMART",
                        "optimizationStrategy": "SCHEDULES_FIRST_THEN_EXCESS",
                        "status": {"current": "CHARGING", "stoppedByCloud": True},
                    },
                },
            },
            {"smartDevice": {"type": {"category": "UNKNOWN"}}},
        ],
    }

    assert coord._merge_dashboard_station_details(coord.data, details) is True

    station = coord.data.station
    conn = coord.data.connectors["conn-1"]
    assert station.dashboard_available is False
    assert station.station_features == ["MAX_CURRENT"]
    assert station.maximum_capacity_a == 32
    assert station.offline_charging_enabled is True
    assert station.offline_failsafe_current_a == 10
    assert station.dashboard_led_device_id == "led-device"
    assert station.led_brightness == 42
    assert conn.dashboard_device_id == "conn-device"
    assert conn.dashboard_device_name == "Cable 1"
    assert conn.max_current == 24
    assert conn.min_current == 7
    assert conn.min_surpluspct == 66
    assert conn.connection_status == "CONNECTED"
    assert conn.evcc_state == "C"
    assert conn.evcc_state_code == 2
    assert conn.selected_mode == "SMART"
    assert conn.stopped_by_cloud is True


@pytest.mark.asyncio
async def test_station_dashboard_refresh_handles_partial_errors_and_auth_failures(hass):
    coord = _station_coordinator(hass)
    dashboard = MagicMock()
    dashboard.async_get_charging_station_details = AsyncMock(return_value={"available": True})
    dashboard.async_get_capacity_protection = AsyncMock(side_effect=RuntimeError("capacity down"))
    dashboard.async_get_overload_protection = AsyncMock(
        return_value={"active": True, "maximumLoad": 25}
    )
    dashboard.async_get_highlevel_configuration = AsyncMock(return_value=None)
    dashboard.async_get_appliances = AsyncMock(return_value=[])
    dashboard.async_get_load_management = AsyncMock(
        return_value={"optimizationStrategy": "EXCESS_ONLY"}
    )
    coord.dashboard_client = dashboard
    coord.data.connectors["conn-1"].dashboard_device_id = "device-1"

    assert await coord._maybe_refresh_dashboard_data(coord.data, force=True) is True
    assert coord.data.station.dashboard_available is True
    assert coord.data.station.overload_maximum_load_a == 25
    assert coord.data.connectors["conn-1"].selected_mode == "SOLAR"

    dashboard.async_get_charging_station_details.side_effect = ConfigEntryAuthFailed("reauth")
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._maybe_refresh_dashboard_data(coord.data, force=True)


@pytest.mark.asyncio
async def test_station_dashboard_scheduled_refresh_reauth_and_cleanup(hass):
    entry = MagicMock()
    coord = _station_coordinator(hass)
    coord.config_entry = entry
    coord._maybe_refresh_dashboard_data = AsyncMock(side_effect=ConfigEntryAuthFailed("reauth"))

    await coord._async_delayed_dashboard_refresh(0)

    entry.async_start_reauth.assert_called_once_with(hass)
    assert coord._dashboard_refresh_task is None


@pytest.mark.asyncio
async def test_station_dashboard_scheduled_refresh_skips_during_shutdown(hass):
    coord = _station_coordinator(hass)
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=True)

    coord.cancel_delayed_refreshes()
    await coord._async_delayed_dashboard_refresh(0)

    coord._maybe_refresh_dashboard_data.assert_not_called()
    assert coord._dashboard_refresh_task is None


def test_dashboard_delayed_refresh_uses_unsub_and_leaves_no_sleeping_task_on_shutdown(hass):
    coord = _station_coordinator(hass)
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=True)
    unsub = MagicMock()

    with patch("custom_components.smappee_ev.coordinator.async_call_later", return_value=unsub):
        coord.async_schedule_dashboard_refresh(delay=30)

    assert coord._dashboard_refresh_unsub is unsub
    assert coord._dashboard_refresh_task is None

    coord.cancel_delayed_refreshes()

    unsub.assert_called_once()
    assert coord._dashboard_refresh_unsub is None
    assert coord._dashboard_refresh_task is None
    coord._maybe_refresh_dashboard_data.assert_not_called()


@pytest.mark.asyncio
async def test_compat_dashboard_delayed_refresh_delegates_positive_delay(hass):
    coord = _station_coordinator(hass)
    coord._maybe_refresh_dashboard_data = AsyncMock(return_value=True)
    unsub = MagicMock()

    with patch(
        "custom_components.smappee_ev.coordinator.async_call_later",
        return_value=unsub,
    ):
        await coord._async_delayed_dashboard_refresh(30)

    assert coord._dashboard_refresh_unsub is unsub
    assert coord._dashboard_refresh_task is None
    coord._maybe_refresh_dashboard_data.assert_not_called()


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
    coord.async_set_updated_data = MagicMock()
    coord._async_get_recent_sessions = AsyncMock(return_value=[{"session": 1}])

    await coord._session_refresh_lock.acquire()
    try:
        await coord._async_refresh_recent_sessions("locked")
    finally:
        coord._session_refresh_lock.release()
    coord._async_get_recent_sessions.assert_not_awaited()

    monkeypatch.setattr("custom_components.smappee_ev.coordinator._now", lambda: 1000.0)
    coord._last_session_api_attempt = 999.0
    await coord._async_refresh_recent_sessions("throttled")
    coord._async_get_recent_sessions.assert_not_awaited()

    coord._last_session_api_attempt = 0
    coord.config_entry = MagicMock()
    coord._async_get_recent_sessions.side_effect = ConfigEntryAuthFailed("reauth")
    await coord._async_refresh_recent_sessions("reauth", force=True)
    coord.config_entry.async_start_reauth.assert_called_once_with(hass)

    coord._async_get_recent_sessions.side_effect = RuntimeError("offline")
    await coord._async_refresh_recent_sessions("offline", force=True)

    coord._async_get_recent_sessions.side_effect = None
    coord._async_get_recent_sessions.return_value = [{"session": 2}]
    await coord._async_refresh_recent_sessions("success", force=True)
    coord.async_set_updated_data.assert_called_once()
    assert coord._last_session_api_update == 1000.0


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
        patch("custom_components.smappee_ev.coordinator.SESSION_FINAL_REFRESH_DELAYS", (30, 120)),
        patch(
            "custom_components.smappee_ev.coordinator.async_call_later", side_effect=fake_call_later
        ),
        patch(
            "custom_components.smappee_ev.coordinator.async_track_time_interval",
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
