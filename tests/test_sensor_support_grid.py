from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfElectricCurrent

from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.sensor import SmappeeSupportGridSensor
from custom_components.smappee_ev.state import ConnectorState, IntegrationData, StationState


def test_support_grid_sensor_native_value():
    connector_state = ConnectorState(connector_number=1, support_grid=4)
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.data = IntegrationData(
        station=StationState(led_brightness=70, available=True),
        connectors={"uuid": connector_state},
    )
    api_client = MagicMock(spec=SmappeeDeviceHandle)
    api_client.serial = "SERIAL123"
    api_client.connector_number = 1

    sensor = SmappeeSupportGridSensor(coordinator, api_client, 1, "station", "uuid")

    assert sensor.native_value == 4.0


def test_support_grid_sensor_metadata():
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.data = None
    api_client = MagicMock(spec=SmappeeDeviceHandle)
    api_client.serial = "SERIAL123"
    api_client.connector_number = 1

    sensor = SmappeeSupportGridSensor(coordinator, api_client, 1, "station", "uuid")

    assert sensor._attr_device_class == SensorDeviceClass.CURRENT
    assert sensor._attr_state_class == SensorStateClass.MEASUREMENT
    assert sensor._attr_native_unit_of_measurement == UnitOfElectricCurrent.AMPERE


def test_support_grid_sensor_native_value_none_without_state():
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.data = None
    api_client = MagicMock(spec=SmappeeDeviceHandle)
    api_client.serial = "SERIAL123"
    api_client.connector_number = 1

    sensor = SmappeeSupportGridSensor(coordinator, api_client, 1, "station", "uuid")

    assert sensor.native_value is None
