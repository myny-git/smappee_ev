"""Test error handling cases for the Smappee EV integration initialization."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.smappee_ev import async_unload_entry
from custom_components.smappee_ev.api.mqtt_gateway import SmappeeMqtt
from custom_components.smappee_ev.const import UPDATE_INTERVAL_DEFAULT
from custom_components.smappee_ev.dashboard_discovery import _fetch_dashboard_connector_mapping
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.mqtt_setup import _setup_mqtt
from custom_components.smappee_ev.runtime_lifecycle import _async_shutdown_runtime_resources
from custom_components.smappee_ev.site_preparation import _prepare_site
from custom_components.smappee_ev.topology import _assign_connectors
from tests.factories import make_site_runtime, make_station_runtime


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


class TestErrorHandling:
    """Test error handling in __init__.py functions."""

    @pytest.mark.asyncio
    async def test_fetch_dashboard_connector_mapping_value_error(self):
        """Test _fetch_dashboard_connector_mapping with ValueError exception."""
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.async_get_charging_station_details = AsyncMock(
            side_effect=ValueError("Invalid data")
        )

        station_devs = [{"uuid": "station_uuid", "id": "station_id"}]
        result = await _fetch_dashboard_connector_mapping(dashboard_client, station_devs)

        assert result == {}

    @pytest.mark.asyncio
    async def test_fetch_dashboard_connector_mapping_runtime_error(self):
        """Test _fetch_dashboard_connector_mapping with RuntimeError exception."""
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.async_get_charging_station_details = AsyncMock(
            side_effect=RuntimeError("Missing key")
        )

        station_devs = [{"uuid": "station_uuid", "id": "station_id"}]
        result = await _fetch_dashboard_connector_mapping(dashboard_client, station_devs)

        assert result == {}

    @pytest.mark.asyncio
    async def test_fetch_dashboard_connector_mapping_timeout_error(self):
        """Test _fetch_dashboard_connector_mapping with TimeoutError exception."""
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.async_get_charging_station_details = AsyncMock(
            side_effect=TimeoutError("Timed out")
        )

        station_devs = [{"uuid": "station_uuid", "id": "station_id"}]
        result = await _fetch_dashboard_connector_mapping(dashboard_client, station_devs)

        assert result == {}

    @pytest.mark.asyncio
    async def test_assign_connectors_empty_cases(self):
        """Test _assign_connectors with empty inputs."""
        # Create empty stations and car_devs
        stations = {}
        car_devs = []
        mapping = {}

        # Should not raise any exceptions
        _assign_connectors(stations, car_devs, mapping, "SITE_SERIAL", 12345)

        # Create stations with no matching serial
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                serial="STATION1",
                connectors={},
            )
        }
        mapping = {
            "STATION2": {  # Different serial, no match
                "connectors": {"connector1_uuid": {"id": "conn1_id", "position": 1}}
            }
        }

        # Should not modify the stations dict
        _assign_connectors(stations, car_devs, mapping, "SITE_SERIAL", 12345)
        assert stations["station1_uuid"].connectors == {}

    @pytest.mark.asyncio
    async def test_assign_connectors_no_matching_car(self):
        """Test _assign_connectors with no matching car devices."""
        # Create stations with a serial that matches the mapping
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                serial="STATION1",
                connectors={},
            )
        }

        # Create car devices with non-matching UUID
        car_devs = [
            {
                "uuid": "connector2_uuid",  # Different UUID, no match
                "id": "connector2_id",
            }
        ]

        # Create mapping that expects connector1_uuid
        mapping = {
            "STATION1": {
                "connectors": {
                    "connector1_uuid": {  # No matching car device
                        "id": "conn1_id",
                        "position": 1,
                    }
                }
            }
        }

        # Should not modify the stations dict
        _assign_connectors(stations, car_devs, mapping, "SITE_SERIAL", 12345)
        assert stations["station1_uuid"].connectors == {}

    @pytest.mark.asyncio
    async def test_prepare_site_exception_handling(self, hass, mock_dashboard_handle, mock_session):
        """Test _prepare_site with exceptions during execution."""
        session, _ = mock_session

        # Create a service location with valid deviceSerialNumber
        service_location = {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "test-service-uuid",
            "deviceSerialNumber": "STATION-SERIAL",
            "name": "Test Location",
        }

        # Mock dashboard device discovery to succeed but _split_devices to raise an exception
        with (
            patch(
                "custom_components.smappee_ev.site_preparation._dashboard_fetch_devices",
                return_value=[{"type": "CHARGINGSTATION"}],
            ),
            patch(
                "custom_components.smappee_ev.site_preparation._split_devices",
                side_effect=Exception("Test exception"),
            ),
            patch(
                "custom_components.smappee_ev.site_preparation._LOGGER.exception"
            ) as mock_log_exception,
        ):
            # Call _prepare_site - should handle the exception
            result = await _prepare_site(
                hass,
                session,
                service_location,
                UPDATE_INTERVAL_DEFAULT,
                "client-prefix",
            )

            # Should return (None, None) on error
            assert result == (None, None)

            # Verify exception was logged
            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_mqtt_stop_exception_handling(self, hass):
        """Test async_unload_entry with exception in MQTT stop."""
        # Create a mock config entry
        entry = MagicMock()
        entry.entry_id = "test_entry_id"

        # Create a mock MQTT client that raises an exception on stop
        mqtt_client = MagicMock(spec=SmappeeMqtt)
        mqtt_client.stop = MagicMock(side_effect=RuntimeError("Failed to stop"))

        # Create runtime data with the problematic MQTT client
        runtime = RuntimeData(api=MagicMock(), sites={}, mqtt={12345: mqtt_client})

        # Attach runtime data to config entry
        entry.runtime_data = runtime

        # Mock config entry unload
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            # Call unload_entry - should handle the MQTT stop exception
            result = await async_unload_entry(hass, entry)

            # Should still return True despite the exception
            assert result is True

            # Verify stop was called
            mqtt_client.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_mqtt_stop_coroutine_handling(self, hass):
        """Test async_unload_entry with coroutine returned from MQTT stop."""
        # Create a mock config entry
        entry = MagicMock()
        entry.entry_id = "test_entry_id"

        # Create a mock MQTT client that returns a coroutine from stop
        mqtt_client = MagicMock(spec=SmappeeMqtt)

        async def mock_stop():
            return None

        mqtt_client.stop = MagicMock(return_value=mock_stop())

        # Create runtime data with the MQTT client
        runtime = RuntimeData(api=MagicMock(), sites={}, mqtt={12345: mqtt_client})

        # Attach runtime data to config entry
        entry.runtime_data = runtime

        # Mock config entry unload
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            # Call unload_entry - should await the coroutine
            result = await async_unload_entry(hass, entry)

            # Should return True
            assert result is True

            # Verify stop was called
            mqtt_client.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_runtime_resources_cancels_background_tasks(self):
        """Test tracked background tasks are explicitly cancelled on shutdown."""
        started = asyncio.Event()

        async def sleeper():
            started.set()
            await asyncio.sleep(60)

        task = asyncio.create_task(sleeper())
        await started.wait()
        runtime = RuntimeData(api=MagicMock(), sites={}, mqtt={}, background_tasks={task})

        await _async_shutdown_runtime_resources(runtime)

        assert task.cancelled()
        assert not runtime.background_tasks

    @pytest.mark.asyncio
    async def test_coordinator_shutdown_exception_handling(self, hass):
        """Test async_unload_entry with exception in coordinator shutdown."""
        # Create a mock config entry
        entry = MagicMock()
        entry.entry_id = "test_entry_id"

        # Create a mock coordinator that raises an exception on shutdown
        coordinator = MagicMock()
        coordinator.async_shutdown = AsyncMock(side_effect=RuntimeError("Failed to shutdown"))

        # Create runtime data with the problematic coordinator
        runtime = RuntimeData(
            api=MagicMock(),
            sites={
                12345: make_site_runtime(
                    stations={
                        "station1_uuid": make_station_runtime(
                            station_uuid="station1_uuid",
                            coordinator=coordinator,
                        )
                    }
                )
            },
            mqtt={},
        )

        # Attach runtime data to config entry
        entry.runtime_data = runtime

        # Mock config entry unload
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            # Call unload_entry - should handle the coordinator shutdown exception
            result = await async_unload_entry(hass, entry)

            # Should still return True despite the exception
            assert result is True

            # Verify shutdown was called
            coordinator.async_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_mqtt_callback_exception_handling(self, hass):
        """Test _setup_mqtt callback exception handling."""
        # Create test stations data with coordinator
        mock_coordinator = MagicMock()
        mock_coordinator.data = MagicMock()
        mock_coordinator.data.station = MagicMock()
        mock_coordinator.apply_mqtt_properties = MagicMock(side_effect=Exception("Callback error"))
        mock_coordinator.async_set_updated_data = MagicMock()

        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                coordinator=mock_coordinator,
                connectors={},
            )
        }

        # Mock SmappeeMqtt to capture the callback and the logger
        with (
            patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt") as mock_mqtt_class,
            patch(
                "custom_components.smappee_ev.mqtt_setup._LOGGER.exception"
            ) as mock_log_exception,
        ):
            # Call _setup_mqtt to get the callback
            _setup_mqtt(
                hass,
                "test-service-uuid",
                "STATION-SERIAL",
                12345,
                stations,
                "client-prefix",
                60,
            )

            # Extract the on_properties callback
            on_props_callback = mock_mqtt_class.call_args[1]["on_properties"]

            assert mock_coordinator.apply_mqtt_properties.call_count == 0
            assert mock_coordinator.async_set_updated_data.call_count == 0
            assert mock_log_exception.call_count == 0

            on_props_callback("test/topic", {"property": "value"})

            # Verify apply_mqtt_properties was called
            mock_coordinator.apply_mqtt_properties.assert_called_once_with(
                "test/topic", {"property": "value"}
            )

            # Verify async_set_updated_data was not called due to the exception
            mock_coordinator.async_set_updated_data.assert_not_called()

            mock_log_exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_entry_no_runtime_data(self, hass):
        """Test async_unload_entry with no runtime_data."""
        # Create a mock config entry with no runtime_data
        entry = MagicMock()
        entry.entry_id = "test_entry_id"

        # Mock config entry unload
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            # Call unload_entry - should handle the missing runtime_data gracefully
            result = await async_unload_entry(hass, entry)

            # Should still return True
            assert result is True

    @pytest.mark.asyncio
    async def test_unload_entry_runtime_data_not_instance(self, hass):
        """Test async_unload_entry with runtime_data that's not a RuntimeData instance."""
        # Create a mock config entry with invalid runtime_data
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.runtime_data = "not_a_runtime_data_instance"  # String, not RuntimeData

        # Mock config entry unload
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            # Call unload_entry - should handle the invalid runtime_data gracefully
            result = await async_unload_entry(hass, entry)

            # Should still return True
            assert result is True

    @pytest.mark.asyncio
    async def test_unload_entry_unload_platforms_failure(self, hass):
        """Test async_unload_entry when async_unload_platforms fails."""
        # Create a mock config entry
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        mqtt = MagicMock()
        mqtt.stop = AsyncMock()
        entry.runtime_data = RuntimeData(api=MagicMock(), sites={}, mqtt={1: mqtt})

        # Mock config entry unload to fail
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=False):
            # Call unload_entry
            result = await async_unload_entry(hass, entry)

            # Should return False
            assert result is False

            # Verify runtime_data was not removed
            assert hasattr(entry, "runtime_data")
            mqtt.stop.assert_not_awaited()
            assert entry.runtime_data.shutdown_task is None

    @pytest.mark.asyncio
    async def test_unload_entry_multiple_entries(self, hass):
        """Test async_unload_entry with multiple active entries."""
        # Create a mock config entry
        entry = MagicMock()
        entry.entry_id = "test_entry_id"
        entry.runtime_data = RuntimeData(api=MagicMock(), sites={}, mqtt={})

        # Mock config entry unload
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            # Setup registered service sentinel
            hass.services.async_register("smappee_ev", "start_charging", MagicMock())

            # Call unload_entry
            result = await async_unload_entry(hass, entry)

            # Should return True
            assert result is True

            # Verify services remain registered domain-wide after unload.
            assert hass.services.has_service("smappee_ev", "start_charging")
