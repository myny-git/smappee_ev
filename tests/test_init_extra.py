"""Additional tests for the Smappee EV integration __init__ module."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
import pytest

from custom_components.smappee_ev import (
    SmappeeCoordinator,
    _create_coordinators,
    _prepare_site,
    _setup_mqtt,
)
from custom_components.smappee_ev.const import UPDATE_INTERVAL_DEFAULT
from custom_components.smappee_ev.discovery import MqttChannelSpec
from custom_components.smappee_ev.mqtt_gateway import SmappeeMqtt


@pytest.fixture
def mock_dashboard_handle():
    """Create a generic dashboard/runtime handle."""
    return MagicMock()


@pytest.fixture
def mock_session():
    """Create a mock aiohttp ClientSession."""
    session = MagicMock()
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock()
    mock_response.text = AsyncMock(return_value="")
    session.get = AsyncMock(return_value=mock_response)
    session.post = AsyncMock(return_value=mock_response)
    return session, mock_response


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
            "station1_uuid": {
                "station_client": mock_station_client,
                "connector_clients": {"connector1_uuid": mock_connector_client},
                "coordinator": None,
                "mqtt": None,
            }
        }

        # Mock SmappeeCoordinator
        with patch("custom_components.smappee_ev.SmappeeCoordinator") as mock_coordinator_class:
            # Create a mock coordinator instance
            mock_coordinator = MagicMock(spec=SmappeeCoordinator)
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()

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
            mock_coordinator.async_config_entry_first_refresh.assert_called_once()

            # Verify coordinator was assigned to station
            assert stations["station1_uuid"]["coordinator"] == mock_coordinator

    @pytest.mark.asyncio
    async def test_create_coordinators_with_config_entry(
        self, hass, mock_station_client, mock_connector_client
    ):
        """Test creating coordinators with config entry."""
        # Create test stations data
        stations = {
            "station1_uuid": {
                "station_client": mock_station_client,
                "connector_clients": {"connector1_uuid": mock_connector_client},
                "coordinator": None,
                "mqtt": None,
            }
        }

        # Create a mock config entry
        config_entry = MagicMock(spec=ConfigEntry)

        # Mock SmappeeCoordinator
        with patch("custom_components.smappee_ev.SmappeeCoordinator") as mock_coordinator_class:
            # Create a mock coordinator instance
            mock_coordinator = MagicMock(spec=SmappeeCoordinator)
            mock_coordinator.async_config_entry_first_refresh = AsyncMock()

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
        stations = {
            "station1_uuid": {
                "station_client": mock_station_client,
                "connector_clients": {},
                "coordinator": MagicMock(spec=SmappeeCoordinator),
                "mqtt": None,
            }
        }

        # Add data property to coordinator
        stations["station1_uuid"]["coordinator"].data = MagicMock()
        stations["station1_uuid"]["coordinator"].data.station = MagicMock()
        stations["station1_uuid"]["coordinator"].update_interval = UPDATE_INTERVAL_DEFAULT

        # Mock SmappeeMqtt
        with patch("custom_components.smappee_ev.SmappeeMqtt") as mock_mqtt_class:
            # Create a mock MQTT instance
            mock_mqtt = MagicMock(spec=SmappeeMqtt)
            mock_mqtt.start = AsyncMock()

            # Set the return value of the MQTT class
            mock_mqtt_class.return_value = mock_mqtt

            # Call _setup_mqtt with valid UUID
            result = _setup_mqtt(
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
                client_id="client-prefix-12345",
                serial_number="STATION-SERIAL",
                on_properties=mock_mqtt_class.call_args[1]["on_properties"],
                service_location_id=12345,
                on_connection_change=mock_mqtt_class.call_args[1]["on_connection_change"],
            )

            # Verify MQTT start was called by checking if result is not None
            # We can't verify if async_create_task was called since hass in pytest is a real object, not a mock

            # Polling remains enabled until MQTT actually connects.
            coord = stations["station1_uuid"]["coordinator"]
            assert coord.update_interval == UPDATE_INTERVAL_DEFAULT

            on_conn_callback = mock_mqtt_class.call_args[1]["on_connection_change"]
            on_conn_callback(True)
            assert coord.update_interval is None

            on_conn_callback(False)
            assert coord.update_interval == timedelta(seconds=UPDATE_INTERVAL_DEFAULT)

            # Verify MQTT was returned
            assert result == mock_mqtt

    def test_setup_mqtt_with_no_uuid(self, hass, mock_station_client):
        """Test setting up MQTT with no service location UUID."""
        # Create test stations data
        stations = {
            "station1_uuid": {
                "station_client": mock_station_client,
                "connector_clients": {},
                "coordinator": MagicMock(spec=SmappeeCoordinator),
                "mqtt": None,
            }
        }

        # Add data property to coordinator
        stations["station1_uuid"]["coordinator"].data = MagicMock()
        stations["station1_uuid"]["coordinator"].data.station = MagicMock()

        # Mock SmappeeMqtt
        with patch("custom_components.smappee_ev.SmappeeMqtt") as mock_mqtt_class:
            # Call _setup_mqtt with no UUID
            result = _setup_mqtt(
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
            "station1_uuid": {
                "station_client": mock_station_client,
                "connector_clients": {},
                "coordinator": MagicMock(spec=SmappeeCoordinator),
                "mqtt": None,
            }
        }
        specs = [
            MqttChannelSpec(1, "grid", "activePower", "servicelocation/site-a/power", "u", "p", []),
            MqttChannelSpec(
                2, "car_charger", "activePower", "servicelocation/site-b/power", "u", "p", []
            ),
        ]

        with patch("custom_components.smappee_ev.SmappeeMqtt") as mock_mqtt_class:
            mock_mqtt = MagicMock(spec=SmappeeMqtt)
            mock_mqtt.start = AsyncMock()
            mock_mqtt_class.return_value = mock_mqtt

            result = _setup_mqtt(
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
            "station1_uuid": {
                "station_client": mock_station_client,
                "connector_clients": {},
                "coordinator": MagicMock(spec=SmappeeCoordinator),
                "mqtt": None,
            }
        }
        specs = [
            MqttChannelSpec(
                1, "grid", "activePower", "servicelocation/site-a/power", "u1", "p1", []
            ),
            MqttChannelSpec(
                2, "car_charger", "activePower", "servicelocation/site-b/power", "u2", "p2", []
            ),
        ]

        with patch("custom_components.smappee_ev.SmappeeMqtt") as mock_mqtt_class:
            mqtt_a = MagicMock(spec=SmappeeMqtt)
            mqtt_b = MagicMock(spec=SmappeeMqtt)
            mqtt_a.start = AsyncMock()
            mqtt_b.start = AsyncMock()
            mock_mqtt_class.side_effect = [mqtt_a, mqtt_b]

            result = _setup_mqtt(
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

        stations = {"station1_uuid": {"coordinator": mock_coordinator, "mqtt": None}}

        # Mock SmappeeMqtt to capture the callback
        with patch("custom_components.smappee_ev.SmappeeMqtt") as mock_mqtt_class:
            # Call _setup_mqtt to get the callback
            _setup_mqtt(
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
            test_topic = "test/topic"
            test_payload = {"property": "value"}
            on_props_callback(test_topic, test_payload)

            # Verify coordinator methods were called
            mock_coordinator.apply_mqtt_properties.assert_called_once_with(test_topic, test_payload)
            mock_coordinator.async_set_updated_data.assert_not_called()

    def test_mqtt_on_connection_callback(self, hass):
        """Test the MQTT on_connection_change callback."""
        # Create test stations data with coordinator
        mock_coordinator = MagicMock(spec=SmappeeCoordinator)

        stations = {"station1_uuid": {"coordinator": mock_coordinator, "mqtt": None}}

        # Mock SmappeeMqtt to capture the callback
        with patch("custom_components.smappee_ev.SmappeeMqtt") as mock_mqtt_class:
            # Call _setup_mqtt to get the callback
            _setup_mqtt(
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
        mock_coordinator.update_interval = None
        mock_coordinator._shutting_down = True
        mock_coordinator.async_request_refresh = AsyncMock()
        mock_coordinator.apply_mqtt_connection_change = MagicMock()
        stations = {"station1_uuid": {"coordinator": mock_coordinator, "mqtt": None}}
        background_tasks = set()

        with patch("custom_components.smappee_ev.SmappeeMqtt") as mock_mqtt_class:
            mock_mqtt = MagicMock(spec=SmappeeMqtt)
            mock_mqtt.start = AsyncMock()
            mock_mqtt_class.return_value = mock_mqtt

            _setup_mqtt(
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
        mock_coordinator.async_request_refresh.assert_not_called()
        mock_coordinator.apply_mqtt_connection_change.assert_called_once_with(False)
        assert background_tasks == set()


class TestPrepareSite:
    """Test site preparation functions."""

    @pytest.mark.asyncio
    async def test_prepare_site_with_no_serial(self, hass, mock_dashboard_handle, mock_session):
        """Test prepare_site with no deviceSerialNumber."""
        session, _ = mock_session

        # Create a service location with no deviceSerialNumber
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "",  # Empty serial
            "name": "Test Location",
        }

        # Call _prepare_site
        result = await _prepare_site(
            hass,
            session,
            service_location,
            UPDATE_INTERVAL_DEFAULT,
            "client-prefix",
        )

        # Verify None was returned for both stations and MQTT
        assert result == (None, None)

    @pytest.mark.asyncio
    async def test_prepare_site_derives_missing_location_serial_from_station_device(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test prepare_site uses station smart-device serial when location serial is absent."""
        session, _ = mock_session
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.username = None
        dashboard_client.password = None

        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": None,
            "name": "Test Location",
        }
        station_devs = [
            {
                "uuid": "station1_uuid",
                "id": "station1_id",
                "serialNumber": "STATION1",
                "type": "CHARGINGSTATION",
            }
        ]
        stations = {
            "station1_uuid": {
                "station_client": MagicMock(),
                "connector_clients": {},
                "coordinator": None,
                "mqtt": None,
                "serial": "STATION1",
            }
        }

        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices", return_value=station_devs
            ),
            patch("custom_components.smappee_ev._split_devices", return_value=(station_devs, [])),
            patch(
                "custom_components.smappee_ev._fetch_dashboard_connector_mapping",
                return_value={},
            ),
            patch(
                "custom_components.smappee_ev._make_station_clients", return_value=stations
            ) as make_station_clients,
            patch("custom_components.smappee_ev._create_coordinators", new_callable=AsyncMock),
            patch(
                "custom_components.smappee_ev._setup_mqtt", return_value=MagicMock()
            ) as setup_mqtt,
        ):
            result, mqtt = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
                dashboard_client=dashboard_client,
            )

        assert result is stations
        assert mqtt is not None
        make_station_clients.assert_called_once_with("STATION1", 12345, station_devs)
        setup_mqtt.assert_called_once()
        assert setup_mqtt.call_args.args[2] == "STATION1"

    @pytest.mark.asyncio
    async def test_prepare_site_with_fetch_devices_error(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test prepare_site when fetch_devices returns None."""
        session, _ = mock_session

        # Create a service location with valid deviceSerialNumber
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "STATION-SERIAL",
            "name": "Test Location",
        }

        # Mock dashboard device discovery to return None (error)
        with patch("custom_components.smappee_ev._dashboard_fetch_devices", return_value=None):
            # Call _prepare_site
            result = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
            )

            # Verify None was returned for both stations and MQTT
            assert result == (None, None)

    @pytest.mark.asyncio
    async def test_prepare_site_with_no_stations_or_cars(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test prepare_site with no stations or car devices."""
        session, _ = mock_session

        # Create a service location with valid deviceSerialNumber
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "STATION-SERIAL",
            "name": "Test Location",
        }

        # Mock _fetch_devices and _split_devices
        with (
            patch("custom_components.smappee_ev._dashboard_fetch_devices", return_value=[]),
            patch("custom_components.smappee_ev._split_devices", return_value=([], [])),
        ):
            # Call _prepare_site
            result = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
            )

            # Verify None was returned for both stations and MQTT
            assert result == (None, None)

    @pytest.mark.asyncio
    async def test_prepare_site_with_no_stations(self, hass, mock_dashboard_handle, mock_session):
        """Test prepare_site with no stations but with car devices."""
        session, _ = mock_session

        # Create a service location with valid deviceSerialNumber
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "STATION-SERIAL",
            "name": "Test Location",
        }

        # Mock _fetch_devices and _split_devices
        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices",
                return_value=[{"type": "CARCHARGER"}],
            ),
            patch(
                "custom_components.smappee_ev._split_devices",
                return_value=([], [{"type": "CARCHARGER"}]),
            ),
        ):
            # Call _prepare_site
            result = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
            )

            # Verify None was returned for both stations and MQTT
            assert result == (None, None)

    @pytest.mark.asyncio
    async def test_prepare_site_success(self, hass, mock_dashboard_handle, mock_session):
        """Test successful site preparation."""
        session, _ = mock_session

        # Create a service location with valid deviceSerialNumber
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "STATION-SERIAL",
            "name": "Test Location",
        }

        # Create test devices
        station_devs = [
            {
                "uuid": "station1_uuid",
                "id": "station1_id",
                "serialNumber": "STATION1",
                "type": "CHARGINGSTATION",
            }
        ]

        car_devs = [{"uuid": "connector1_uuid", "id": "connector1_id", "type": "CARCHARGER"}]

        # Create station mapping
        station_mapping = {
            "STATION1": {"connectors": {"connector1_uuid": {"id": "conn1_id", "position": 1}}}
        }

        # Create stations data
        stations = {
            "station1_uuid": {
                "station_client": MagicMock(),
                "connector_clients": {"connector1_uuid": MagicMock()},
                "coordinator": MagicMock(),
                "mqtt": None,
                "serial": "STATION1",
            }
        }

        # Mock the required functions
        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices",
                return_value=[{"type": "CHARGINGSTATION"}, {"type": "CARCHARGER"}],
            ),
            patch(
                "custom_components.smappee_ev._split_devices", return_value=(station_devs, car_devs)
            ),
            patch(
                "custom_components.smappee_ev._fetch_dashboard_connector_mapping",
                return_value=station_mapping,
            ),
            patch("custom_components.smappee_ev._make_station_clients", return_value=stations),
            patch("custom_components.smappee_ev._assign_connectors"),
            patch("custom_components.smappee_ev._fallback_assign"),
            patch("custom_components.smappee_ev._create_coordinators"),
            patch("custom_components.smappee_ev._setup_mqtt", return_value=MagicMock()),
        ):
            # Call _prepare_site
            result, mqtt = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
            )

            # Verify stations was returned
            assert result is not None
            assert mqtt is not None

            # Verify stations have MQTT reference
            for station in stations.values():
                assert station["mqtt"] is not None

    @pytest.mark.asyncio
    async def test_prepare_site_with_station_filter(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test site preparation with station filtering based on allowed serials."""
        session, _ = mock_session

        # Create a service location with valid deviceSerialNumber
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "STATION-SERIAL",
            "name": "Test Location",
        }

        # Create test devices
        station_devs = [
            {
                "uuid": "station1_uuid",
                "id": "station1_id",
                "serialNumber": "STATION1",
                "type": "CHARGINGSTATION",
            },
            {
                "uuid": "station2_uuid",
                "id": "station2_id",
                "serialNumber": "STATION2",
                "type": "CHARGINGSTATION",
            },
        ]

        car_devs = [
            {"uuid": "connector1_uuid", "id": "connector1_id", "type": "CARCHARGER"},
            {"uuid": "connector2_uuid", "id": "connector2_id", "type": "CARCHARGER"},
        ]

        # Create station mapping with only STATION1 (filter out STATION2)
        station_mapping = {
            "STATION1": {"connectors": {"connector1_uuid": {"id": "conn1_id", "position": 1}}}
        }

        # Create stations data
        stations = {
            "station1_uuid": {
                "station_client": MagicMock(),
                "connector_clients": {},
                "coordinator": None,
                "mqtt": None,
                "serial": "STATION1",
            }
        }

        # Mock the required functions
        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices",
                return_value=station_devs + car_devs,
            ),
            patch(
                "custom_components.smappee_ev._split_devices", return_value=(station_devs, car_devs)
            ),
            patch(
                "custom_components.smappee_ev._fetch_dashboard_connector_mapping",
                return_value=station_mapping,
            ),
            patch("custom_components.smappee_ev._make_station_clients", return_value=stations),
            patch("custom_components.smappee_ev._assign_connectors"),
            patch("custom_components.smappee_ev._fallback_assign"),
            patch("custom_components.smappee_ev._create_coordinators"),
            patch("custom_components.smappee_ev._setup_mqtt", return_value=MagicMock()),
        ):
            # Call _prepare_site
            result, mqtt = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
            )

            # Verify stations was returned
            assert result is not None
            assert mqtt is not None

            # Verify only STATION1 is in stations
            assert "station1_uuid" in stations
            assert len(stations) == 1

    @pytest.mark.asyncio
    async def test_prepare_site_uses_mapping_station_serial_when_smartdevice_id_differs(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test station setup survives Dashboard mapping/station smartdevice ID mismatch."""
        session, _ = mock_session
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.username = None
        dashboard_client.password = None

        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "SITE-SERIAL",
            "name": "Test Location",
        }
        station_devs = [
            {
                "uuid": "station_smartdevice_uuid",
                "id": "station_smartdevice_id",
                "serialNumber": "SMARTDEVICE-STATION",
                "type": "CHARGINGSTATION",
            }
        ]
        station_mapping = {
            "REAL-STATION": {
                "connectors": {
                    "mqtt_connector_uuid": {
                        "id": "connector_id",
                        "position": 1,
                        "smart_device": {
                            "uuid": "dashboard_connector_uuid",
                            "id": "connector_id",
                            "type": {"category": "CARCHARGER"},
                            "carCharger": {
                                "chargingStateUpdateChannel": {
                                    "name": (
                                        "servicelocation/site/etc/carcharger/"
                                        "acchargingcontroller/v1/devices/"
                                        "mqtt_connector_uuid/property/chargingstate"
                                    )
                                }
                            },
                        },
                    }
                }
            }
        }

        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices",
                return_value=station_devs,
            ),
            patch("custom_components.smappee_ev._split_devices", return_value=(station_devs, [])),
            patch(
                "custom_components.smappee_ev._fetch_dashboard_connector_mapping",
                return_value=station_mapping,
            ),
            patch("custom_components.smappee_ev._create_coordinators", new_callable=AsyncMock),
            patch("custom_components.smappee_ev._setup_mqtt", return_value=None),
        ):
            result, mqtt = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
                dashboard_client=dashboard_client,
            )

        assert mqtt is None
        assert result is not None
        assert set(result) == {"REAL-STATION"}
        bucket = result["REAL-STATION"]
        assert bucket["serial"] == "REAL-STATION"
        assert set(bucket["connector_clients"]) == {"mqtt_connector_uuid"}
        assert bucket["connector_clients"]["mqtt_connector_uuid"].charging_station_serial == (
            "REAL-STATION"
        )

    @pytest.mark.asyncio
    async def test_prepare_site_rebuilds_empty_station_buckets_from_mapping(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test mapped connectors still load when station smartdevice metadata is incomplete."""
        session, _ = mock_session
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.username = None
        dashboard_client.password = None

        service_location = {
            "serviceLocationId": 236259,
            "serviceLocationUuid": "2454a357-9ded-4c30-a5ef-41a356fccf25",
            "deviceSerialNumber": "SITE-SERIAL",
            "name": "Test Location",
        }
        station_devs = [
            {
                "serialNumber": "REAL-STATION",
                "type": "CHARGINGSTATION",
            }
        ]
        connector = {
            "uuid": "dashboard_connector_uuid",
            "id": "connector_id",
            "type": {"category": "CARCHARGER"},
            "carCharger": {
                "chargingStateUpdateChannel": {
                    "name": (
                        "servicelocation/2454a357-9ded-4c30-a5ef-41a356fccf25/"
                        "etc/carcharger/acchargingcontroller/v1/devices/"
                        "aa6a3217-cc6a-44a8-8ff9-1ea67618ec15/property/chargingstate"
                    )
                }
            },
        }
        station_mapping = {
            "REAL-STATION": {
                "connectors": {
                    "aa6a3217-cc6a-44a8-8ff9-1ea67618ec15": {
                        "id": "connector_id",
                        "position": 1,
                        "smart_device": connector,
                    }
                }
            }
        }

        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices",
                return_value=station_devs,
            ),
            patch("custom_components.smappee_ev._split_devices", return_value=(station_devs, [])),
            patch(
                "custom_components.smappee_ev._fetch_dashboard_connector_mapping",
                return_value=station_mapping,
            ),
            patch("custom_components.smappee_ev._create_coordinators", new_callable=AsyncMock),
            patch("custom_components.smappee_ev._setup_mqtt", return_value=None),
        ):
            result, mqtt = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
                dashboard_client=dashboard_client,
            )

        assert mqtt is None
        assert result is not None
        assert set(result) == {"REAL-STATION"}
        bucket = result["REAL-STATION"]
        assert bucket["serial"] == "REAL-STATION"
        assert set(bucket["connector_clients"]) == {"aa6a3217-cc6a-44a8-8ff9-1ea67618ec15"}
        connector_client = bucket["connector_clients"]["aa6a3217-cc6a-44a8-8ff9-1ea67618ec15"]
        assert connector_client.charging_station_serial == "REAL-STATION"

    @pytest.mark.asyncio
    async def test_prepare_site_moves_unkeyed_mapping_to_service_serial(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test connector mappings with no station key still create a station bucket."""
        session, _ = mock_session
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.username = None
        dashboard_client.password = None

        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "SITE-SERIAL",
            "name": "Test Location",
        }
        station_devs = [
            {
                "uuid": "station_smartdevice_uuid",
                "id": "station_smartdevice_id",
                "serialNumber": "SMARTDEVICE-STATION",
                "type": "CHARGINGSTATION",
            }
        ]
        connector = {
            "uuid": "dashboard_connector_uuid",
            "id": "connector_id",
            "type": {"category": "CARCHARGER"},
            "carCharger": {
                "chargingStateUpdateChannel": {
                    "name": (
                        "servicelocation/site/etc/carcharger/acchargingcontroller/v1/"
                        "devices/aa6a3217-cc6a-44a8-8ff9-1ea67618ec15/property/chargingstate"
                    )
                }
            },
        }
        station_mapping = {
            None: {
                "connectors": {
                    "aa6a3217-cc6a-44a8-8ff9-1ea67618ec15": {
                        "id": "connector_id",
                        "position": 1,
                        "smart_device": connector,
                    }
                }
            }
        }

        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices",
                return_value=station_devs,
            ),
            patch("custom_components.smappee_ev._split_devices", return_value=(station_devs, [])),
            patch(
                "custom_components.smappee_ev._fetch_dashboard_connector_mapping",
                return_value=station_mapping,
            ),
            patch("custom_components.smappee_ev._create_coordinators", new_callable=AsyncMock),
            patch("custom_components.smappee_ev._setup_mqtt", return_value=None),
        ):
            result, mqtt = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
                dashboard_client=dashboard_client,
            )

        assert mqtt is None
        assert result is not None
        assert set(result) == {"SITE-SERIAL"}
        bucket = result["SITE-SERIAL"]
        assert bucket["serial"] == "SITE-SERIAL"
        assert set(bucket["connector_clients"]) == {"aa6a3217-cc6a-44a8-8ff9-1ea67618ec15"}

    @pytest.mark.asyncio
    async def test_prepare_site_treats_empty_metering_mapping_as_monitor_only(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test empty connector mappings do not trigger fallback connector assignment."""
        session, _ = mock_session
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "STATION-SERIAL",
            "name": "Test Location",
        }
        station_devs = [
            {
                "uuid": "station1_uuid",
                "id": "station1_id",
                "serialNumber": "STATION1",
                "type": "CHARGINGSTATION",
            }
        ]
        car_devs = [
            {
                "uuid": "connector1_uuid",
                "id": "connector1_id",
                "connectorNumber": 1,
                "type": "CARCHARGER",
            }
        ]
        stations = {
            "station1_uuid": {
                "station_client": MagicMock(),
                "connector_clients": {},
                "coordinator": None,
                "mqtt": None,
                "serial": "STATION1",
            }
        }

        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices",
                return_value=station_devs + car_devs,
            ),
            patch(
                "custom_components.smappee_ev._split_devices", return_value=(station_devs, car_devs)
            ),
            patch(
                "custom_components.smappee_ev._fetch_dashboard_connector_mapping",
                return_value={},
            ),
            patch("custom_components.smappee_ev._make_station_clients", return_value=stations),
            patch("custom_components.smappee_ev.SmappeeDeviceHandle", return_value=MagicMock()),
            patch("custom_components.smappee_ev._create_coordinators", new_callable=AsyncMock),
            patch("custom_components.smappee_ev._setup_mqtt", return_value=None),
        ):
            result, mqtt = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
            )

        assert result is stations
        assert mqtt is None
        assert stations["station1_uuid"]["connector_clients"] == {}

    @pytest.mark.asyncio
    async def test_prepare_site_skips_fallback_for_unassigned_multi_station_mapping(
        self, hass, mock_dashboard_handle, mock_session
    ):
        """Test fallback assignment is not used when multiple stations exist."""
        session, _ = mock_session
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "STATION-SERIAL",
            "name": "Test Location",
        }
        station_devs = [
            {
                "uuid": "station1_uuid",
                "id": "station1_id",
                "serialNumber": "STATION1",
                "type": "CHARGINGSTATION",
            },
            {
                "uuid": "station2_uuid",
                "id": "station2_id",
                "serialNumber": "STATION2",
                "type": "CHARGINGSTATION",
            },
        ]
        car_devs = [
            {
                "uuid": "connector1_uuid",
                "id": "connector1_id",
                "connectorNumber": 1,
                "type": "CARCHARGER",
            }
        ]
        station_mapping = {
            "STATION1": {"connectors": {"connector1_uuid": {"id": "unknown", "position": 1}}}
        }
        stations = {
            "station1_uuid": {
                "station_client": MagicMock(),
                "connector_clients": {},
                "coordinator": None,
                "mqtt": None,
                "serial": "STATION1",
            },
            "station2_uuid": {
                "station_client": MagicMock(),
                "connector_clients": {},
                "coordinator": None,
                "mqtt": None,
                "serial": "STATION2",
            },
        }

        with (
            patch(
                "custom_components.smappee_ev._dashboard_fetch_devices",
                return_value=station_devs + car_devs,
            ),
            patch(
                "custom_components.smappee_ev._split_devices", return_value=(station_devs, car_devs)
            ),
            patch(
                "custom_components.smappee_ev._fetch_dashboard_connector_mapping",
                return_value=station_mapping,
            ),
            patch("custom_components.smappee_ev._make_station_clients", return_value=stations),
            patch("custom_components.smappee_ev._assign_connectors") as assign_connectors,
            patch("custom_components.smappee_ev._fallback_assign") as fallback_assign,
            patch("custom_components.smappee_ev._create_coordinators", new_callable=AsyncMock),
            patch("custom_components.smappee_ev._setup_mqtt", return_value=None),
        ):
            result, mqtt = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
            )

        assert result is stations
        assert mqtt is None
        assign_connectors.assert_called_once()
        fallback_assign.assert_not_called()
        assert all(bucket["connector_clients"] == {} for bucket in stations.values())
