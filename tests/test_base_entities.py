"""Test the Smappee EV base entities."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.helpers.update_coordinator import CoordinatorEntity
import pytest

from custom_components.smappee_ev.base_entities import (
    SmappeeBaseEntity,
    SmappeeConnectorEntity,
    SmappeeStationEntity,
    SmappeeStationRestEntity,
)
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.helpers import (
    make_connector_device_info,
    make_led_device_info,
    make_station_device_info,
    station_serial,
)
from custom_components.smappee_ev.state import ConnectorState, StationState


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.last_update_success = True

    # Mock station data
    station = MagicMock()
    station.name = "Test Station"
    station.mqtt_connected = True

    # Mock connector data
    connector = MagicMock()
    connector.status = "AVAILABLE"
    connector.power = 0

    # Setup coordinator data
    coordinator.data = MagicMock()
    coordinator.data.station = station
    coordinator.data.connectors = {"connector-uuid": connector}

    return coordinator


def test_scoped_device_info_hierarchy():
    """Test site/station child devices point at the expected parent."""
    station = make_station_device_info(
        317418,
        317443,
        "6230010364",
        station_model="WALL_QUANTUM_CABLE",
    )
    led = make_led_device_info(317418, 317443, "6230010364", "LED-controller-123")
    connector = make_connector_device_info(317418, 317443, "6230010364", "connector-uuid", "1")

    assert station["identifiers"] == {(DOMAIN, "station:317418:317443:6230010364")}
    assert station["via_device"] == (DOMAIN, "site:317418")
    assert led["via_device"] == (DOMAIN, "station:317418:317443:6230010364")
    assert connector["via_device"] == (DOMAIN, "station:317418:317443:6230010364")


def test_connector_device_info_uses_serial_fallback_without_station_name():
    """Connector device names should not expose a missing station name as None."""
    connector = make_connector_device_info(317418, 317443, "6230010364", "connector-uuid", "1")

    assert connector["name"] == "Smappee EV 6230010364 | Connector 1"


def test_station_serial_prefers_charging_station_serial():
    """Use the physical charger serial when the handle also has a gateway serial."""
    coordinator = SimpleNamespace(
        station_client=SimpleNamespace(
            serial_id="5130086592",
            charging_station_serial="6220017988",
        )
    )

    assert station_serial(coordinator) == "6220017988"


class TestSmappeeBaseEntity:
    """Test the SmappeeBaseEntity class."""

    def test_init(self, mock_coordinator):
        """Test entity initialization."""
        entity = SmappeeBaseEntity(mock_coordinator, 12345, "station-uuid")

        assert entity._sid == 12345
        assert entity._station_uuid == "station-uuid"
        assert entity._attr_has_entity_name is True
        assert isinstance(entity, CoordinatorEntity)

    @patch("custom_components.smappee_ev.base_entities.station_serial", return_value="SERIAL123")
    @patch("custom_components.smappee_ev.base_entities.make_device_info")
    def test_device_info(self, mock_make_device_info, mock_station_serial, mock_coordinator):
        """Test device_info property."""
        mock_make_device_info.return_value = {"identifiers": {("test", "device")}}

        entity = SmappeeBaseEntity(mock_coordinator, 12345, "station-uuid")
        device_info = entity.device_info

        # Verify device_info calls helper with correct parameters
        mock_make_device_info.assert_called_once_with(12345, "SERIAL123", "station-uuid")
        assert device_info == {"identifiers": {("test", "device")}}


class TestSmappeeStationEntity:
    """Test the SmappeeStationEntity class."""

    @patch("custom_components.smappee_ev.base_entities.make_unique_id", return_value="unique-id")
    def test_init(self, mock_make_unique_id, mock_coordinator):
        """Test entity initialization."""
        entity = SmappeeStationEntity(
            mock_coordinator, 12345, "station-uuid", "test-suffix", "Test Entity"
        )

        assert entity._sid == 12345
        assert entity._station_uuid == "station-uuid"
        assert entity._attr_unique_id == "unique-id"
        assert entity._attr_name == "Test Entity"

        # Verify make_unique_id called with correct parameters
        mock_make_unique_id.assert_called_once_with(
            12345, entity._serial, "station-uuid", None, "test-suffix"
        )

    def test_available_ignores_station_api_available(self, mock_coordinator):
        """Test MQTT-backed station entities do not depend on station REST state."""
        mock_coordinator.data.station = StationState(
            led_brightness=50,
            api_available=False,
        )
        entity = SmappeeStationEntity(
            mock_coordinator, 12345, "station-uuid", "test-suffix", "Test Entity"
        )

        assert entity.available is True


class TestSmappeeStationRestEntity:
    """Test the SmappeeStationRestEntity class."""

    def test_available_uses_station_api_available(self, mock_coordinator):
        """Test REST-backed station entities combine coordinator and station API state."""
        mock_coordinator.data.station = StationState(
            led_brightness=50,
            api_available=False,
        )
        entity = SmappeeStationRestEntity(
            mock_coordinator, 12345, "station-uuid", "test-suffix", "Test Entity"
        )

        assert entity.available is False


class TestSmappeeConnectorEntity:
    """Test the SmappeeConnectorEntity class."""

    @patch(
        "custom_components.smappee_ev.base_entities.make_unique_id",
        return_value="connector-unique-id",
    )
    def test_init(self, mock_make_unique_id, mock_coordinator):
        """Test entity initialization."""
        entity = SmappeeConnectorEntity(
            mock_coordinator,
            12345,
            "station-uuid",
            "connector-uuid",
            "test-suffix",
            "Test Connector",
        )

        assert entity._sid == 12345
        assert entity._station_uuid == "station-uuid"
        assert entity._connector_uuid == "connector-uuid"
        assert entity.connector_uuid == "connector-uuid"
        assert entity._attr_unique_id == "connector-unique-id"
        assert entity._attr_name == "Test Connector"

        # Verify make_unique_id called with correct parameters
        mock_make_unique_id.assert_called_once_with(
            12345, entity._serial, "station-uuid", "connector-uuid", "test-suffix"
        )

    def test_connector_uuid_property(self, mock_coordinator):
        """Test connector_uuid property."""
        entity = SmappeeConnectorEntity(
            mock_coordinator,
            12345,
            "station-uuid",
            "connector-uuid",
            "test-suffix",
            "Test Connector",
        )

        assert entity.connector_uuid == "connector-uuid"

    def test_conn_state_property(self, mock_coordinator):
        """Test _conn_state property."""
        entity = SmappeeConnectorEntity(
            mock_coordinator,
            12345,
            "station-uuid",
            "connector-uuid",
            "test-suffix",
            "Test Connector",
        )

        conn_state = entity._conn_state
        assert conn_state is not None
        assert conn_state.status == "AVAILABLE"
        assert conn_state.power == 0

    def test_conn_state_property_no_data(self, mock_coordinator):
        """Test _conn_state property with no data."""
        # Remove coordinator data
        mock_coordinator.data = None

        entity = SmappeeConnectorEntity(
            mock_coordinator,
            12345,
            "station-uuid",
            "connector-uuid",
            "test-suffix",
            "Test Connector",
        )

        conn_state = entity._conn_state
        assert conn_state is None

    def test_conn_state_property_missing_connector(self, mock_coordinator):
        """Test _conn_state property with missing connector."""
        # Use a different connector UUID than what's in the data
        entity = SmappeeConnectorEntity(
            mock_coordinator,
            12345,
            "station-uuid",
            "different-uuid",
            "test-suffix",
            "Test Connector",
        )

        conn_state = entity._conn_state
        assert conn_state is None

    def test_available_uses_connector_api_available(self, mock_coordinator):
        """Test connector availability combines coordinator and connector API state."""
        mock_coordinator.data.connectors["connector-uuid"] = ConnectorState(
            connector_number=1,
            api_available=False,
        )
        entity = SmappeeConnectorEntity(
            mock_coordinator,
            12345,
            "station-uuid",
            "connector-uuid",
            "test-suffix",
            "Test Connector",
        )

        assert entity.available is False
