from datetime import UTC, datetime
from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy
import pytest

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState
from custom_components.smappee_ev.sensor import ConnectorSessionEnergySensor


def _make_sensor(connector_uuid: str = "conn-1") -> ConnectorSessionEnergySensor:
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.data = IntegrationData(
        station=StationState(),
        connectors={
            "conn-1": ConnectorState(connector_number=1),
            "conn-2": ConnectorState(connector_number=2),
        },
        recent_sessions=[
            {"connectorUuid": "conn-2", "energy": 8.1},
            {
                "connectorUuid": "conn-1",
                "energy": 3.4,
                "from": 1_781_273_310_000,
                "to": 1_781_273_910_000,
            },
        ],
    )
    coordinator.connector_clients = {"conn-1": MagicMock(), "conn-2": MagicMock()}
    api_client = MagicMock(spec=SmappeeDeviceHandle)
    api_client.serial = "SERIAL123"
    api_client.charging_station_serial = "STATION123"
    api_client.smart_device_id = "device-1"
    api_client.connector_number = 1
    return ConnectorSessionEnergySensor(coordinator, api_client, 1, "station", connector_uuid)


def test_session_energy_sensor_filters_by_connector_uuid():
    sensor = _make_sensor("conn-1")

    assert sensor.native_value == 3.4
    attrs = sensor.extra_state_attributes
    assert attrs["connectorUuid"] == "conn-1"
    assert attrs["from"] == datetime.fromtimestamp(1_781_273_310, tz=UTC).isoformat()
    assert attrs["to"] == datetime.fromtimestamp(1_781_273_910, tz=UTC).isoformat()
    assert attrs["duration_minutes"] == 10.0


def test_session_energy_sensor_metadata():
    sensor = _make_sensor()

    assert sensor._attr_device_class == SensorDeviceClass.ENERGY
    assert sensor._attr_native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
    assert sensor._attr_state_class == SensorStateClass.TOTAL


def test_session_energy_sensor_avoids_ambiguous_multi_connector_fallback():
    sensor = _make_sensor("unknown-connector")

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


@pytest.mark.parametrize("serial_key", ["serialNumber", "serial"])
@pytest.mark.parametrize("number_key", ["position", "label"])
@pytest.mark.parametrize("number", [1, "1"])
def test_session_energy_sensor_matches_nested_station_and_controller(
    serial_key, number_key, number
):
    sensor = _make_sensor()
    sensor.coordinator.data.recent_sessions = [
        {
            "station": {serial_key: "OTHER_STATION"},
            "controller": {number_key: number},
            "energy": 99,
        },
        {
            "station": {serial_key: "STATION123"},
            "controller": {number_key: 2},
            "energy": 88,
        },
        {
            "station": {serial_key: "STATION123"},
            "controller": {number_key: number},
            "energy": "3.456",
            "from": 1_781_273_310_000,
            "to": 1_781_273_910_000,
        },
    ]

    assert sensor.native_value == 3.46
    attrs = sensor.extra_state_attributes
    assert attrs["from"] == datetime.fromtimestamp(1_781_273_310, tz=UTC).isoformat()
    assert attrs["to"] == datetime.fromtimestamp(1_781_273_910, tz=UTC).isoformat()
    assert attrs["duration_minutes"] == 10.0
    assert attrs["duration_formatted"] == "00:10:00"
    assert "station" not in attrs
    assert "controller" not in attrs


@pytest.mark.parametrize(
    "session",
    [
        {
            "station": {"serialNumber": "OTHER_STATION"},
            "controller": {"position": 1},
            "energy": 99,
        },
        {
            "station": {"serialNumber": "STATION123"},
            "controller": {"position": 2},
            "energy": 88,
        },
        {"station": None, "controller": None, "energy": 99},
        {"station": {"serialNumber": "STATION123"}, "controller": {}, "energy": 99},
    ],
)
def test_session_energy_sensor_rejects_unmatched_nested_sessions(session):
    sensor = _make_sensor()
    sensor.coordinator.data.recent_sessions = [session]

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}
