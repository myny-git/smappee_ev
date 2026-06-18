# tests/test_platforms.py
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.button import ButtonEntity
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
import pytest

from custom_components.smappee_ev import button, light, sensor, switch
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.data import IntegrationData, SiteData, SiteState, StationState
from custom_components.smappee_ev.helpers import (
    build_connector_label,
    connector_device_identifier,
    led_device_identifier,
    make_device_info,
    make_unique_id,
    station_device_identifier,
)
from custom_components.smappee_ev.light import SmappeeLedLight
from custom_components.smappee_ev.sensor import ConnectorPowerSensor, StationGridPower
from tests.factories import (
    make_config_entry,
    make_connector_client,
    make_runtime_data,
    make_site,
    make_station_bucket,
    make_station_client,
    make_station_coordinator,
)


@pytest.fixture
def mock_runtime_data():
    """Create mock runtime data."""
    return make_runtime_data(
        sites={
            12345: make_site(
                stations={
                    "station_uuid": make_station_bucket(
                        coordinator=MagicMock(spec=SmappeeCoordinator),
                        station_client=MagicMock(),
                        connector_clients={"connector_uuid": MagicMock()},
                    )
                }
            )
        }
    )


@pytest.fixture
def mock_config_entry(mock_runtime_data):
    """Create mock config entry."""
    return make_config_entry(runtime_data=mock_runtime_data)


class TestButtonPlatform:
    """Test cases for button platform."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self, hass: HomeAssistant, mock_config_entry):
        """Test button platform setup."""
        async_add_entities = MagicMock()

        await button.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Verify entities were added
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) > 0
        assert all(isinstance(entity, ButtonEntity) for entity in entities)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant):
        """Test button platform setup with no sites."""
        runtime = make_runtime_data()
        runtime.sites = None

        entry = make_config_entry(runtime_data=runtime)

        async_add_entities = MagicMock()

        await button.async_setup_entry(hass, entry, async_add_entities)

        # Should add empty list with update_before_add=True
        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_async_setup_entry_empty_sites(self, hass: HomeAssistant):
        """Test button platform setup with empty sites."""
        runtime = make_runtime_data()
        entry = make_config_entry(runtime_data=runtime)

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

        await sensor.async_setup_entry(hass, mock_config_entry, async_add_entities)

        # Verify entities were added
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) > 0
        assert all(isinstance(entity, SensorEntity) for entity in entities)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant):
        """Test sensor platform setup with no sites."""
        runtime = make_runtime_data()
        runtime.sites = None

        entry = make_config_entry(runtime_data=runtime)

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

        runtime = make_runtime_data(
            sites={
                317418: make_site(
                    stations={
                        "station-a": make_station_bucket(
                            coordinator=station_coord_1,
                            station_client=MagicMock(),
                            connector_clients={"connector-a": MagicMock()},
                        ),
                        "station-b": make_station_bucket(
                            coordinator=station_coord_2,
                            station_client=MagicMock(),
                            connector_clients={"connector-b": MagicMock()},
                        ),
                    },
                    site_coordinator=site_coord,
                )
            }
        )
        entry = make_config_entry(runtime_data=runtime)
        async_add_entities = MagicMock()

        await sensor.async_setup_entry(hass, entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert sum(isinstance(entity, StationGridPower) for entity in entities) == 1
        assert sum(isinstance(entity, ConnectorPowerSensor) for entity in entities) == 2
        unique_ids = [entity.unique_id for entity in entities]
        assert len(unique_ids) == len(set(unique_ids))

    @pytest.mark.asyncio
    async def test_connector_sensor_metadata_uses_station_device_hierarchy(
        self, hass: HomeAssistant
    ):
        """Protect HA device registry identifiers for connector entities."""
        station_client = make_station_client()
        connector_client = make_connector_client(
            service_location_id=317443,
            connector_number=1,
            smart_device_uuid="connector-uuid",
            serial="STATION123",
        )
        coordinator = make_station_coordinator(
            station_client=station_client,
            connectors={
                "connector-uuid": MagicMock(
                    connector_number=1,
                    power_total=7200,
                    api_available=True,
                )
            },
        )
        runtime = make_runtime_data(
            sites={
                317418: make_site(
                    stations={
                        "station-uuid": make_station_bucket(
                            coordinator=coordinator,
                            station_client=station_client,
                            connector_clients={"connector-uuid": connector_client},
                        )
                    }
                )
            }
        )
        entry = make_config_entry(runtime_data=runtime)
        async_add_entities = MagicMock()

        await sensor.async_setup_entry(hass, entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        power_sensor = next(
            entity for entity in entities if isinstance(entity, ConnectorPowerSensor)
        )
        assert power_sensor.unique_id == (
            "317418:STATION123:station-uuid:connector-uuid:sensor:power_total"
        )
        assert power_sensor.native_value == 7200.0
        assert power_sensor.device_info["identifiers"] == {
            connector_device_identifier(317418, 317443, "STATION123", "connector-uuid")
        }
        assert power_sensor.device_info["via_device"] == station_device_identifier(
            317418, 317443, "STATION123"
        )


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
        runtime = make_runtime_data()
        runtime.sites = None

        entry = make_config_entry(runtime_data=runtime)

        async_add_entities = MagicMock()

        await light.async_setup_entry(hass, entry, async_add_entities)

        async_add_entities.assert_called_once_with([], False)

    @pytest.mark.asyncio
    async def test_led_light_metadata_uses_led_device_hierarchy(self, hass: HomeAssistant):
        """Protect HA device registry identifiers for LED controller entities."""
        station_client = make_station_client()
        station_client.set_brightness = AsyncMock()
        connector_client = make_connector_client(
            service_location_id=317443,
            connector_number=1,
            smart_device_uuid="connector-uuid",
            serial="STATION123",
        )
        coordinator = make_station_coordinator(
            station_client=station_client,
            station_state=StationState(led_brightness=40, available=True),
        )
        runtime = make_runtime_data(
            sites={
                317418: make_site(
                    stations={
                        "station-uuid": make_station_bucket(
                            coordinator=coordinator,
                            station_client=station_client,
                            connector_clients={"connector-uuid": connector_client},
                            led_devices={"led-device-1": {"name": "LED Ring"}},
                        )
                    }
                )
            }
        )
        entry = make_config_entry(runtime_data=runtime)
        async_add_entities = MagicMock()

        await light.async_setup_entry(hass, entry, async_add_entities)

        led_light = async_add_entities.call_args[0][0][0]
        assert isinstance(led_light, SmappeeLedLight)
        assert led_light.unique_id == "317418:STATION123:station-uuid:light:led"
        assert led_light.is_on is True
        assert led_light.brightness == 102
        assert led_light.device_info["identifiers"] == {
            led_device_identifier(317418, 317443, "STATION123", "led-device-1")
        }
        assert led_light.device_info["via_device"] == station_device_identifier(
            317418, 317443, "STATION123"
        )


class TestSwitchPlatform:
    """Test cases for switch platform metadata."""

    @pytest.mark.asyncio
    async def test_availability_switch_metadata_uses_station_device_hierarchy(
        self, hass: HomeAssistant
    ):
        """Protect HA device registry identifiers for station switch entities."""
        station_client = make_station_client()
        connector_client = make_connector_client(
            service_location_id=317443,
            connector_number=1,
            smart_device_uuid="connector-uuid",
            serial="STATION123",
        )
        coordinator = make_station_coordinator(
            station_client=station_client,
            station_state=StationState(available=False),
        )
        runtime = make_runtime_data(
            sites={
                317418: make_site(
                    stations={
                        "station-uuid": make_station_bucket(
                            coordinator=coordinator,
                            station_client=station_client,
                            connector_clients={"connector-uuid": connector_client},
                        )
                    }
                )
            }
        )
        entry = make_config_entry(runtime_data=runtime)
        async_add_entities = MagicMock()

        await switch.async_setup_entry(hass, entry, async_add_entities)

        entities = async_add_entities.call_args[0][0]
        assert all(isinstance(entity, SwitchEntity) for entity in entities)
        availability_switch = next(
            entity for entity in entities if isinstance(entity, switch.SmappeeAvailabilitySwitch)
        )
        assert availability_switch.unique_id == (
            "317418:STATION123:station-uuid:switch:station_available"
        )
        assert availability_switch.is_on is False
        assert availability_switch.device_info["identifiers"] == {
            station_device_identifier(317418, 317443, "STATION123"),
            ("smappee_ev", "317418:STATION123:station-uuid"),
        }
        assert availability_switch.device_info["via_device"] == ("smappee_ev", "site:317418")


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


class TestPlatformHelpers:
    """Test platform helper functions."""

    def test_build_connector_label(self):
        """Test connector label building."""
        api_client = MagicMock()
        api_client.connector_number = 1

        assert build_connector_label(api_client, "connector-test-uuid") == "Connector 1"

        api_client.connector_number = None
        assert build_connector_label(api_client, "connector-test-uuid") == "Connector uuid"

    def test_make_unique_id(self):
        """Test unique ID generation."""
        assert (
            make_unique_id(12345, "SERIAL123", "station-uuid", "connector-uuid", "power")
            == "12345:SERIAL123:station-uuid:connector-uuid:power"
        )
        assert (
            make_unique_id(12345, "SERIAL123", "station-uuid", None, "grid_power")
            == "12345:SERIAL123:station-uuid:grid_power"
        )

    def test_make_device_info(self):
        """Test device info generation."""
        result = make_device_info(
            12345,
            "SERIAL123",
            "station-uuid",
            station_name="Garage Charger",
            station_model="EV Wall Business",
        )

        assert result["identifiers"] == {
            station_device_identifier(12345, 12345, "SERIAL123"),
            ("smappee_ev", "12345:SERIAL123:station-uuid"),
        }
        assert result["name"] == "Garage Charger"
        assert result["model"] == "EV Wall Business"


class TestPlatformErrors:
    """Test platform error handling."""

    @pytest.mark.asyncio
    async def test_setup_with_corrupted_sites(self, hass: HomeAssistant):
        """Test platform setup with corrupted sites data."""
        runtime = make_runtime_data()
        runtime.sites = "invalid_data"  # Should be dict, not string

        entry = make_config_entry(runtime_data=runtime)

        async_add_entities = MagicMock()

        await button.async_setup_entry(hass, entry, async_add_entities)

        async_add_entities.assert_called_once_with([], False)
