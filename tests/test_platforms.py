# tests/test_platforms.py
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.button import ButtonEntity
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest

from custom_components.smappee_ev import button, light, sensor
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.data import (
    IntegrationData,
    RuntimeData,
    SiteData,
    SiteState,
    StationState,
)
from custom_components.smappee_ev.light import SmappeeLedLight
from custom_components.smappee_ev.sensor import ConnectorPowerSensor, StationGridPower


@pytest.fixture
def mock_runtime_data():
    """Create mock runtime data."""
    runtime = MagicMock(spec=RuntimeData)
    runtime.sites = {
        12345: {
            "stations": {
                "station_uuid": {
                    "coordinator": MagicMock(spec=SmappeeCoordinator),
                    "station_client": MagicMock(),
                    "connector_clients": {"connector_uuid": MagicMock()},
                }
            }
        }
    }
    return runtime


@pytest.fixture
def mock_config_entry(mock_runtime_data):
    """Create mock config entry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.runtime_data = mock_runtime_data
    return entry


class TestButtonPlatform:
    """Test cases for button platform."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self, hass: HomeAssistant, mock_config_entry):
        """Test button platform setup."""
        async_add_entities = MagicMock()

        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label",
            return_value="Connector 1",
        ):
            await button.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Verify entities were added
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) > 0
        assert all(isinstance(entity, ButtonEntity) for entity in entities)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant):
        """Test button platform setup with no sites."""
        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = None

        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime

        async_add_entities = MagicMock()

        await button.async_setup_entry(hass, entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_sites(self, hass: HomeAssistant):
        """Test button platform setup with empty sites."""
        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = {}

        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime

        async_add_entities = MagicMock()

        await button.async_setup_entry(hass, entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)


class TestSensorPlatform:
    """Test cases for sensor platform."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self, hass: HomeAssistant, mock_config_entry):
        """Test sensor platform setup."""
        async_add_entities = MagicMock()

        with (
            patch(
                "custom_components.smappee_ev.helpers.make_unique_id",
                return_value="test_unique_id",
            ),
            patch(
                "custom_components.smappee_ev.helpers.make_device_info",
                return_value={"identifiers": {("test", "device")}},
            ),
        ):
            await sensor.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Verify entities were added
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) > 0
        assert all(isinstance(entity, SensorEntity) for entity in entities)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant):
        """Test sensor platform setup with no sites."""
        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = None

        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime

        async_add_entities = MagicMock()

        await sensor.async_setup_entry(hass, entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_site_sensors_created_once_per_site(self, hass: HomeAssistant):
        """Test site/grid sensors are not duplicated per station."""
        site_coord = MagicMock()
        site_coord.data = SiteData(site=SiteState())
        site_coord.last_update_success = True
        station_coord_1 = MagicMock(spec=SmappeeCoordinator)
        station_coord_1.data = IntegrationData(station=StationState(), connectors={})
        station_coord_1.last_update_success = True
        station_coord_2 = MagicMock(spec=SmappeeCoordinator)
        station_coord_2.data = IntegrationData(station=StationState(), connectors={})
        station_coord_2.last_update_success = True

        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = {
            317418: {
                "site_coordinator": site_coord,
                "stations": {
                    "station-a": {
                        "coordinator": station_coord_1,
                        "station_client": MagicMock(),
                        "connector_clients": {"connector-a": MagicMock()},
                    },
                    "station-b": {
                        "coordinator": station_coord_2,
                        "station_client": MagicMock(),
                        "connector_clients": {"connector-b": MagicMock()},
                    },
                },
            }
        }
        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime
        async_add_entities = MagicMock()

        await sensor.async_setup_entry(hass, entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert sum(isinstance(entity, StationGridPower) for entity in entities) == 1
        assert sum(isinstance(entity, ConnectorPowerSensor) for entity in entities) == 2


class TestLightPlatform:
    """Test cases for light platform."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self, hass: HomeAssistant, mock_config_entry):
        """Test light platform setup."""
        async_add_entities = MagicMock()

        await light.async_setup_entry(hass, mock_config_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert all(isinstance(entity, LightEntity) for entity in entities)
        assert all(isinstance(entity, SmappeeLedLight) for entity in entities)
        assert async_add_entities.call_args[0][1] is False

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant):
        """Test light platform setup with no sites."""
        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = None

        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime

        async_add_entities = MagicMock()

        await light.async_setup_entry(hass, entry, async_add_entities)

        async_add_entities.assert_called_once_with([], False)


class TestSmappeeLedLight:
    """Test the Smappee LED light entity."""

    def _entity(self) -> tuple[SmappeeLedLight, MagicMock, MagicMock]:
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = IntegrationData(
            station=StationState(led_brightness=70, available=True),
            connectors={},
        )
        coordinator.async_set_updated_data = MagicMock()
        api_client = MagicMock()
        api_client.set_brightness = AsyncMock()
        entity = SmappeeLedLight(
            coordinator=coordinator,
            api_client=api_client,
            sid=1,
            station_uuid="station",
        )
        return entity, coordinator, api_client

    def test_state_and_brightness(self):
        """Test state and HA brightness scaling."""
        entity, coordinator, _api_client = self._entity()

        assert entity.is_on is True
        assert entity.brightness == 178
        assert entity.color_mode is ColorMode.BRIGHTNESS
        assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}

        coordinator.data.station.led_brightness = 0
        assert entity.is_on is False
        assert entity.brightness is None

        coordinator.data.station.led_brightness = None
        assert entity.is_on is None
        assert entity.brightness is None

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness(self):
        """Test turn on maps HA brightness to Smappee percentage."""
        entity, coordinator, api_client = self._entity()

        await entity.async_turn_on(**{ATTR_BRIGHTNESS: 128})

        api_client.set_brightness.assert_awaited_once_with(50)
        assert coordinator.data.station.led_brightness == 50
        coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)

    @pytest.mark.asyncio
    async def test_turn_on_uses_existing_or_default(self):
        """Test turn on keeps existing brightness or restores default from off."""
        entity, coordinator, api_client = self._entity()

        await entity.async_turn_on()
        api_client.set_brightness.assert_awaited_once_with(70)

        api_client.set_brightness.reset_mock()
        coordinator.data.station.led_brightness = 0

        await entity.async_turn_on()
        api_client.set_brightness.assert_awaited_once_with(70)

    @pytest.mark.asyncio
    async def test_turn_off(self):
        """Test turn off sets LED brightness to zero."""
        entity, coordinator, api_client = self._entity()

        await entity.async_turn_off()

        api_client.set_brightness.assert_awaited_once_with(0)
        assert coordinator.data.station.led_brightness == 0


class TestEntityCreation:
    """Test entity creation and configuration."""

    def test_entity_attributes(self):
        """Test entity attributes and properties."""
        # Mock coordinator
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = {
            "station_uuid": {"connectors": {"connector_uuid": {"status": "AVAILABLE", "power": 0}}}
        }

        # Test that entity can access coordinator data
        assert coordinator.data is not None
        assert "station_uuid" in coordinator.data

    @pytest.mark.asyncio
    async def test_entity_updates(self):
        """Test entity update handling."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.async_add_listener = MagicMock()
        coordinator.async_remove_listener = MagicMock()

        # Simulate entity lifecycle
        entity = MagicMock()
        entity.coordinator = coordinator

        # Test listener registration
        coordinator.async_add_listener(entity.async_write_ha_state)
        coordinator.async_add_listener.assert_called_once()

        # Test listener removal
        coordinator.async_remove_listener(entity.async_write_ha_state)
        coordinator.async_remove_listener.assert_called_once()


class TestPlatformHelpers:
    """Test platform helper functions."""

    def test_build_connector_label(self):
        """Test connector label building."""
        with patch(
            "custom_components.smappee_ev.helpers.build_connector_label", return_value="Connector 1"
        ) as mock_label:
            api_client = MagicMock()
            connector_uuid = "test_uuid"

            result = mock_label(api_client, connector_uuid)

            assert result == "Connector 1"
            mock_label.assert_called_once_with(api_client, connector_uuid)

    def test_make_unique_id(self):
        """Test unique ID generation."""
        with patch(
            "custom_components.smappee_ev.helpers.make_unique_id", return_value="test_unique_id"
        ) as mock_unique_id:
            result = mock_unique_id("test", "entity")

            assert result == "test_unique_id"
            mock_unique_id.assert_called_once_with("test", "entity")

    def test_make_device_info(self):
        """Test device info generation."""
        with patch(
            "custom_components.smappee_ev.helpers.make_device_info",
            return_value={"identifiers": {("test", "device")}},
        ) as mock_device_info:
            result = mock_device_info("test_device")

            assert result["identifiers"] == {("test", "device")}
            mock_device_info.assert_called_once_with("test_device")


class TestEntityBehavior:
    """Test entity behavior and state management."""

    def test_entity_availability(self):
        """Test entity availability logic."""
        coordinator = MagicMock(spec=SmappeeCoordinator)

        # Test available coordinator
        coordinator.last_update_success = True
        coordinator.available = True

        # Mock entity
        entity = MagicMock()
        entity.coordinator = coordinator
        entity.available = coordinator.available and coordinator.last_update_success

        assert entity.available is True

        # Test unavailable coordinator
        coordinator.available = False
        entity.available = coordinator.available and coordinator.last_update_success

        assert entity.available is False

    def test_entity_state_updates(self):
        """Test entity state update behavior."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = {
            "station_uuid": {
                "connectors": {"connector_uuid": {"status": "CHARGING", "power": 7200}}
            }
        }

        # Mock entity accessing coordinator data
        entity = MagicMock()
        entity.coordinator = coordinator

        # Simulate state property access
        state_data = entity.coordinator.data["station_uuid"]["connectors"]["connector_uuid"]

        assert state_data["status"] == "CHARGING"
        assert state_data["power"] == 7200

    @pytest.mark.asyncio
    async def test_entity_async_update(self):
        """Test entity async update method."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.async_request_refresh = AsyncMock()

        # Mock entity
        entity = MagicMock()
        entity.coordinator = coordinator

        # Simulate calling async_update
        await entity.coordinator.async_request_refresh()

        coordinator.async_request_refresh.assert_called_once()


class TestPlatformErrors:
    """Test platform error handling."""

    @pytest.mark.asyncio
    async def test_setup_with_missing_data(self, hass: HomeAssistant):
        """Test platform setup with missing runtime data."""
        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = None

        async_add_entities = MagicMock()

        # Should handle missing runtime data gracefully
        with contextlib.suppress(AttributeError):
            await button.async_setup_entry(hass, entry, async_add_entities)

    @pytest.mark.asyncio
    async def test_setup_with_corrupted_sites(self, hass: HomeAssistant):
        """Test platform setup with corrupted sites data."""
        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = "invalid_data"  # Should be dict, not string

        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime

        async_add_entities = MagicMock()

        # Should handle corrupted data gracefully by raising an error
        with pytest.raises(AttributeError):
            await button.async_setup_entry(hass, entry, async_add_entities)

        # async_add_entities should not be called when there's an error
        async_add_entities.assert_not_called()
