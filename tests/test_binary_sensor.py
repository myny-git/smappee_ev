# tests/test_binary_sensor.py
from unittest.mock import MagicMock, patch

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
import pytest

from custom_components.smappee_ev import binary_sensor
from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.binary_sensor import SmappeeMqttConnectivity
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.models.state import IntegrationData, StationState
from tests.factories import make_site_runtime, make_station_runtime


@pytest.fixture
def mock_station_state():
    """Create a mock station state."""
    return StationState(
        led_brightness=50,
        available=True,
        mqtt_connected=True,
        last_mqtt_rx=1234567890.0,
    )


@pytest.fixture
def mock_integration_data(mock_station_state):
    """Create mock integration data."""
    return IntegrationData(
        station=mock_station_state,
        connectors={},
    )


@pytest.fixture
def mock_coordinator(mock_integration_data):
    """Create a mock coordinator."""
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.data = mock_integration_data
    return coordinator


@pytest.fixture
def mock_api_client():
    """Create a mock API client."""
    api_client = MagicMock(spec=SmappeeDeviceHandle)
    api_client.serial = "SERIAL123"
    api_client.connector_number = None
    api_client.station_mode = True
    return api_client


@pytest.fixture
def mock_runtime_data(mock_coordinator, mock_api_client):
    """Create mock runtime data."""
    runtime = MagicMock(spec=RuntimeData)
    runtime.sites = {
        12345: make_site_runtime(
            site_location_id=12345,
            stations={
                "station_uuid_123": make_station_runtime(
                    site_location_id=12345,
                    control_location_id=12345,
                    station_uuid="station_uuid_123",
                    coordinator=mock_coordinator,
                    station_client=mock_api_client,
                    connectors={},
                )
            },
        )
    }
    return runtime


@pytest.fixture
def mock_config_entry(mock_runtime_data):
    """Create mock config entry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.runtime_data = mock_runtime_data
    entry.entry_id = "test_entry_id"
    return entry


class TestBinarySensorPlatform:
    """Test cases for binary_sensor platform."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self, hass: HomeAssistant, mock_config_entry):
        """Test binary_sensor platform setup."""
        async_add_entities = MagicMock()

        await binary_sensor.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Verify entities were added
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert all(isinstance(entity, BinarySensorEntity) for entity in entities)
        assert all(isinstance(entity, SmappeeMqttConnectivity) for entity in entities)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant, mock_config_entry):
        """Test binary_sensor platform setup with no sites."""
        mock_config_entry.runtime_data.sites = {}
        async_add_entities = MagicMock()

        await binary_sensor.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Coordinator was refreshed before platform setup.
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_sites(self, hass: HomeAssistant, mock_config_entry):
        """Test binary_sensor platform setup with empty sites."""
        mock_config_entry.runtime_data.sites = {}
        async_add_entities = MagicMock()

        await binary_sensor.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Coordinator was refreshed before platform setup.
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_stations(self, hass: HomeAssistant, mock_config_entry):
        """Test binary_sensor platform setup with empty stations."""
        mock_config_entry.runtime_data.sites = {12345: make_site_runtime(stations={})}
        async_add_entities = MagicMock()

        await binary_sensor.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Coordinator was refreshed before platform setup.
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_stations(self, hass: HomeAssistant, mock_config_entry):
        """Test binary_sensor platform setup with no stations."""
        mock_config_entry.runtime_data.sites = {12345: make_site_runtime(stations={})}
        async_add_entities = MagicMock()

        await binary_sensor.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Coordinator was refreshed before platform setup.
        async_add_entities.assert_called_once_with([], False)


class TestSmappeeMqttConnectivity:
    """Test cases for the MQTT connectivity binary sensor."""

    def test_init(self, mock_coordinator, mock_api_client):
        """Test initialization of MQTT connectivity sensor."""
        entity = SmappeeMqttConnectivity(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station_uuid_123",
        )

        # Check basic properties
        assert entity._attr_has_entity_name is True
        assert entity._attr_name == "MQTT Connected"
        assert entity._attr_device_class == BinarySensorDeviceClass.CONNECTIVITY
        assert entity._attr_entity_category == EntityCategory.DIAGNOSTIC
        assert entity.api_client == mock_api_client
        assert entity._sid == 12345
        assert entity._station_uuid == "station_uuid_123"

    def test_device_info(self, mock_coordinator, mock_api_client):
        """Test device_info property."""
        entity = SmappeeMqttConnectivity(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station_uuid_123",
        )

        # The device_info is actually a property on the parent class
        # We just want to verify it's accessible from our entity
        assert entity.device_info is not None

    def test_is_on_true(self, mock_coordinator, mock_api_client, mock_station_state):
        """Test is_on property when MQTT is connected."""
        mock_station_state.mqtt_connected = True
        entity = SmappeeMqttConnectivity(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station_uuid_123",
        )

        assert entity.is_on is True

    def test_is_on_false(self, mock_coordinator, mock_api_client, mock_station_state):
        """Test is_on property when MQTT is disconnected."""
        mock_station_state.mqtt_connected = False
        entity = SmappeeMqttConnectivity(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station_uuid_123",
        )

        assert entity.is_on is False

    def test_is_on_none(self, mock_coordinator, mock_api_client, mock_station_state):
        """Test is_on property when MQTT state is None."""
        mock_station_state.mqtt_connected = None
        entity = SmappeeMqttConnectivity(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station_uuid_123",
        )

        assert entity.is_on is False

    def test_is_on_no_data(self, mock_coordinator, mock_api_client):
        """Test is_on property when coordinator has no data."""
        mock_coordinator.data = None
        entity = SmappeeMqttConnectivity(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station_uuid_123",
        )

        assert entity.is_on is False

    def test_extra_state_attributes(self, mock_coordinator, mock_api_client):
        """Test extra_state_attributes property."""
        entity = SmappeeMqttConnectivity(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station_uuid_123",
        )

        # Mock the _serial attribute directly instead of patching station_serial
        entity._serial = "SERIAL123"
        attrs = entity.extra_state_attributes
        assert attrs["service_location_id"] == 12345
        assert attrs["station_serial"] == "SERIAL123"
        assert attrs["station_uuid"] == "station_uuid_123"

    def test_station_serial_helper(self, mock_coordinator):
        """Test _station_serial helper function."""
        with patch(
            "custom_components.smappee_ev.binary_sensor.station_serial", return_value="SERIAL123"
        ):
            result = binary_sensor._station_serial(mock_coordinator)
            assert result == "SERIAL123"
