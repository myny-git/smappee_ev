"""Additional tests for the Smappee EV integration __init__ module."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
import pytest

from custom_components.smappee_ev.api.discovery import MqttChannelSpec
from custom_components.smappee_ev.api.mqtt_gateway import SmappeeMqtt
from custom_components.smappee_ev.const import UPDATE_INTERVAL_DEFAULT
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.mqtt_setup import setup_mqtt
from custom_components.smappee_ev.runtime_assembly import _create_coordinators
from tests.factories import make_connector_runtime, make_station_runtime


@pytest.fixture
def mock_station_client():
    """Create a mock station client."""
    client = MagicMock()
    client.async_get_station_state = AsyncMock(return_value={})
    client.is_station = True
    return client


@pytest.fixture
def mock_connector_client():
    """Create a mock connector client."""
    client = MagicMock()
    client.async_get_connector_state = AsyncMock(return_value={})
    client.is_station = False
    client.connector_number = 1
    return client


class TestCoordinatorCreation:
    """Test coordinator creation functions."""

    @pytest.mark.asyncio
    async def test_create_coordinators(self, hass, mock_station_client, mock_connector_client):
        """Test creating coordinators for stations."""
        # Create test stations data
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=mock_station_client,
                coordinator=None,
                site_name=None,
                gateway_serial=None,
                gateway_type=None,
                station_name=None,
                station_model=None,
                connectors={
                    "connector1_uuid": make_connector_runtime(
                        connector_key="connector1_uuid",
                        connector_uuid="connector1_uuid",
                        connector_client=mock_connector_client,
                    )
                },
            )
        }

        # Mock SmappeeCoordinator
        with patch(
            "custom_components.smappee_ev.runtime_assembly.SmappeeCoordinator"
        ) as mock_coordinator_class:
            # Create a mock coordinator instance
            mock_coordinator = MagicMock(spec=SmappeeCoordinator)
            mock_coordinator.config_entry = None
            mock_coordinator.last_update_success = True
            mock_coordinator.async_refresh = AsyncMock()

            # Set the return value of the coordinator class
            mock_coordinator_class.return_value = mock_coordinator

            # Call _create_coordinators
            await _create_coordinators(hass, stations, UPDATE_INTERVAL_DEFAULT)

            # Verify coordinator was created and configured correctly
            mock_coordinator_class.assert_called_once_with(
                hass,
                station_client=mock_station_client,
                connector_clients={"connector1_uuid": mock_connector_client},
                update_interval=UPDATE_INTERVAL_DEFAULT,
                config_entry=None,
            )

            # Verify first refresh was called
            mock_coordinator.async_refresh.assert_called_once()

            # Verify coordinator was assigned to station
            assert stations["station1_uuid"].station_coordinator == mock_coordinator

    @pytest.mark.asyncio
    async def test_create_coordinators_with_config_entry(
        self, hass, mock_station_client, mock_connector_client
    ):
        """Test creating coordinators with config entry."""
        # Create test stations data
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=mock_station_client,
                coordinator=None,
                site_name=None,
                gateway_serial=None,
                gateway_type=None,
                station_name=None,
                station_model=None,
                connectors={
                    "connector1_uuid": make_connector_runtime(
                        connector_key="connector1_uuid",
                        connector_uuid="connector1_uuid",
                        connector_client=mock_connector_client,
                    )
                },
            )
        }

        # Create a mock config entry
        config_entry = MagicMock(spec=ConfigEntry)

        # Mock SmappeeCoordinator
        with patch(
            "custom_components.smappee_ev.runtime_assembly.SmappeeCoordinator"
        ) as mock_coordinator_class:
            # Create a mock coordinator instance
            mock_coordinator = MagicMock(spec=SmappeeCoordinator)
            mock_coordinator.config_entry = None
            mock_coordinator.last_update_success = True
            mock_coordinator.async_refresh = AsyncMock()

            # Set the return value of the coordinator class
            mock_coordinator_class.return_value = mock_coordinator

            # Call _create_coordinators with config_entry
            await _create_coordinators(
                hass, stations, UPDATE_INTERVAL_DEFAULT, config_entry=config_entry
            )

            # Verify coordinator was created with config_entry
            mock_coordinator_class.assert_called_once_with(
                hass,
                station_client=mock_station_client,
                connector_clients={"connector1_uuid": mock_connector_client},
                update_interval=UPDATE_INTERVAL_DEFAULT,
                config_entry=config_entry,
            )


class TestMqttSetup:
    """Test MQTT setup functions."""

    def test_setup_mqtt_with_valid_uuid(self, hass, mock_station_client):
        """Test setting up MQTT with a valid service location UUID."""
        # Create test stations data
        mock_coordinator = MagicMock(spec=SmappeeCoordinator)
        mock_coordinator.monitoring_only = False
        mock_coordinator._shutting_down = False
        mock_coordinator.hass = hass
        mock_coordinator._mqtt_connection_up = None
        mock_coordinator._is_stopping = False
        mock_coordinator.async_refresh.side_effect = lambda: setattr(
            mock_coordinator, "_force_smartdevice_refresh", False
        )
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=mock_station_client,
                coordinator=mock_coordinator,
                connectors={},
            )
        }

        # Add data property to coordinator
        mock_coordinator.data = MagicMock()
        mock_coordinator.data.station = MagicMock()
        expected_update_interval = timedelta(seconds=UPDATE_INTERVAL_DEFAULT)
        mock_coordinator.update_interval = expected_update_interval

        # Mock SmappeeMqtt
        with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mock_mqtt_class:
            # Create a mock MQTT instance
            mock_mqtt = MagicMock(spec=SmappeeMqtt)
            mock_mqtt.start = AsyncMock()

            # Set the return value of the MQTT class
            mock_mqtt_class.return_value = mock_mqtt

            # Call setup_mqtt with valid UUID
            result = setup_mqtt(
                hass,
                "test-service-uuid",
                "STATION-SERIAL",
                12345,
                stations,
                "client-prefix",
                UPDATE_INTERVAL_DEFAULT,
            )

            # Verify MQTT was created with correct parameters
            mock_mqtt_class.assert_called_once_with(
                service_location_uuid="test-service-uuid",
                tracking_serials=None,
                client_id="client-prefix-12345",
                serial_number="STATION-SERIAL",
                on_properties=mock_mqtt_class.call_args[1]["on_properties"],
                service_location_id=12345,
                on_connection_change=mock_mqtt_class.call_args[1]["on_connection_change"],
            )

            # Verify MQTT start was called by checking if result is not None
            # We can't verify if async_create_task was called since hass in pytest is a real object, not a mock

            # Polling remains enabled until MQTT actually connects.
            coord = stations["station1_uuid"].station_coordinator
            assert coord.update_interval == expected_update_interval

            on_conn_callback = mock_mqtt_class.call_args[1]["on_connection_change"]
            on_conn_callback(True)
            assert coord.update_interval == expected_update_interval

            on_conn_callback(False)
            assert coord.update_interval == expected_update_interval

            # Verify MQTT was returned
            assert result == mock_mqtt

    def test_setup_mqtt_with_no_uuid(self, hass, mock_station_client):
        """Test setting up MQTT with no service location UUID."""
        # Create test stations data
        mock_coordinator = MagicMock(spec=SmappeeCoordinator)
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=mock_station_client,
                coordinator=mock_coordinator,
                connectors={},
            )
        }

        # Add data property to coordinator
        mock_coordinator.data = MagicMock()
        mock_coordinator.data.station = MagicMock()

        # Mock SmappeeMqtt
        with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mock_mqtt_class:
            # Call setup_mqtt with no UUID
            result = setup_mqtt(
                hass,
                None,  # No UUID
                "STATION-SERIAL",
                12345,
                stations,
                "client-prefix",
                UPDATE_INTERVAL_DEFAULT,
            )

            # Verify MQTT was not created
            mock_mqtt_class.assert_not_called()

            # Verify None was returned
            assert result is None

    def test_setup_mqtt_groups_same_credentials_into_one_client(self, hass, mock_station_client):
        """Test MQTT specs with equal credentials use one MQTT client."""
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=mock_station_client,
                coordinator=MagicMock(spec=SmappeeCoordinator),
                connectors={},
            )
        }
        specs = [
            MqttChannelSpec(1, "grid", "activePower", "servicelocation/site-a/power", "u", "p", []),
            MqttChannelSpec(
                2, "car_charger", "activePower", "servicelocation/site-b/power", "u", "p", []
            ),
        ]

        with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mock_mqtt_class:
            mock_mqtt = MagicMock(spec=SmappeeMqtt)
            mock_mqtt.start = AsyncMock()
            mock_mqtt_class.return_value = mock_mqtt

            result = setup_mqtt(
                hass,
                "fallback-site",
                "STATION-SERIAL",
                12345,
                stations,
                "client-prefix",
                UPDATE_INTERVAL_DEFAULT,
                mqtt_specs=specs,
            )

            assert result == mock_mqtt
            assert mock_mqtt_class.call_count == 1
            assert mock_mqtt_class.call_args.kwargs["mqtt_specs"] == specs
            assert mock_mqtt_class.call_args.kwargs["service_location_uuids"] == [
                "site-a",
                "site-b",
            ]

    def test_setup_mqtt_splits_different_credentials_into_two_clients(
        self, hass, mock_station_client
    ):
        """Test MQTT specs with different credentials use separate MQTT clients."""
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=mock_station_client,
                coordinator=MagicMock(spec=SmappeeCoordinator),
                connectors={},
            )
        }
        specs = [
            MqttChannelSpec(
                1, "grid", "activePower", "servicelocation/site-a/power", "u1", "p1", []
            ),
            MqttChannelSpec(
                2, "car_charger", "activePower", "servicelocation/site-b/power", "u2", "p2", []
            ),
        ]

        with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mock_mqtt_class:
            mqtt_a = MagicMock(spec=SmappeeMqtt)
            mqtt_b = MagicMock(spec=SmappeeMqtt)
            mqtt_a.start = AsyncMock()
            mqtt_b.start = AsyncMock()
            mock_mqtt_class.side_effect = [mqtt_a, mqtt_b]

            result = setup_mqtt(
                hass,
                "fallback-site",
                "STATION-SERIAL",
                12345,
                stations,
                "client-prefix",
                UPDATE_INTERVAL_DEFAULT,
                mqtt_specs=specs,
            )

            assert result == [mqtt_a, mqtt_b]
            assert mock_mqtt_class.call_count == 2
            assert mock_mqtt_class.call_args_list[0].kwargs["mqtt_specs"] == [specs[0]]
            assert mock_mqtt_class.call_args_list[1].kwargs["mqtt_specs"] == [specs[1]]

    def test_mqtt_on_properties_callback(self, hass):
        """Test the MQTT on_properties callback."""
        # Create test stations data with coordinator
        mock_coordinator = MagicMock(spec=SmappeeCoordinator)
        mock_coordinator.data = MagicMock()
        mock_coordinator.data.station = MagicMock()
        mock_coordinator.apply_mqtt_properties = MagicMock()
        mock_coordinator.async_set_updated_data = MagicMock()

        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                coordinator=mock_coordinator,
                connectors={},
            )
        }

        # Mock SmappeeMqtt to capture the callback
        with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mock_mqtt_class:
            # Call setup_mqtt to get the callback
            setup_mqtt(
                hass,
                "test-service-uuid",
                "STATION-SERIAL",
                12345,
                stations,
                "client-prefix",
                UPDATE_INTERVAL_DEFAULT,
            )

            # Extract the on_properties callback
            on_props_callback = mock_mqtt_class.call_args[1]["on_properties"]

            # Test the callback
            test_topic = "servicelocation/test-service-uuid/power"
            test_payload = {"property": "value"}
            on_props_callback(test_topic, test_payload)

            # Verify coordinator methods were called
            mock_coordinator.apply_mqtt_properties.assert_called_once_with(test_topic, test_payload)
            mock_coordinator.async_set_updated_data.assert_not_called()

    def test_mqtt_on_connection_callback(self, hass):
        """Test the MQTT on_connection_change callback."""
        # Create test stations data with coordinator
        mock_coordinator = MagicMock(spec=SmappeeCoordinator)
        mock_coordinator.monitoring_only = False
        mock_coordinator._shutting_down = False
        mock_coordinator.hass = hass
        mock_coordinator._mqtt_connection_up = None
        mock_coordinator._is_stopping = False
        mock_coordinator.async_refresh.side_effect = lambda: setattr(
            mock_coordinator, "_force_smartdevice_refresh", False
        )

        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                coordinator=mock_coordinator,
                connectors={},
            )
        }

        # Mock SmappeeMqtt to capture the callback
        with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mock_mqtt_class:
            # Call setup_mqtt to get the callback
            setup_mqtt(
                hass,
                "test-service-uuid",
                "STATION-SERIAL",
                12345,
                stations,
                "client-prefix",
                UPDATE_INTERVAL_DEFAULT,
            )

            # Extract the on_connection_change callback
            on_conn_callback = mock_mqtt_class.call_args[1]["on_connection_change"]

            # Test the callback with connection up
            on_conn_callback(True)

            # Verify coordinator handles the connection state
            mock_coordinator.apply_mqtt_connection_change.assert_called_once_with(True)

            # Reset mocks
            mock_coordinator.apply_mqtt_connection_change.reset_mock()

            # Test the callback with connection down
            on_conn_callback(False)

            # Verify coordinator handles the connection state
            mock_coordinator.apply_mqtt_connection_change.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_mqtt_disconnect_during_shutdown_skips_fallback_refresh(self, hass):
        """Test disconnect fallback polling does not schedule work while shutting down."""
        mock_coordinator = MagicMock(spec=SmappeeCoordinator)
        mock_coordinator.monitoring_only = False
        mock_coordinator._shutting_down = False
        mock_coordinator.update_interval = None
        mock_coordinator._shutting_down = True
        mock_coordinator.async_refresh = AsyncMock()
        mock_coordinator.apply_mqtt_connection_change = MagicMock()
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                coordinator=mock_coordinator,
                connectors={},
            )
        }
        background_tasks = set()

        with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mock_mqtt_class:
            mock_mqtt = MagicMock(spec=SmappeeMqtt)
            mock_mqtt.start = AsyncMock()
            mock_mqtt_class.return_value = mock_mqtt

            setup_mqtt(
                hass,
                "test-service-uuid",
                "STATION-SERIAL",
                12345,
                stations,
                "client-prefix",
                UPDATE_INTERVAL_DEFAULT,
                background_tasks=background_tasks,
            )
            await hass.async_block_till_done()

            on_conn_callback = mock_mqtt_class.call_args.kwargs["on_connection_change"]
            on_conn_callback(False)

        assert mock_coordinator.update_interval == timedelta(seconds=UPDATE_INTERVAL_DEFAULT)
        mock_coordinator.async_refresh.assert_not_called()
        mock_coordinator.apply_mqtt_connection_change.assert_not_called()
        assert background_tasks == set()
