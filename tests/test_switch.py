"""Tests for the switch platform."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.smappee_ev import switch
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.data import (
    ConnectorState,
    IntegrationData,
    RuntimeData,
    StationState,
)
from tests.factories import make_connector_runtime, make_site_runtime, make_station_runtime


@pytest.fixture
def mock_runtime_data():
    """Create mock runtime data."""
    runtime = MagicMock(spec=RuntimeData)
    connector_1 = MagicMock()
    connector_2 = MagicMock()
    runtime.sites = {
        12345: make_site_runtime(
            site_location_id=12345,
            stations={
                "station_uuid": make_station_runtime(
                    site_location_id=12345,
                    control_location_id=12345,
                    station_uuid="station_uuid",
                    coordinator=MagicMock(spec=SmappeeCoordinator),
                    station_client=MagicMock(),
                    connectors={
                        "connector_uuid1": make_connector_runtime(
                            connector_key="connector_uuid1",
                            connector_uuid="connector_uuid1",
                            connector_client=connector_1,
                        ),
                        "connector_uuid2": make_connector_runtime(
                            connector_key="connector_uuid2",
                            connector_uuid="connector_uuid2",
                            connector_client=connector_2,
                        ),
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
    return entry


@pytest.fixture
def mock_integration_data():
    """Create mock integration data with station and connector states."""
    station = StationState(
        led_brightness=50,
        available=True,
        mqtt_connected=True,
    )

    connector1 = ConnectorState(
        connector_number=1,
        session_state="AVAILABLE",
        selected_current_limit=16,
        selected_percentage_limit=80,
        selected_mode="NORMAL",
        min_current=6,
        max_current=32,
        connection_status="DISCONNECTED",
        available=True,
        ui_mode_base="NORMAL",
    )

    connector2 = ConnectorState(
        connector_number=2,
        session_state="CHARGING",
        selected_current_limit=20,
        selected_percentage_limit=100,
        selected_mode="NORMAL",
        min_current=6,
        max_current=32,
        connection_status="CONNECTED",
        available=True,
        ui_mode_base="SMART",
    )

    return IntegrationData(
        station=station,
        connectors={"connector_uuid1": connector1, "connector_uuid2": connector2},
    )


class TestSwitchPlatform:
    """Test cases for switch platform."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self, hass: HomeAssistant, mock_config_entry):
        """Test switch platform setup."""
        async_add_entities = MagicMock()

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            side_effect=lambda client, uuid: f"Connector {1 if uuid == 'connector_uuid1' else 2}",
        ):
            await switch.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Verify entities were added
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 4  # 2 station switches + 2 connector switches
        assert all(isinstance(entity, SwitchEntity) for entity in entities)

        # Check we have the right types of switches
        assert sum(1 for e in entities if isinstance(e, switch.SmappeeAvailabilitySwitch)) == 1
        assert sum(1 for e in entities if isinstance(e, switch.SmappeeOfflineChargingSwitch)) == 1
        assert sum(1 for e in entities if isinstance(e, switch.SmappeeChargingSwitch)) == 2

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant):
        """Test switch platform setup with no sites."""
        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = {}

        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime

        async_add_entities = MagicMock()

        await switch.async_setup_entry(hass, entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_sites(self, hass: HomeAssistant):
        """Test switch platform setup with empty sites."""
        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = {}

        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime

        async_add_entities = MagicMock()

        await switch.async_setup_entry(hass, entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)


class TestSmappeeChargingSwitch:
    """Test the SmappeeChargingSwitch class."""

    def test_initialization(self):
        """Test switch initialization."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        api_client = MagicMock()

        # Mock the connector number since it's used in the name
        api_client.connector_number = 1

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        assert charging_switch.connector_uuid == "connector_uuid1"
        assert charging_switch._sid == 12345
        assert charging_switch._station_uuid == "station_uuid"
        assert charging_switch.api_client == api_client
        assert charging_switch._is_on is False
        assert charging_switch.translation_key == "evcc_charging"

    def test_is_on_property(self):
        """Test is_on property."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        api_client = MagicMock()

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        # Default state
        assert charging_switch.is_on is False

        # Set state to on
        charging_switch._is_on = True
        assert charging_switch.is_on is True

    @pytest.mark.asyncio
    async def test_async_added_to_hass(self):
        """Test async_added_to_hass method."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        api_client = MagicMock()

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        # Mock last state - ON
        last_state = MagicMock()
        last_state.state = "on"
        charging_switch.async_get_last_state = AsyncMock(return_value=last_state)

        await charging_switch.async_added_to_hass()
        assert charging_switch._is_on is True

        # Mock last state - OFF
        last_state.state = "off"
        charging_switch.async_get_last_state = AsyncMock(return_value=last_state)
        charging_switch._is_on = False

        await charging_switch.async_added_to_hass()
        assert charging_switch._is_on is False

        # Mock no last state
        charging_switch.async_get_last_state = AsyncMock(return_value=None)
        charging_switch._is_on = False

        await charging_switch.async_added_to_hass()
        assert charging_switch._is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on(self, mock_integration_data):
        """Test async_turn_on method."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()
        api_client.set_charging_mode = AsyncMock()

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        charging_switch.async_write_ha_state = MagicMock()

        await charging_switch.async_turn_on()

        api_client.set_charging_mode.assert_awaited_once_with("STANDARD")
        assert charging_switch._is_on is True
        charging_switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_with_mode_change(self, mock_integration_data):
        """Test async_turn_on always sets STANDARD regardless of current selected mode."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        mock_integration_data.connectors["connector_uuid1"].selected_mode = "SMART"
        coordinator.data = mock_integration_data

        api_client = MagicMock()
        api_client.set_charging_mode = AsyncMock()

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        charging_switch.async_write_ha_state = MagicMock()

        await charging_switch.async_turn_on()

        api_client.set_charging_mode.assert_awaited_once_with("STANDARD")
        assert charging_switch._is_on is True
        charging_switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_error(self, mock_integration_data):
        """Test async_turn_on method with error."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data

        api_client = MagicMock()
        api_client.set_charging_mode = AsyncMock(side_effect=ClientError("Connection error"))

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        charging_switch.async_write_ha_state = MagicMock()

        # Test turn on with error
        with pytest.raises(ClientError):
            await charging_switch.async_turn_on()

        # State should remain off, but write_ha_state should be called
        assert charging_switch._is_on is False
        charging_switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_cancelled(self, mock_integration_data):
        """Test async_turn_on propagates cancellation without handling it as an error."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data

        api_client = MagicMock()
        api_client.set_charging_mode = AsyncMock(side_effect=asyncio.CancelledError())

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        charging_switch.async_write_ha_state = MagicMock()

        with pytest.raises(asyncio.CancelledError):
            await charging_switch.async_turn_on()

        charging_switch.async_write_ha_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off(self):
        """Test async_turn_off method."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        api_client = MagicMock()
        api_client.pause_charging = AsyncMock()

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        charging_switch._is_on = True
        charging_switch.async_write_ha_state = MagicMock()

        # Test turn off
        await charging_switch.async_turn_off()

        api_client.pause_charging.assert_awaited_once()
        assert charging_switch._is_on is False
        charging_switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off_error(self):
        """Test async_turn_off method with error."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        api_client = MagicMock()
        api_client.pause_charging = AsyncMock(side_effect=UpdateFailed("Failed to pause"))

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        charging_switch._is_on = True
        charging_switch.async_write_ha_state = MagicMock()

        # Test turn off with error
        with pytest.raises(UpdateFailed):
            await charging_switch.async_turn_off()

        # State should remain on, but write_ha_state should be called
        assert charging_switch._is_on is True
        charging_switch.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off_cancelled(self):
        """Test async_turn_off propagates cancellation without handling it as an error."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        api_client = MagicMock()
        api_client.pause_charging = AsyncMock(side_effect=asyncio.CancelledError())

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            charging_switch = switch.SmappeeChargingSwitch(
                coordinator=coordinator,
                api_client=api_client,
                sid=12345,
                station_uuid="station_uuid",
                connector_uuid="connector_uuid1",
            )

        charging_switch._is_on = True
        charging_switch.async_write_ha_state = MagicMock()

        with pytest.raises(asyncio.CancelledError):
            await charging_switch.async_turn_off()

        charging_switch.async_write_ha_state.assert_not_called()


class TestSmappeeAvailabilitySwitch:
    """Test the SmappeeAvailabilitySwitch class."""

    def test_initialization(self):
        """Test switch initialization."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        api_client = MagicMock()

        availability_switch = switch.SmappeeAvailabilitySwitch(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        assert availability_switch._station_uuid == "station_uuid"
        assert availability_switch._sid == 12345
        assert availability_switch.api_client == api_client
        assert availability_switch.translation_key == "station_available"

    def test_is_on_property(self, mock_integration_data):
        """Test is_on property."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()

        availability_switch = switch.SmappeeAvailabilitySwitch(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        # Test available station
        assert availability_switch.is_on is True

        # Test unavailable station
        mock_integration_data.station.available = False
        assert availability_switch.is_on is False

        # Test with None data
        coordinator.data = None
        assert availability_switch.is_on is True  # Default to True when no data

    @pytest.mark.asyncio
    async def test_async_turn_on(self, mock_integration_data):
        """Test async_turn_on method."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()
        api_client.set_available = AsyncMock()

        availability_switch = switch.SmappeeAvailabilitySwitch(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        # Test turn on
        mock_integration_data.station.available = False
        await availability_switch.async_turn_on()

        api_client.set_available.assert_called_once()
        assert mock_integration_data.station.available is True

        # Test optimistic update
        coordinator.async_set_updated_data.assert_called_once_with(mock_integration_data)

    @pytest.mark.asyncio
    async def test_async_turn_off(self, mock_integration_data):
        """Test async_turn_off method."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()
        api_client.set_unavailable = AsyncMock()

        availability_switch = switch.SmappeeAvailabilitySwitch(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        # Test turn off
        mock_integration_data.station.available = True
        await availability_switch.async_turn_off()

        api_client.set_unavailable.assert_called_once()
        assert mock_integration_data.station.available is False

        # Test optimistic update
        coordinator.async_set_updated_data.assert_called_once_with(mock_integration_data)

    @pytest.mark.asyncio
    async def test_set_available_error(self, mock_integration_data):
        """Test _set_available method with error."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()
        api_client.set_available = AsyncMock(side_effect=RuntimeError("Connection error"))

        availability_switch = switch.SmappeeAvailabilitySwitch(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        # Set initial state
        mock_integration_data.station.available = False

        # Test set_available with error
        with pytest.raises(HomeAssistantError):
            await availability_switch._set_available(True)

        # State should be reverted to original
        assert mock_integration_data.station.available is False

        # First update changes optimistically, second reverts on error
        assert coordinator.async_set_updated_data.call_count == 2


class TestSmappeeOfflineChargingSwitch:
    """Test station offline charging switch behavior."""

    def _make_switch(self, data):
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = data
        api_client = MagicMock()
        api_client.set_offline_charging_config = AsyncMock()
        offline_switch = switch.SmappeeOfflineChargingSwitch(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )
        return offline_switch, coordinator, api_client

    def test_is_on(self, mock_integration_data):
        offline_switch, _, _ = self._make_switch(mock_integration_data)

        assert offline_switch.is_on is False

        mock_integration_data.station.offline_charging_enabled = True
        assert offline_switch.is_on is True

        offline_switch.coordinator.data = None
        assert offline_switch.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_uses_existing_failsafe_and_schedules_refresh(
        self, mock_integration_data
    ):
        mock_integration_data.station.offline_charging_enabled = False
        mock_integration_data.station.offline_failsafe_current_a = 9
        offline_switch, coordinator, api_client = self._make_switch(mock_integration_data)

        await offline_switch.async_turn_on()

        assert mock_integration_data.station.offline_charging_enabled is True
        api_client.set_offline_charging_config.assert_awaited_once_with(True, 9)
        coordinator.async_set_updated_data.assert_called_with(mock_integration_data)
        coordinator.async_schedule_dashboard_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_off_defaults_missing_failsafe(self, mock_integration_data):
        mock_integration_data.station.offline_charging_enabled = True
        mock_integration_data.station.offline_failsafe_current_a = None
        offline_switch, _, api_client = self._make_switch(mock_integration_data)

        await offline_switch.async_turn_off()

        assert mock_integration_data.station.offline_charging_enabled is False
        api_client.set_offline_charging_config.assert_awaited_once_with(False, 3)

    @pytest.mark.asyncio
    async def test_turn_on_reverts_optimistic_state_on_error(self, mock_integration_data):
        mock_integration_data.station.offline_charging_enabled = False
        offline_switch, coordinator, api_client = self._make_switch(mock_integration_data)
        api_client.set_offline_charging_config.side_effect = RuntimeError("api down")

        with pytest.raises(HomeAssistantError) as err:
            await offline_switch.async_turn_on()

        assert err.value.translation_key == "station_service_failed"
        assert mock_integration_data.station.offline_charging_enabled is False
        assert coordinator.async_set_updated_data.call_count == 2

    @pytest.mark.asyncio
    async def test_turn_on_raises_when_station_state_missing(self):
        offline_switch, _, _ = self._make_switch(None)

        with pytest.raises(HomeAssistantError) as err:
            await offline_switch.async_turn_on()

        assert err.value.translation_key == "station_unavailable"

    @pytest.mark.asyncio
    async def test_turn_off_propagates_home_assistant_error(self, mock_integration_data):
        offline_switch, _, api_client = self._make_switch(mock_integration_data)
        api_client.set_offline_charging_config.side_effect = HomeAssistantError("custom error")

        with pytest.raises(HomeAssistantError, match="custom error"):
            await offline_switch.async_turn_off()
