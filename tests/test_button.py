"""Tests for the button component of the Smappee EV integration."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.smappee_ev.button import (
    SmappeeActionButton,
    SmappeeStationActionButton,
    async_setup_entry,
)
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.runtime_data import RuntimeData
from custom_components.smappee_ev.state import ConnectorState
from tests.factories import make_connector_runtime, make_site_runtime, make_station_runtime


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id"
    entry.domain = DOMAIN

    # Create runtime data with sites and stations
    runtime = MagicMock(spec=RuntimeData)
    client = MagicMock()
    client.get_connector_details.return_value = {"name": "Connector 1"}
    runtime.sites = {
        1: make_site_runtime(
            site_location_id=1,
            stations={
                "station1": make_station_runtime(
                    site_location_id=1,
                    control_location_id=1,
                    station_uuid="station1",
                    coordinator=MagicMock(spec=SmappeeCoordinator),
                    station_client=MagicMock(),
                    connectors={
                        "conn1": make_connector_runtime(
                            connector_key="conn1",
                            connector_uuid="conn1",
                            connector_client=client,
                        )
                    },
                )
            },
        )
    }

    entry.runtime_data = runtime
    return entry


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = MagicMock(spec=SmappeeCoordinator)
    connector = ConnectorState(connector_number=1)
    connector.selected_current_limit = 10
    connector.min_current = 6
    connector.max_current = 32
    connector.selected_mode = "NORMAL"
    connector.ui_mode_base = "NORMAL"

    coordinator.data = MagicMock()
    coordinator.data.connectors = {"conn1": connector}

    return coordinator


async def test_async_setup_entry(hass, mock_config_entry):
    """Test setting up the button platform."""
    # Use a regular MagicMock instead of AsyncMock to avoid the coroutine warning
    mock_add_entities = MagicMock()

    await async_setup_entry(hass, mock_config_entry, mock_add_entities)

    # Should add 1 station button plus 4 buttons per connector (start, pause, stop, resume)
    assert mock_add_entities.call_count == 1

    # Get the entities that were added
    entities = mock_add_entities.call_args[0][0]
    assert len(entities) == 5
    assert entities[0]._action == "restart_charging_station"
    assert entities[0].translation_key == "restart_charging_station"

    # Check entity names and actions
    actions = ["start_charging", "pause_charging", "stop_charging", "resume_charging"]
    for i, action in enumerate(actions):
        entity = entities[i + 1]
        assert entity._action == action
        assert entity.translation_key == action


async def test_button_attributes(mock_coordinator):
    """Test the button entity attributes."""
    api_client = MagicMock()

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Test Button",
        action="start_charging",
    )

    assert button.name == "Test Button"
    assert button.unique_id.endswith("button:start_charging")
    assert button.api_client == api_client
    assert button._action == "start_charging"


async def test_station_button_press_restart_charging_station(mock_coordinator):
    """Test pressing the station restart button."""
    api_client = MagicMock()
    api_client.restart_charging_station = AsyncMock()

    button = SmappeeStationActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        name="Restart charging station",
        action="restart_charging_station",
    )

    await button.async_press()

    api_client.restart_charging_station.assert_awaited_once()
    mock_coordinator.async_schedule_dashboard_refresh.assert_called_once()


async def test_station_button_restart_raises_translated_error(mock_coordinator):
    """Test station restart errors are surfaced through HA translation metadata."""
    api_client = MagicMock()
    api_client.restart_charging_station = AsyncMock(side_effect=RuntimeError("offline"))

    button = SmappeeStationActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        action="restart_charging_station",
    )

    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()

    assert err.value.translation_key == "station_service_failed"
    assert err.value.translation_placeholders == {
        "method_name": "restart_charging_station",
        "error": "offline",
    }
    mock_coordinator.async_schedule_dashboard_refresh.assert_not_called()


async def test_station_button_unknown_action_logs_debug(mock_coordinator):
    """Test unknown station actions do not call the API."""
    api_client = MagicMock()
    api_client.restart_charging_station = AsyncMock()

    button = SmappeeStationActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        action="unknown_station_action",
    )

    with patch("custom_components.smappee_ev.button._LOGGER.debug") as debug:
        await button.async_press()

    debug.assert_called_once_with("Unknown station action for button: %s", "unknown_station_action")
    api_client.restart_charging_station.assert_not_awaited()


async def test_button_press_start_charging(mock_coordinator):
    """Test pressing the start charging button."""
    api_client = MagicMock()
    api_client.start_charging = AsyncMock()
    connector = mock_coordinator.data.connectors["conn1"]
    connector.selected_current_limit = 10
    connector.selected_percentage_limit = 50

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Start Charging",
        action="start_charging",
    )

    await button.async_press()

    api_client.start_charging.assert_awaited_once_with()
    assert connector.selected_current_limit == 10
    assert connector.selected_percentage_limit == 50
    mock_coordinator.async_set_updated_data.assert_not_called()
    mock_coordinator.async_schedule_dashboard_refresh.assert_called_once()


async def test_button_press_start_charging_ignores_restored_slider_limit(mock_coordinator):
    """Test pressing the start charging button ignores restored slider current."""
    api_client = MagicMock()
    api_client.start_charging = AsyncMock()
    connector = mock_coordinator.data.connectors["conn1"]
    connector.selected_current_limit = 16.5

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Start Charging",
        action="start_charging",
    )

    await button.async_press()

    api_client.start_charging.assert_awaited_once_with()


async def test_button_press_start_charging_no_connector(mock_coordinator):
    """Test pressing the start charging button with a non-existent connector."""
    api_client = MagicMock()
    api_client.start_charging = AsyncMock()

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="non_existent",  # This connector doesn't exist in data
        name="Start Charging",
        action="start_charging",
    )

    await button.async_press()

    api_client.start_charging.assert_awaited_once_with()


async def test_button_press_start_charging_no_coordinator_data(mock_coordinator):
    """Test pressing the start charging button with no coordinator data."""
    api_client = MagicMock()
    api_client.start_charging = AsyncMock()

    # Set coordinator data to None
    mock_coordinator.data = None

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Start Charging",
        action="start_charging",
    )

    await button.async_press()

    api_client.start_charging.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("action", "method_name", "client_method"),
    [
        ("start_charging", "start_charging", "start_charging"),
        ("pause_charging", "pause_charging", "pause_charging"),
        ("stop_charging", "stop_charging", "stop_charging"),
        ("resume_charging", "set_charging_mode", "set_charging_mode"),
    ],
)
async def test_connector_button_api_errors_are_translated(
    mock_coordinator, action, method_name, client_method
):
    """Test connector button API errors include the failing Dashboard action."""
    api_client = MagicMock()
    setattr(api_client, client_method, AsyncMock(side_effect=RuntimeError("boom")))

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        action=action,
    )

    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()

    assert err.value.translation_key == "connector_service_failed"
    assert err.value.translation_placeholders == {
        "method_name": method_name,
        "error": "boom",
    }
    mock_coordinator.async_schedule_dashboard_refresh.assert_not_called()


async def test_button_press_pause_charging():
    """Test pressing the pause charging button."""
    api_client = MagicMock()
    api_client.pause_charging = AsyncMock()

    button = SmappeeActionButton(
        coordinator=MagicMock(),
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Pause Charging",
        action="pause_charging",
    )

    await button.async_press()

    # Verify the API client was called
    api_client.pause_charging.assert_called_once()


async def test_button_press_stop_charging():
    """Test pressing the stop charging button."""
    api_client = MagicMock()
    api_client.stop_charging = AsyncMock()

    button = SmappeeActionButton(
        coordinator=MagicMock(),
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Stop Charging",
        action="stop_charging",
    )

    await button.async_press()

    # Verify the API client was called
    api_client.stop_charging.assert_called_once()


async def test_button_press_resume_charging(mock_coordinator):
    """Test pressing the resume charging button."""
    api_client = MagicMock()
    api_client.set_charging_mode = AsyncMock()

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Resume Charging",
        action="resume_charging",
    )

    await button.async_press()

    # NORMAL is a legacy/restored label for Dashboard STANDARD.
    api_client.set_charging_mode.assert_called_once_with("STANDARD")


async def test_button_press_resume_charging_no_selected_mode(mock_coordinator):
    """Test resume charging when selected_mode is None."""
    api_client = MagicMock()
    api_client.set_charging_mode = AsyncMock()

    # Remove selected_mode
    connector = mock_coordinator.data.connectors["conn1"]
    connector.selected_mode = None

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Resume Charging",
        action="resume_charging",
    )

    await button.async_press()

    # Should fallback to ui_mode_base and normalize legacy NORMAL.
    api_client.set_charging_mode.assert_called_once_with("STANDARD")


async def test_button_press_resume_charging_uses_solar_mode(mock_coordinator):
    """Test resume charging preserves solar mode."""
    api_client = MagicMock()
    api_client.set_charging_mode = AsyncMock()
    connector = mock_coordinator.data.connectors["conn1"]
    connector.selected_mode = "solar"
    connector.ui_mode_base = "standard"

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Resume Charging",
        action="resume_charging",
    )

    await button.async_press()

    api_client.set_charging_mode.assert_called_once_with("SOLAR")


async def test_button_press_resume_charging_no_modes(mock_coordinator):
    """Test resume charging when both selected_mode and ui_mode_base are None."""
    api_client = MagicMock()
    api_client.set_charging_mode = AsyncMock()

    # Remove both modes
    connector = mock_coordinator.data.connectors["conn1"]
    connector.selected_mode = None
    connector.ui_mode_base = None

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Resume Charging",
        action="resume_charging",
    )

    await button.async_press()

    # Should use default "STANDARD"
    api_client.set_charging_mode.assert_called_once_with("STANDARD")


async def test_button_press_resume_charging_no_connector(mock_coordinator):
    """Test resume charging with a non-existent connector."""
    api_client = MagicMock()
    api_client.set_charging_mode = AsyncMock()

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="non_existent",  # This connector doesn't exist in data
        name="Resume Charging",
        action="resume_charging",
    )

    await button.async_press()

    # Should use default mode
    api_client.set_charging_mode.assert_called_once_with("STANDARD")


async def test_button_press_resume_charging_no_coordinator_data(mock_coordinator):
    """Test resume charging with no coordinator data."""
    api_client = MagicMock()
    api_client.set_charging_mode = AsyncMock()

    # Set coordinator data to None
    mock_coordinator.data = None

    button = SmappeeActionButton(
        coordinator=mock_coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Resume Charging",
        action="resume_charging",
    )

    await button.async_press()

    # Should use default mode
    api_client.set_charging_mode.assert_called_once_with("STANDARD")


async def test_button_press_unknown_action():
    """Test pressing a button with an unknown action."""
    api_client = MagicMock()

    button = SmappeeActionButton(
        coordinator=MagicMock(),
        api_client=api_client,
        sid=1,
        station_uuid="station1",
        connector_uuid="conn1",
        name="Unknown Action",
        action="unknown_action",
    )

    with patch("custom_components.smappee_ev.button._LOGGER.debug") as mock_logger:
        await button.async_press()

        # Should log a debug message
        mock_logger.assert_called_once_with("Unknown action for button: %s", "unknown_action")

        # No API client methods should be called
        assert not api_client.start_charging.called
        assert not api_client.pause_charging.called
        assert not api_client.stop_charging.called
        assert not api_client.set_charging_mode.called
