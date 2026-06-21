from types import SimpleNamespace

from custom_components.smappee_ev.const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT
from custom_components.smappee_ev.models import runtime_data
from custom_components.smappee_ev.models.runtime_data import (
    RuntimeData,
    SmappeeSiteRuntime,
    SmappeeStationRuntime,
)
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState


def test_connector_state_defaults_match_safe_ha_behavior():
    connector = ConnectorState(connector_number=1)

    assert connector.session_state == "Initialize"
    assert connector.available is True
    assert connector.api_available is True
    assert connector.paused is False
    assert connector.min_current == DEFAULT_MIN_CURRENT
    assert connector.max_current == DEFAULT_MAX_CURRENT


def test_station_state_mutable_defaults_are_not_shared():
    first = StationState()
    second = StationState()

    first.station_features.append("offline_charging")

    assert first.station_features == ["offline_charging"]
    assert second.station_features == []


def test_integration_data_recent_sessions_default_is_not_shared():
    first = IntegrationData(station=StationState(), connectors={})
    second = IntegrationData(station=StationState(), connectors={})

    first.recent_sessions.append({"id": "session-1"})

    assert first.recent_sessions == [{"id": "session-1"}]
    assert second.recent_sessions == []


def test_runtime_data_background_tasks_default_is_not_shared():
    first = RuntimeData(api=object(), sites={}, mqtt={})
    second = RuntimeData(api=object(), sites={}, mqtt={})
    task = object()

    first.background_tasks.add(task)

    assert first.background_tasks == {task}
    assert second.background_tasks == set()


def test_runtime_data_exports_mqtt_runtime_value_at_runtime():
    assert hasattr(runtime_data, "MqttRuntimeValue")


def test_runtime_data_accepts_single_and_multiple_mqtt_clients():
    single_mqtt = SimpleNamespace(name="single")
    multi_mqtt = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]
    station = SmappeeStationRuntime(
        site_location_id=123,
        control_location_id=456,
        site_name="Home",
        gateway_serial="gateway",
        gateway_type="Infinity",
        control_name="Station",
        control_uuid="station-uuid",
        control_function_type="CHARGINGSTATION",
        station_name="Garage",
        charging_station_serial="station-serial",
        charging_station_model="EV Wall",
        station_client=object(),
        station_coordinator=None,
        mqtt=single_mqtt,
    )
    site = SmappeeSiteRuntime(
        site_location_id=123,
        site_name="Home",
        site_function_type="SERVICELOCATION",
        site_uuid="site-uuid",
        gateway_serial="gateway",
        gateway_type="Infinity",
        mqtt_clients=multi_mqtt,
        stations={"station-uuid": station},
    )
    runtime = RuntimeData(
        api=object(),
        sites={123: site},
        mqtt={123: multi_mqtt, 456: single_mqtt},
    )

    assert station.mqtt is single_mqtt
    assert site.mqtt_clients is multi_mqtt
    assert runtime.mqtt == {123: multi_mqtt, 456: single_mqtt}
