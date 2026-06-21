# tests/test_platforms.py
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.button import ButtonEntity
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
import pytest

from custom_components.smappee_ev import (
    binary_sensor,
    button,
    light,
    number,
    select,
    sensor,
    switch,
)
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
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
from custom_components.smappee_ev.state import (
    ConnectorState,
    IntegrationData,
    SiteData,
    SiteState,
    StationState,
)
from tests.factories import (
    make_config_entry,
    make_connector_client,
    make_connector_runtime,
    make_led_runtime,
    make_runtime_data,
    make_site_runtime,
    make_station_client,
    make_station_coordinator,
    make_station_runtime,
)


@pytest.fixture
def mock_runtime_data():
    """Create mock runtime data."""
    return make_runtime_data(
        sites={
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
                            "connector_uuid": make_connector_runtime(
                                connector_key="connector_uuid",
                                connector_uuid="connector_uuid",
                                connector_client=MagicMock(),
                            )
                        },
                    )
                },
            )
        }
    )


@pytest.fixture
def mock_config_entry(mock_runtime_data):
    """Create mock config entry."""
    return make_config_entry(runtime_data=mock_runtime_data)


def _typed_runtime_data():
    """Create typed runtime containers for platform setup regression coverage."""
    station_client = make_station_client(service_location_id=317443, serial="STATION123")
    station_client.set_brightness = AsyncMock()
    connector_client = make_connector_client(
        service_location_id=317443,
        connector_number=1,
        smart_device_uuid="connector-uuid",
        serial="STATION123",
    )
    coordinator = make_station_coordinator(
        station_client=station_client,
        station_state=StationState(led_brightness=40, mqtt_connected=True),
        connectors={"connector-uuid": ConnectorState(connector_number=1)},
    )
    coordinator.dashboard_client = None

    return make_runtime_data(
        sites={
            317418: make_site_runtime(
                site_location_id=317418,
                site_name="Typed Site",
                site_function_type="SERVICELOCATION",
                site_uuid="site-uuid",
                gateway_serial="GATEWAY123",
                gateway_type="Infinity",
                measurement_location_ids=[317418],
                stations={
                    "station-uuid": make_station_runtime(
                        site_location_id=317418,
                        control_location_id=317443,
                        station_uuid="station-uuid",
                        serial="STATION123",
                        station_client=station_client,
                        coordinator=coordinator,
                        led_devices={
                            "led-device-1": make_led_runtime(
                                led_key="led-device-1",
                                led_device_id="led-device-1",
                                led_device_name="LED Ring",
                            )
                        },
                        connectors={
                            "connector-uuid": make_connector_runtime(
                                connector_key="connector-uuid",
                                connector_uuid="connector-uuid",
                                connector_position=1,
                                connector_client=connector_client,
                            )
                        },
                    )
                },
            )
        }
    )


@pytest.mark.asyncio
async def test_platform_setup_accepts_typed_runtime_containers(hass: HomeAssistant):
    """Ensure platforms can set up from dataclass runtime containers."""
    entry = make_config_entry(runtime_data=_typed_runtime_data())

    expectations = [
        (button, 5),
        (binary_sensor, 1),
        (light, 1),
        (number, 3),
        (select, 1),
        (switch, 3),
    ]
    for platform, expected_count in expectations:
        async_add_entities = MagicMock()
        await platform.async_setup_entry(hass, entry, async_add_entities)

        entities = async_add_entities.call_args.args[0]
        assert len(entities) == expected_count

    async_add_entities = MagicMock()
    await sensor.async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    assert sum(isinstance(entity, StationGridPower) for entity in entities) == 1
    assert sum(isinstance(entity, ConnectorPowerSensor) for entity in entities) == 1


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
        runtime.sites = {}

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
        runtime.sites = {}

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
                317418: make_site_runtime(
                    site_location_id=317418,
                    site_coordinator=site_coord,
                    stations={
                        "station-a": make_station_runtime(
                            site_location_id=317418,
                            control_location_id=317443,
                            station_uuid="station-a",
                            coordinator=station_coord_1,
                            station_client=MagicMock(),
                            connectors={
                                "connector-a": make_connector_runtime(
                                    connector_key="connector-a",
                                    connector_uuid="connector-a",
                                    connector_client=MagicMock(),
                                )
                            },
                        ),
                        "station-b": make_station_runtime(
                            site_location_id=317418,
                            control_location_id=317443,
                            station_uuid="station-b",
                            coordinator=station_coord_2,
                            station_client=MagicMock(),
                            connectors={
                                "connector-b": make_connector_runtime(
                                    connector_key="connector-b",
                                    connector_uuid="connector-b",
                                    connector_client=MagicMock(),
                                )
                            },
                        ),
                    },
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
                317418: make_site_runtime(
                    site_location_id=317418,
                    stations={
                        "station-uuid": make_station_runtime(
                            site_location_id=317418,
                            control_location_id=317443,
                            station_uuid="station-uuid",
                            serial="STATION123",
                            coordinator=coordinator,
                            station_client=station_client,
                            connectors={
                                "connector-uuid": make_connector_runtime(
                                    connector_key="connector-uuid",
                                    connector_uuid="connector-uuid",
                                    connector_position=1,
                                    connector_client=connector_client,
                                )
                            },
                        )
                    },
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
        runtime.sites = {}

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
                317418: make_site_runtime(
                    site_location_id=317418,
                    stations={
                        "station-uuid": make_station_runtime(
                            site_location_id=317418,
                            control_location_id=317443,
                            station_uuid="station-uuid",
                            serial="STATION123",
                            coordinator=coordinator,
                            station_client=station_client,
                            connectors={
                                "connector-uuid": make_connector_runtime(
                                    connector_key="connector-uuid",
                                    connector_uuid="connector-uuid",
                                    connector_position=1,
                                    connector_client=connector_client,
                                )
                            },
                            led_devices={
                                "led-device-1": make_led_runtime(
                                    led_key="led-device-1",
                                    led_device_name="LED Ring",
                                )
                            },
                        )
                    },
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
                317418: make_site_runtime(
                    site_location_id=317418,
                    stations={
                        "station-uuid": make_station_runtime(
                            site_location_id=317418,
                            control_location_id=317443,
                            station_uuid="station-uuid",
                            serial="STATION123",
                            coordinator=coordinator,
                            station_client=station_client,
                            connectors={
                                "connector-uuid": make_connector_runtime(
                                    connector_key="connector-uuid",
                                    connector_uuid="connector-uuid",
                                    connector_position=1,
                                    connector_client=connector_client,
                                )
                            },
                        )
                    },
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
