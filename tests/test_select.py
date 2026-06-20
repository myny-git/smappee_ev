# tests/test_select.py
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import State
import pytest

from custom_components.smappee_ev import select
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.data import (
    ConnectorState,
    IntegrationData,
    RuntimeData,
    StationState,
)
from custom_components.smappee_ev.device_handle import SmappeeDeviceHandle
from tests.factories import make_connector_runtime, make_site_runtime, make_station_runtime


@pytest.fixture
def mock_connector_state():
    """Create a mock connector state."""
    return ConnectorState(
        connector_number=1,
        session_state="Connected",
        selected_current_limit=16,
        selected_percentage_limit=80,
        selected_mode="NORMAL",
        ui_mode_base="NORMAL",
    )


@pytest.fixture
def mock_integration_data(mock_connector_state):
    """Create mock integration data."""
    return IntegrationData(
        station=StationState(),
        connectors={"connector_uuid_123": mock_connector_state},
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
    api_client.connector_number = 1
    api_client.station_mode = False
    api_client.set_charging_mode = AsyncMock()
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
                    station_client=MagicMock(),
                    connectors={
                        "connector_uuid_123": make_connector_runtime(
                            connector_key="connector_uuid_123",
                            connector_uuid="connector_uuid_123",
                            connector_client=mock_api_client,
                        )
                    },
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


class TestSelectPlatform:
    """Test cases for select platform."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self, hass: HomeAssistant, mock_config_entry):
        """Test select platform setup."""
        async_add_entities = MagicMock()

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            await select.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Verify entities were added
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert all(isinstance(entity, SelectEntity) for entity in entities)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant, mock_config_entry):
        """Test select platform setup with no sites."""
        mock_config_entry.runtime_data.sites = {}
        async_add_entities = MagicMock()

        await select.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_sites(self, hass: HomeAssistant, mock_config_entry):
        """Test select platform setup with empty sites."""
        mock_config_entry.runtime_data.sites = {}
        async_add_entities = MagicMock()

        await select.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_connector_clients(
        self, hass: HomeAssistant, mock_config_entry
    ):
        """Test select platform setup with no connector clients."""
        mock_config_entry.runtime_data.sites[12345].stations["station_uuid_123"].connectors.clear()
        async_add_entities = MagicMock()

        await select.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)


class TestSmappeeModeSelect:
    """Test cases for the Smappee mode select entity."""

    def test_init(self, mock_coordinator, mock_api_client):
        """Test initialization of mode select entity."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        # Check basic properties
        assert entity._attr_has_entity_name is True
        assert entity._attr_options == ["standard", "smart", "solar"]
        assert entity.api_client == mock_api_client
        assert entity._sid == 12345
        assert entity._station_uuid == "station_uuid_123"
        assert entity._connector_uuid == "connector_uuid_123"
        assert entity.translation_key == "charging_mode"

    def test_device_info(self, mock_coordinator, mock_api_client):
        """Test device_info property."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        # We just verify it's accessible
        assert entity.device_info is not None

    def test_current_option(self, mock_coordinator, mock_api_client, mock_connector_state):
        """Test current_option property."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        # Test with selected_mode set
        mock_connector_state.selected_mode = "SMART"
        assert entity.current_option == "smart"

        # Test with only ui_mode_base set
        mock_connector_state.selected_mode = None
        mock_connector_state.ui_mode_base = "SOLAR"
        assert entity.current_option == "solar"

        # Test with neither set (falls back to STANDARD)
        mock_connector_state.selected_mode = None
        mock_connector_state.ui_mode_base = None
        assert entity.current_option == "standard"

    def test_current_option_no_data(self, mock_coordinator, mock_api_client):
        """Test current_option property with no data."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        # Test with no data
        mock_coordinator.data = None
        assert entity.current_option == "standard"

    @pytest.mark.asyncio
    async def test_async_select_option(
        self, mock_coordinator, mock_api_client, mock_connector_state
    ):
        """Test selecting an option."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        entity.async_write_ha_state = MagicMock()
        mock_coordinator.async_set_updated_data = MagicMock()

        # Select SMART mode
        await entity.async_select_option("smart")

        # Check that API call was made
        mock_api_client.set_charging_mode.assert_called_once_with("SMART")

        # Check that state was updated
        assert mock_connector_state.selected_mode == "smart"

        # Check that coordinator was updated
        mock_coordinator.async_set_updated_data.assert_called_once()

        # Check that entity state was written
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_select_option_no_data(self, mock_coordinator, mock_api_client):
        """Test selecting an option with no data."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        entity.async_write_ha_state = MagicMock()
        mock_coordinator.async_set_updated_data = MagicMock()

        # Set coordinator data to None
        mock_coordinator.data = None

        # Select SMART mode
        await entity.async_select_option("smart")

        # Check that API call was made
        mock_api_client.set_charging_mode.assert_called_once_with("SMART")

        # Coordinator update should not be called
        mock_coordinator.async_set_updated_data.assert_not_called()

        # Check that entity state was written
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_added_to_hass(
        self, hass, mock_coordinator, mock_api_client, mock_connector_state
    ):
        """Test entity added to hass with state restoration."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        # Mock RestoreEntity methods
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()

        # Set selected_mode to None to test restoration
        mock_connector_state.selected_mode = None

        # Create last state with SMART mode
        mock_last_state = State("select.charging_mode_1", "smart")

        with patch(
            "homeassistant.helpers.restore_state.RestoreEntity.async_get_last_state",
            return_value=mock_last_state,
        ):
            await entity.async_added_to_hass()

        # Check that state was restored
        assert mock_connector_state.selected_mode == "smart"

        # Coordinator update already writes entity state through listeners
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_keeps_live_mode_and_writes_state_when_platform_ready(
        self, hass, mock_coordinator, mock_api_client, mock_connector_state
    ):
        """Test restore does not overwrite a live mode already provided by the coordinator."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        entity.hass = hass
        entity.platform = object()
        entity.async_write_ha_state = MagicMock()
        mock_coordinator.async_set_updated_data = MagicMock()
        mock_connector_state.selected_mode = "standard"
        mock_last_state = State("select.charging_mode_1", "smart")

        with patch(
            "homeassistant.helpers.restore_state.RestoreEntity.async_get_last_state",
            return_value=mock_last_state,
        ):
            await entity.async_added_to_hass()

        assert mock_connector_state.selected_mode == "standard"
        mock_coordinator.async_set_updated_data.assert_not_called()
        entity.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_select_option_rolls_back_on_api_error(
        self, mock_coordinator, mock_api_client, mock_connector_state
    ):
        """Test optimistic mode update rolls back if the API call fails."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        entity.async_write_ha_state = MagicMock()
        mock_coordinator.async_set_updated_data = MagicMock()
        mock_connector_state.selected_mode = "standard"
        mock_api_client.set_charging_mode.side_effect = RuntimeError("boom")

        with pytest.raises(HomeAssistantError):
            await entity.async_select_option("smart")

        assert mock_connector_state.selected_mode == "standard"
        assert mock_coordinator.async_set_updated_data.call_count == 2
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_no_restore(self, hass, mock_coordinator, mock_api_client):
        """Test entity added to hass without state restoration."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        # Mock RestoreEntity methods
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()

        # Create last state with invalid mode
        mock_last_state = State("select.charging_mode_1", "INVALID")

        with patch(
            "homeassistant.helpers.restore_state.RestoreEntity.async_get_last_state",
            return_value=mock_last_state,
        ):
            await entity.async_added_to_hass()

        # Check that entity state was not written
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_unknown_state(self, hass, mock_coordinator, mock_api_client):
        """Test entity added to hass with unknown last state."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        # Mock RestoreEntity methods
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()

        # Create unknown last state
        mock_last_state = State("select.charging_mode_1", "unknown")

        with patch(
            "homeassistant.helpers.restore_state.RestoreEntity.async_get_last_state",
            return_value=mock_last_state,
        ):
            await entity.async_added_to_hass()

        # Check that entity state was not written
        entity.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_added_to_hass_no_last_state(self, hass, mock_coordinator, mock_api_client):
        """Test entity added to hass with no last state."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            entity = select.SmappeeModeSelect(
                coordinator=mock_coordinator,
                api_client=mock_api_client,
                sid=12345,
                station_uuid="station_uuid_123",
                connector_uuid="connector_uuid_123",
            )

        # Mock RestoreEntity methods
        entity.hass = hass
        entity.async_write_ha_state = MagicMock()

        with patch(
            "homeassistant.helpers.restore_state.RestoreEntity.async_get_last_state",
            return_value=None,
        ):
            await entity.async_added_to_hass()

        # Check that entity state was not written
        entity.async_write_ha_state.assert_not_called()
