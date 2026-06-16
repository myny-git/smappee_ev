"""Test the Smappee EV restore entity functionality."""

from unittest.mock import MagicMock, patch

from homeassistant.components.number import RestoreNumber
from homeassistant.components.sensor import RestoreSensor
from homeassistant.helpers.restore_state import RestoreEntity
import pytest

from custom_components.smappee_ev.number import (
    SmappeeCombinedCurrentSlider,
    SmappeeMinSurplusPctNumber,
)
from custom_components.smappee_ev.sensor import SmappeeEVCCStateSensor, SmappeeEvseStatusSensor


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock()

    # Mock station data
    station = MagicMock()
    station.led_brightness = None

    # Mock connector data
    connector = MagicMock()
    connector.min_current = 6
    connector.max_current = 32
    connector.selected_current_limit = None
    connector.selected_percentage_limit = None
    connector.min_surpluspct = None
    connector.evcc_state = None
    connector.status_current = None

    # Setup coordinator data
    data = MagicMock()
    data.station = station
    data.connectors = {"connector-uuid": connector}
    coordinator.data = data

    return coordinator


@pytest.fixture
def mock_api_client():
    """Create a mock API client."""
    api_client = MagicMock()
    api_client.set_brightness = MagicMock()
    api_client.start_charging = MagicMock(return_value=(16, 50))
    api_client.set_percentage_limit = MagicMock(return_value=(16, 50))
    api_client.set_min_surpluspct = MagicMock()

    return api_client


class TestRestoreEntityFunctionality:
    """Test restore entity functionality for Smappee EV entities."""

    @pytest.mark.asyncio
    async def test_current_slider_restore(self, hass, mock_coordinator, mock_api_client):
        """Test current slider restore functionality."""
        entity = SmappeeCombinedCurrentSlider(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            connector_uuid="connector-uuid",
        )

        # Set the hass attribute and entity_id
        entity.hass = hass
        entity.entity_id = "number.test_current_slider"

        # Mock last number data
        last_data = MagicMock()
        last_data.native_value = 16

        with patch.object(RestoreNumber, "async_get_last_number_data", return_value=last_data):
            await entity.async_added_to_hass()

        # Verify state was restored
        connector = mock_coordinator.data.connectors["connector-uuid"]
        assert connector.selected_current_limit == 16

        # Verify percentage was calculated correctly
        assert connector.selected_percentage_limit == 38  # (16-6)/(32-6)*100 = 38.46%
        assert mock_coordinator.async_set_updated_data.called

    @pytest.mark.asyncio
    async def test_min_surplus_pct_restore(self, hass, mock_coordinator, mock_api_client):
        """Test min surplus percentage restore functionality."""
        entity = SmappeeMinSurplusPctNumber(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            connector_uuid="connector-uuid",
        )

        # Set the hass attribute and entity_id
        entity.hass = hass
        entity.entity_id = "number.test_min_surplus_pct"

        # Mock last number data
        last_data = MagicMock()
        last_data.native_value = 25

        with patch.object(RestoreNumber, "async_get_last_number_data", return_value=last_data):
            await entity.async_added_to_hass()

        # Verify state was restored
        connector = mock_coordinator.data.connectors["connector-uuid"]
        assert connector.min_surpluspct == 25
        assert mock_coordinator.async_set_updated_data.called

    @pytest.mark.asyncio
    async def test_current_slider_restore_no_last_state(
        self, hass, mock_coordinator, mock_api_client
    ):
        """Test current slider restore with no last state."""
        entity = SmappeeCombinedCurrentSlider(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            connector_uuid="connector-uuid",
        )

        # Set the hass attribute and entity_id
        entity.hass = hass
        entity.entity_id = "number.test_current_slider"

        with patch.object(RestoreNumber, "async_get_last_number_data", return_value=None):
            await entity.async_added_to_hass()

        # Verify state was not restored
        connector = mock_coordinator.data.connectors["connector-uuid"]
        assert connector.selected_current_limit is None
        assert connector.selected_percentage_limit is None
        assert not mock_coordinator.async_set_updated_data.called

    @pytest.mark.asyncio
    async def test_current_slider_restore_with_existing_value(
        self, hass, mock_coordinator, mock_api_client
    ):
        """Test current slider restore doesn't overwrite existing value."""
        # Set an existing value
        connector = mock_coordinator.data.connectors["connector-uuid"]
        connector.selected_current_limit = 20
        connector.selected_percentage_limit = 60

        entity = SmappeeCombinedCurrentSlider(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            connector_uuid="connector-uuid",
        )

        # Set the hass attribute and entity_id
        entity.hass = hass
        entity.entity_id = "number.test_current_slider"

        # Mock last number data
        last_data = MagicMock()
        last_data.native_value = 16

        with patch.object(RestoreNumber, "async_get_last_number_data", return_value=last_data):
            await entity.async_added_to_hass()

        # Verify existing value was not overwritten
        assert connector.selected_current_limit == 20
        assert connector.selected_percentage_limit == 60
        assert not mock_coordinator.async_set_updated_data.called

    @pytest.mark.asyncio
    async def test_current_slider_restore_does_not_overwrite_api_percentage(
        self, hass, mock_coordinator, mock_api_client
    ):
        """Test current slider restore doesn't overwrite API percentage state."""
        connector = mock_coordinator.data.connectors["connector-uuid"]
        connector.selected_current_limit = None
        connector.selected_percentage_limit = 60

        entity = SmappeeCombinedCurrentSlider(
            coordinator=mock_coordinator,
            api_client=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            connector_uuid="connector-uuid",
        )

        entity.hass = hass
        entity.entity_id = "number.test_current_slider"

        last_data = MagicMock()
        last_data.native_value = 16

        with patch.object(RestoreNumber, "async_get_last_number_data", return_value=last_data):
            await entity.async_added_to_hass()

        assert connector.selected_current_limit is None
        assert connector.selected_percentage_limit == 60
        assert not mock_coordinator.async_set_updated_data.called

    @pytest.mark.asyncio
    async def test_evcc_state_sensor_restore(self, hass, mock_coordinator, mock_api_client):
        """Test EVCC state sensor restore functionality."""
        entity = SmappeeEVCCStateSensor(
            c=mock_coordinator,
            api=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            uuid="connector-uuid",
        )

        # Set the hass attribute and entity_id
        entity.hass = hass
        entity.entity_id = "sensor.test_evcc_state"

        # Mock last sensor data
        last_data = MagicMock()
        last_data.native_value = "CONNECTED"

        # Mock last state with attributes
        last_state = MagicMock()
        last_state.state = "CONNECTED"
        last_state.attributes = {
            "iec_status": "A",
            "session_state": "IDLE",
            "charging_mode": "SCHEDULE",
            "optimization_strategy": "OPTIMIZE_GREEN",
            "paused": False,
            "status_current": "AVAILABLE",
        }

        with (
            patch.object(RestoreSensor, "async_get_last_sensor_data", return_value=last_data),
            patch.object(RestoreEntity, "async_get_last_state", return_value=last_state),
        ):
            await entity.async_added_to_hass()

        # Verify state was restored
        assert entity._restored_value == "CONNECTED"

        # Test that native_value returns the restored value
        assert entity.native_value == "CONNECTED"

        # Verify restored attributes
        assert entity._restored_attributes is not None
        assert entity._restored_attributes.get("iec_status") == "A"
        assert entity._restored_attributes.get("session_state") == "IDLE"
        assert entity._restored_attributes.get("charging_mode") == "SCHEDULE"
        assert entity._restored_attributes.get("optimization_strategy") == "OPTIMIZE_GREEN"
        assert entity._restored_attributes.get("paused") is False
        assert entity._restored_attributes.get("status_current") == "AVAILABLE"

    @pytest.mark.asyncio
    async def test_evcc_state_sensor_no_restore_when_real_data(
        self, hass, mock_coordinator, mock_api_client
    ):
        """Test EVCC state sensor uses real data when available."""
        # Set up real data
        connector = mock_coordinator.data.connectors["connector-uuid"]
        connector.evcc_state = "CHARGING"

        entity = SmappeeEVCCStateSensor(
            c=mock_coordinator,
            api=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            uuid="connector-uuid",
        )

        # Set the hass attribute and entity_id
        entity.hass = hass
        entity.entity_id = "sensor.test_evcc_state"

        # Mock last sensor data
        last_data = MagicMock()
        last_data.native_value = "CONNECTED"

        # Mock last state
        last_state = MagicMock()
        last_state.state = "CONNECTED"

        with (
            patch.object(RestoreSensor, "async_get_last_sensor_data", return_value=last_data),
            patch.object(RestoreEntity, "async_get_last_state", return_value=last_state),
        ):
            await entity.async_added_to_hass()

        # Verify real data is used
        assert entity.native_value == "CHARGING"

    @pytest.mark.asyncio
    async def test_evse_status_sensor_restore(self, hass, mock_coordinator, mock_api_client):
        """Test EVSE status sensor restore functionality."""
        entity = SmappeeEvseStatusSensor(
            c=mock_coordinator,
            api=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            uuid="connector-uuid",
        )

        # Set the hass attribute and entity_id
        entity.hass = hass
        entity.entity_id = "sensor.test_evse_status"

        # Mock last sensor data
        last_data = MagicMock()
        last_data.native_value = "AVAILABLE"

        # Mock last state with attributes
        last_state = MagicMock()
        last_state.state = "AVAILABLE"
        last_state.attributes = {}

        with (
            patch.object(RestoreSensor, "async_get_last_sensor_data", return_value=last_data),
            patch.object(RestoreEntity, "async_get_last_state", return_value=last_state),
        ):
            await entity.async_added_to_hass()

        # Verify state was restored
        assert entity._restored_value == "AVAILABLE"

        # Test that native_value returns the restored value
        assert entity.native_value == "AVAILABLE"

    @pytest.mark.asyncio
    async def test_evse_status_sensor_no_restore_when_real_data(
        self, hass, mock_coordinator, mock_api_client
    ):
        """Test EVSE status sensor uses real data when available."""
        # Set up real data
        connector = mock_coordinator.data.connectors["connector-uuid"]
        connector.status_current = "CHARGING"

        entity = SmappeeEvseStatusSensor(
            c=mock_coordinator,
            api=mock_api_client,
            sid=12345,
            station_uuid="station-uuid",
            uuid="connector-uuid",
        )

        # Set the hass attribute and entity_id
        entity.hass = hass
        entity.entity_id = "sensor.test_evse_status"

        # Mock last sensor data
        last_data = MagicMock()
        last_data.native_value = "AVAILABLE"

        # Mock last state
        last_state = MagicMock()
        last_state.state = "AVAILABLE"

        with (
            patch.object(RestoreSensor, "async_get_last_sensor_data", return_value=last_data),
            patch.object(RestoreEntity, "async_get_last_state", return_value=last_state),
        ):
            await entity.async_added_to_hass()

        # Verify real data is used
        assert entity.native_value == "charging"
