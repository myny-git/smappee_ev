# tests/test_init.py
"""Test the Smappee EV integration initialization."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientError, ClientSession
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import pytest

from custom_components.smappee_ev import (
    _assign_connectors,
    _build_mqtt_routes,
    _connector_uuid,
    _dashboard_client_configured,
    _dashboard_discover_service_locations,
    _fallback_assign,
    _fetch_dashboard_connector_mapping,
    _find_in,
    _is_connector,
    _is_station,
    _make_station_clients,
    _mqtt_specs_from_highlevel_configs,
    _normalize_dashboard_service_location,
    _safe_str,
    _split_devices,
    async_remove_config_entry_device,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.smappee_ev.const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.smappee_ev.data import RuntimeData
from custom_components.smappee_ev.discovery import MqttChannelSpec, SmappeeLocationTopology
from tests.factories import make_connector_runtime, make_site_runtime, make_station_runtime


class MockResponseContext:
    """Simple async context manager for mocked aiohttp responses."""

    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return None


def test_mqtt_routes_parent_power_to_site_and_child_power_to_station():
    """Test explicit parent/child MQTT power routing."""
    site_coord = MagicMock()
    station_coord = MagicMock()
    specs = [
        MqttChannelSpec(
            service_location_id=317418,
            role="grid",
            metric="activePower",
            topic="servicelocation/uuid317418/power",
            username=None,
            password=None,
            aspect_paths=[],
        ),
        MqttChannelSpec(
            service_location_id=317443,
            role="car_charger",
            metric="activePower",
            topic="servicelocation/uuid317443/power",
            username=None,
            password=None,
            aspect_paths=[],
        ),
    ]

    routes = _build_mqtt_routes(
        specs,
        site_coord,
        {"station": make_station_runtime(coordinator=station_coord)},
    )

    assert routes["servicelocation/uuid317418/power"] == [site_coord]
    assert routes["servicelocation/uuid317443/power"] == [station_coord]


def test_mqtt_routes_shared_power_topic_to_site_and_station_once():
    """Test shared Dashboard power topics route to every needed coordinator once."""
    site_coord = MagicMock()
    station_coord = MagicMock()
    topic = "servicelocation/shared_uuid/power"
    specs = [
        MqttChannelSpec(123, "car_charger", "activePower", topic, None, None, []),
        MqttChannelSpec(123, "car_charger", "current", topic, None, None, []),
        MqttChannelSpec(123, "grid", "activePower", topic, None, None, []),
        MqttChannelSpec(123, "grid", "current", topic, None, None, []),
        MqttChannelSpec(123, "production", "activePower", topic, None, None, []),
        MqttChannelSpec(123, "consumption", "consumption", topic, None, None, []),
    ]

    routes = _build_mqtt_routes(
        specs,
        site_coord,
        {"station": make_station_runtime(coordinator=station_coord)},
    )

    assert routes[topic] == [station_coord, site_coord]


def test_mqtt_specs_keep_all_roles_for_shared_power_topic():
    """Test highlevel parsing keeps roles that share one physical MQTT topic."""
    topic = "servicelocation/shared_uuid/power"
    config = {
        "measurements": [
            {
                "type": "APPLIANCE",
                "appliance": {"type": "CAR_CHARGER"},
                "updateChannels": {
                    "activePower": {
                        "protocol": "MQTT",
                        "name": topic,
                        "aspectPaths": [{"path": "$.channelData[3]"}],
                    }
                },
            },
            {
                "type": "GRID",
                "updateChannels": {
                    "activePower": {
                        "protocol": "MQTT",
                        "name": topic,
                        "aspectPaths": [{"path": "$.channelData[9]"}],
                    }
                },
            },
            {
                "type": "PRODUCTION",
                "updateChannels": {
                    "activePower": {
                        "protocol": "MQTT",
                        "name": topic,
                        "aspectPaths": [{"path": "$.channelData[15]"}],
                    }
                },
            },
        ],
        "updateSpecs": {
            "consumption": {
                "channel": {
                    "protocol": "MQTT",
                    "name": topic,
                    "aspectPaths": [{"path": "$.consumptionPower"}],
                }
            }
        },
    }

    specs = _mqtt_specs_from_highlevel_configs({123: config})

    assert [spec.role for spec in specs] == [
        "car_charger",
        "grid",
        "production",
        "consumption",
    ]


@pytest.fixture
def mock_dashboard_handle():
    """Create a generic dashboard/runtime handle."""
    return MagicMock()


@pytest.fixture
def mock_session():
    """Create a mock aiohttp ClientSession."""
    session = MagicMock(spec=ClientSession)
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock()
    mock_response.text = AsyncMock(return_value="")
    session.get = MagicMock(return_value=MockResponseContext(mock_response))
    session.post = AsyncMock(return_value=mock_response)
    return session, mock_response


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {
        "username": "test_user",
        "password": "test_password",
    }
    entry.options = {}
    entry.entry_id = "test_entry_id"
    entry.title = "Test Smappee Account"
    entry.state = ConfigEntryState.LOADED

    # Add runtime_data attribute that will be set by async_setup_entry
    entry.runtime_data = None

    return entry


class TestInitFunctions:
    """Test the initialization helper functions."""

    def test_is_station(self):
        """Test _is_station function."""
        # Test with type as string
        assert _is_station({"type": "CHARGINGSTATION"}) is True
        assert _is_station({"type": "chargingstation"}) is True
        assert _is_station({"type": "OTHER"}) is False

        # Test with type as dict
        assert _is_station({"type": {"category": "CHARGINGSTATION"}}) is True
        assert _is_station({"type": {"category": "chargingstation"}}) is True
        assert _is_station({"type": {"category": "OTHER"}}) is False

        # Test with missing type
        assert _is_station({}) is False

    def test_is_connector(self):
        """Test _is_connector function."""
        # Test with type as string
        assert _is_connector({"type": "CARCHARGER"}) is True
        assert _is_connector({"type": "carcharger"}) is True
        assert _is_connector({"type": "OTHER"}) is False

        # Test with type as dict
        assert _is_connector({"type": {"category": "CARCHARGER"}}) is True
        assert _is_connector({"type": {"category": "carcharger"}}) is True
        assert _is_connector({"type": {"category": "OTHER"}}) is False

        # Test with missing type
        assert _is_connector({}) is False

        # Dashboard detail payloads can omit the category but still carry carCharger metadata.
        assert _is_connector({"carCharger": {}}) is True

    def test_connector_uuid_prefers_mqtt_channel_uuid(self):
        """Test connector UUID discovery prefers the MQTT device UUID."""
        assert (
            _connector_uuid(
                {
                    "uuid": "dashboard_uuid",
                    "carCharger": {
                        "chargingStateUpdateChannel": {
                            "name": (
                                "servicelocation/site/etc/carcharger/acchargingcontroller/v1/"
                                "devices/mqtt_uuid/property/chargingstate"
                            )
                        }
                    },
                }
            )
            == "mqtt_uuid"
        )

    def test_safe_str(self):
        """Test _safe_str function."""
        # Valid strings
        assert _safe_str("test") == "test"
        assert _safe_str(" test ") == "test"  # Strips whitespace

        # Empty strings should return None
        assert _safe_str("") is None
        assert _safe_str(" ") is None

        # Non-string types
        assert _safe_str(123) == "123"

        # The actual implementation in __init__.py converts None to 'None' string
        assert _safe_str(None) is None

        # This is a separate mock test with a custom implementation
        with patch("custom_components.smappee_ev._safe_str") as mock_safe_str:
            # Make our mocked function return None only for "TypeError" input
            mock_safe_str.side_effect = lambda x: None if x == "TypeError" else _safe_str(x)
            assert mock_safe_str("TypeError") is None

    def test_dashboard_client_configured_with_username_password(self):
        """Test dashboard setup can start from username/password credentials."""
        dashboard_client = MagicMock()
        dashboard_client._token = None
        dashboard_client.refresh_token = None
        dashboard_client.username = "test_user"
        dashboard_client.password = "test_password"  # noqa: S105

        assert _dashboard_client_configured(dashboard_client) is True

    def test_normalize_dashboard_service_location_keeps_missing_serial(self):
        """Test discovery keeps candidate charging locations without top-level serials."""
        result = _normalize_dashboard_service_location(
            {
                "serviceLocationId": 12345,
                "serviceLocationUuid": "sl_uuid_1",
                "functionType": "CHARGINGSTATION",
                "name": "Home",
            }
        )

        assert result == {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "sl_uuid_1",
            "deviceSerialNumber": None,
            "chargingStation": {},
            "functionType": "CHARGINGSTATION",
            "name": "Home",
        }

    def test_normalize_dashboard_service_location_keeps_nested_charging_station(self):
        """Test chargingStation details override a non-charging functionType."""
        result = _normalize_dashboard_service_location(
            {
                "serviceLocationId": 12345,
                "serviceLocationUuid": "sl_uuid_1",
                "functionType": "ELECTRICITY",
                "chargingStation": {"serialNumber": "STATION123"},
                "name": "Home",
            }
        )

        assert result == {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "sl_uuid_1",
            "deviceSerialNumber": "STATION123",
            "chargingStation": {"serialNumber": "STATION123"},
            "functionType": "ELECTRICITY",
            "name": "Home",
        }

    def test_normalize_dashboard_service_location_uses_plural_charging_stations(self):
        """Test discovery accepts Dashboard payloads with chargingStations lists."""
        result = _normalize_dashboard_service_location(
            {
                "serviceLocationId": 12345,
                "serviceLocationUuid": "sl_uuid_1",
                "chargingStations": [{"serial": "STATION123"}],
                "name": "Home",
            }
        )

        assert result == {
            "serviceLocationId": 12345,
            "serviceLocationUuid": "sl_uuid_1",
            "deviceSerialNumber": "STATION123",
            "chargingStation": {"serial": "STATION123"},
            "functionType": None,
            "name": "Home",
        }

    @pytest.mark.asyncio
    async def test_dashboard_discover_service_locations_falls_back_without_function_type(self):
        """Test discovery still tries locations when Dashboard omits charging function types."""
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.async_get_service_locations_full_details = AsyncMock(
            return_value=[
                {
                    "serviceLocationId": 12345,
                    "serviceLocationUuid": "sl_uuid_1",
                    "functionType": "ELECTRICITY",
                    "deviceSerialNumber": "GATEWAY123",
                    "name": "Home",
                }
            ]
        )

        result = await _dashboard_discover_service_locations(dashboard_client)

        assert result == [
            {
                "serviceLocationId": 12345,
                "serviceLocationUuid": "sl_uuid_1",
                "deviceSerialNumber": "GATEWAY123",
                "chargingStation": {},
                "functionType": "ELECTRICITY",
                "name": "Home",
            }
        ]

    def test_find_in(self):
        """Test _find_in function."""
        # Direct key match
        assert _find_in({"serialNumber": "SN123"}, "serialNumber") == "SN123"
        assert _find_in({"serial": "SN123"}, "serialNumber", "serial") == "SN123"

        # Empty/None value
        assert _find_in({"serialNumber": ""}, "serialNumber") is None

        # Mock _safe_str for None input
        with patch("custom_components.smappee_ev._safe_str", return_value=None):
            assert _find_in({"serialNumber": None}, "serialNumber") is None

        # Property-based match in configurationProperties
        dev = {"configurationProperties": [{"spec": {"name": "serialNumber"}, "value": "SN456"}]}
        assert _find_in(dev, "nonexistent") == "SN456"

        # Property-based match in properties with value as dict
        dev = {"properties": [{"spec": {"name": "serialNumber"}, "value": {"value": "SN789"}}]}
        assert _find_in(dev, "nonexistent") == "SN789"

        # No match
        assert _find_in({"other": "value"}, "serialNumber", "serial") is None
        assert _find_in({}, "serialNumber") is None


class TestDeviceSplitting:
    """Test device splitting helpers."""

    def test_split_devices(self):
        """Test device splitting into stations and connectors."""
        devices = [
            {"id": "station1", "type": "CHARGINGSTATION"},
            {"id": "connector1", "type": "CARCHARGER"},
            {"id": "other", "type": "OTHER"},
            {"id": "station2", "type": {"category": "CHARGINGSTATION"}},
            {"id": "connector2", "type": {"category": "CARCHARGER"}},
        ]

        stations, connectors = _split_devices(devices)

        assert len(stations) == 2
        assert stations[0]["id"] == "station1"
        assert stations[1]["id"] == "station2"

        assert len(connectors) == 2
        assert connectors[0]["id"] == "connector1"
        assert connectors[1]["id"] == "connector2"


class TestSitePreparation:
    """Test site preparation functions."""

    @pytest.mark.asyncio
    async def test_fetch_dashboard_connector_mapping_success(self):
        """Test successful Dashboard connector mapping fetch."""
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.async_get_charging_station_details = AsyncMock(
            return_value={
                "modules": [
                    {
                        "position": 1,
                        "smartDevice": {
                            "uuid": "connector1",
                            "id": "conn1",
                            "type": {"category": "CARCHARGER"},
                        },
                    }
                ]
            }
        )

        station_devs = [{"uuid": "station_uuid", "id": "station_id", "serialNumber": "STATION1"}]
        result = await _fetch_dashboard_connector_mapping(dashboard_client, station_devs)

        dashboard_client.async_get_charging_station_details.assert_awaited_once_with("STATION1")
        assert result == {
            "STATION1": {
                "connectors": {
                    "connector1": {
                        "id": "conn1",
                        "position": 1,
                        "smart_device": {
                            "uuid": "connector1",
                            "id": "conn1",
                            "type": {"category": "CARCHARGER"},
                        },
                    }
                }
            }
        }

    @pytest.mark.asyncio
    async def test_fetch_dashboard_connector_mapping_client_error(self):
        """Test Dashboard connector mapping fetch with client error."""
        dashboard_client = MagicMock()
        dashboard_client._token = "dashboard_token"  # noqa: S105
        dashboard_client.refresh_token = None
        dashboard_client.async_get_charging_station_details = AsyncMock(side_effect=ClientError())

        station_devs = [{"uuid": "station_uuid", "id": "station_id"}]
        result = await _fetch_dashboard_connector_mapping(dashboard_client, station_devs)

        assert result == {}

    def test_make_station_clients(self):
        """Test making station clients."""
        # Mock SmappeeDeviceHandle
        with patch("custom_components.smappee_ev.SmappeeDeviceHandle"):
            station_devs = [
                {"uuid": "station1_uuid", "id": "station1_id", "serialNumber": "STATION1"},
                {"uuid": "station2_uuid", "id": "station2_id"},
            ]

            result = _make_station_clients("SITE_SERIAL", 12345, station_devs)

            # Check that station clients were created
            assert len(result) == 2
            assert "station1_uuid" in result
            assert "station2_uuid" in result

            # Check station client properties
            assert result["station1_uuid"].charging_station_serial == "STATION1"
            assert result["station1_uuid"].station_client is not None
            assert isinstance(result["station1_uuid"].connectors, dict)
            assert result["station1_uuid"].station_coordinator is None
            assert result["station1_uuid"].mqtt is None

    def test_assign_connectors(self):
        """Test connector assignment to stations."""
        # Mock SmappeeDeviceHandle
        with patch("custom_components.smappee_ev.SmappeeDeviceHandle") as mock_api_client_class:
            # Create stations
            stations = {
                "station1_uuid": make_station_runtime(
                    station_uuid="station1_uuid",
                    serial="STATION1",
                    connectors={},
                )
            }

            # Create car devices
            car_devs = [{"uuid": "connector1_uuid", "id": "connector1_id", "position": 1}]

            # Create mapping
            mapping = {
                "STATION1": {
                    "connectors": {"connector1_uuid": {"id": "conn1_mapped_id", "position": 2}}
                }
            }

            _assign_connectors(stations, car_devs, mapping, "SITE_SERIAL", 12345)

            # Check that connector was assigned to station
            assert "connector1_uuid" in stations["station1_uuid"].connectors

            # Check that API client was created with correct parameters
            mock_api_client_class.assert_called_once_with(
                "SITE_SERIAL",
                "connector1_uuid",
                "connector1_id",  # Should use the device ID, not the mapped ID
                12345,
                connector_number=2,  # Should use position from mapping
                charging_station_serial="STATION1",
                site_location_id=317418,
            )

    def test_fallback_assign(self):
        """Test fallback connector assignment."""
        # Mock SmappeeDeviceHandle
        with patch("custom_components.smappee_ev.SmappeeDeviceHandle") as mock_api_client_class:
            # Create stations with no connectors
            stations = {
                "station1_uuid": make_station_runtime(
                    station_uuid="station1_uuid",
                    serial="STATION1",
                    connectors={},
                )
            }

            # Create car devices
            car_devs = [
                {"uuid": "connector1_uuid", "id": "connector1_id", "position": 1},
                {"uuid": "connector2_uuid", "id": "connector2_id", "connectorNumber": 2},
            ]

            _fallback_assign(stations, car_devs, "SITE_SERIAL", 12345)

            # Check that connectors were assigned to the first station
            assert len(stations["station1_uuid"].connectors) == 2
            assert "connector1_uuid" in stations["station1_uuid"].connectors
            assert "connector2_uuid" in stations["station1_uuid"].connectors

            # Check correct position values were used
            assert mock_api_client_class.call_count == 2
            # Sort calls by connector UUID to ensure consistent test
            calls = sorted(
                mock_api_client_class.call_args_list, key=lambda x: x[0][1]
            )  # Sort by the connector_uuid argument

            # First connector should use position
            assert calls[0][0][1] == "connector1_uuid"
            assert calls[0][1]["connector_number"] == 1

            # Second connector should use connectorNumber
            assert calls[1][0][1] == "connector2_uuid"
            assert calls[1][1]["connector_number"] == 2

            # Station serial should be forwarded for API 2 chargingstations calls
            assert calls[0][1]["charging_station_serial"] == "STATION1"
            assert calls[1][1]["charging_station_serial"] == "STATION1"


class TestDomainSetup:
    """Test domain setup functions."""

    @staticmethod
    def _topology() -> SmappeeLocationTopology:
        return SmappeeLocationTopology(
            site_location_id=12345,
            site_location_uuid="sl_uuid_1",
            site_name="Home",
            site_function_type="DEFAULT",
            control_location_id=12345,
            control_location_uuid="sl_uuid_1",
            control_name="Home",
            control_function_type="DEFAULT",
            measurement_location_ids=[12345],
            charging_station_serial="SN123",
            site_gateway_serial="SN123",
            site_gateway_type=None,
            control_gateway_serial="SN123",
            control_gateway_type=None,
            write_access=True,
        )

    @pytest.mark.asyncio
    async def test_async_setup(self, hass):
        """Test async_setup function."""
        # Mock services registration
        with patch("custom_components.smappee_ev.register_services") as mock_register_services:
            mock_register_services.return_value = None

            # Initial call should register services
            result = await async_setup(hass, {})
            assert result is True
            assert DOMAIN not in hass.data
            mock_register_services.assert_called_once_with(hass)

            # Second call should not register services again
            mock_register_services.reset_mock()
            hass.services.async_register(DOMAIN, "start_charging", MagicMock())
            result = await async_setup(hass, {})
            assert result is True
            mock_register_services.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_setup_entry_success(self, hass, mock_config_entry, mock_session):
        """Test successful setup builds the expected site-first runtime shape."""
        session, response = mock_session

        site_coordinator = MagicMock()
        station_coordinator = MagicMock()
        station_client = MagicMock()
        connector_1 = MagicMock()
        connector_2 = MagicMock()
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=station_client,
                coordinator=station_coordinator,
                connectors={
                    "connector1": make_connector_runtime(
                        connector_key="connector1",
                        connector_uuid="connector1",
                        connector_client=connector_1,
                    ),
                    "connector2": make_connector_runtime(
                        connector_key="connector2",
                        connector_uuid="connector2",
                        connector_client=connector_2,
                    ),
                },
                site_coordinator=site_coordinator,
                highlevel_configs={12345: {"channels": []}},
            )
        }
        mqtt_client = MagicMock()

        with (
            patch("custom_components.smappee_ev.async_get_clientsession", return_value=session),
            patch(
                "custom_components.smappee_ev._dashboard_discover_topologies",
                return_value=[self._topology()],
            ),
            patch(
                "custom_components.smappee_ev._prepare_topology",
                return_value=(stations, mqtt_client),
            ),
            patch.object(
                hass.config_entries, "async_forward_entry_setups", return_value=None
            ) as mock_setup,
        ):
            result = await async_setup_entry(hass, mock_config_entry)

            assert result is True

            mock_setup.assert_awaited_once()
            assert mock_setup.await_args.args[0] is mock_config_entry

            assert hasattr(mock_config_entry, "runtime_data")
            runtime = mock_config_entry.runtime_data
            assert isinstance(runtime, RuntimeData)
            assert 12345 in runtime.mqtt
            assert runtime.mqtt[12345] == mqtt_client
            assert runtime.mqtt == {12345: mqtt_client}
            assert runtime.dashboard is runtime.api

            site = runtime.sites[12345]
            assert site.site_name == "Home"
            assert site.site_coordinator is site_coordinator
            assert site.site_uuid == "sl_uuid_1"
            assert site.control_location_ids == [12345]
            assert site.measurement_location_ids == [12345]
            assert site.highlevel_configs == {12345: {"channels": []}}

            station_bucket = site.stations["station1_uuid"]
            assert station_bucket.station_client is station_client
            assert station_bucket.station_coordinator is station_coordinator
            assert {
                key: connector.connector_client
                for key, connector in station_bucket.connectors.items()
            } == {
                "connector1": connector_1,
                "connector2": connector_2,
            }

    @pytest.mark.asyncio
    async def test_async_setup_entry_token_update_preserves_password(
        self, hass, mock_config_entry, mock_session
    ):
        """Test persisted token updates keep password for login fallback."""
        session, response = mock_session
        mock_config_entry.data = {
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_password",
            CONF_DASHBOARD_REFRESH_TOKEN: "old_refresh",
        }

        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=MagicMock(),
                coordinator=MagicMock(),
                connectors={
                    "connector1": make_connector_runtime(
                        connector_key="connector1",
                        connector_uuid="connector1",
                        connector_client=MagicMock(),
                    )
                },
            )
        }

        with (
            patch("custom_components.smappee_ev.async_get_clientsession", return_value=session),
            patch(
                "custom_components.smappee_ev._dashboard_discover_topologies",
                return_value=[self._topology()],
            ),
            patch("custom_components.smappee_ev._prepare_topology", return_value=(stations, None)),
            patch.object(hass.config_entries, "async_forward_entry_setups", return_value=None),
            patch.object(hass.config_entries, "async_update_entry") as update_entry,
        ):
            result = await async_setup_entry(hass, mock_config_entry)
            runtime = mock_config_entry.runtime_data
            runtime.dashboard._token_update_callback(
                {CONF_DASHBOARD_REFRESH_TOKEN: "new_dashboard_refresh"}
            )

        assert result is True
        update_entry.assert_called_once()
        args = update_entry.call_args.args
        _, kwargs = update_entry.call_args
        assert args[0] == mock_config_entry
        assert kwargs["data"][CONF_DASHBOARD_REFRESH_TOKEN] == "new_dashboard_refresh"
        assert kwargs["data"][CONF_PASSWORD] == "test_password"

    @pytest.mark.asyncio
    async def test_async_setup_entry_auth_failed(self, hass, mock_config_entry, mock_session):
        """Test config entry setup with dashboard discovery failure."""
        session, response = mock_session

        with (
            patch("custom_components.smappee_ev.async_get_clientsession", return_value=session),
            patch(
                "custom_components.smappee_ev._dashboard_discover_topologies",
                side_effect=ClientError("Auth failed"),
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, mock_config_entry)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_locations(self, hass, mock_config_entry, mock_session):
        """Test config entry setup with no service locations."""
        session, response = mock_session

        with (
            patch("custom_components.smappee_ev.async_get_clientsession", return_value=session),
            patch(
                "custom_components.smappee_ev._dashboard_discover_topologies",
                return_value=[],
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, mock_config_entry)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites_mapped(self, hass, mock_config_entry, mock_session):
        """Test config entry setup with service locations but no sites mapped."""
        session, response = mock_session

        with (
            patch("custom_components.smappee_ev.async_get_clientsession", return_value=session),
            patch("custom_components.smappee_ev._dashboard_discover_topologies") as mock_discover,
            patch("custom_components.smappee_ev._prepare_topology", return_value=(None, None)),
        ):
            # Return some service locations
            mock_discover.return_value = [self._topology()]

            # Should raise ConfigEntryNotReady
            with pytest.raises(ConfigEntryNotReady):
                await async_setup_entry(hass, mock_config_entry)

    @pytest.mark.asyncio
    async def test_async_setup_entry_cleans_partial_topology_on_auth_failure(
        self, hass, mock_config_entry, mock_session
    ):
        """Test successful topology resources are cleaned if another topology auth-fails."""
        session, response = mock_session
        stations = {
            "station1_uuid": make_station_runtime(
                station_uuid="station1_uuid",
                station_client=MagicMock(),
                coordinator=MagicMock(),
                connectors={
                    "connector1": make_connector_runtime(
                        connector_key="connector1",
                        connector_uuid="connector1",
                        connector_client=MagicMock(),
                    )
                },
            )
        }
        mqtt_client = MagicMock()

        with (
            patch("custom_components.smappee_ev.async_get_clientsession", return_value=session),
            patch(
                "custom_components.smappee_ev._dashboard_discover_topologies",
                return_value=[self._topology(), self._topology()],
            ),
            patch(
                "custom_components.smappee_ev._prepare_topology",
                side_effect=[
                    (stations, mqtt_client),
                    ConfigEntryAuthFailed("dashboard auth failed"),
                ],
            ),
            patch(
                "custom_components.smappee_ev._async_shutdown_runtime_resources",
                new_callable=AsyncMock,
            ) as shutdown,
            pytest.raises(ConfigEntryAuthFailed, match="dashboard auth failed"),
        ):
            await async_setup_entry(hass, mock_config_entry)

        shutdown.assert_awaited_once()
        temp_runtime = shutdown.await_args.args[0]
        assert isinstance(temp_runtime, RuntimeData)
        assert temp_runtime.sites[12345].stations == stations
        assert temp_runtime.mqtt[12345] == mqtt_client
        assert mock_config_entry.runtime_data is None

    @pytest.mark.asyncio
    async def test_async_unload_entry(self, hass, mock_config_entry):
        """Test config entry unloading."""
        # Create runtime data with MQTT clients and coordinators
        mqtt_client = MagicMock()
        mqtt_client.stop = MagicMock(return_value=None)

        coordinator = MagicMock()
        coordinator.async_shutdown = AsyncMock()

        sites = {
            12345: make_site_runtime(
                stations={
                    "station1_uuid": make_station_runtime(
                        station_uuid="station1_uuid",
                        coordinator=coordinator,
                    )
                }
            )
        }

        runtime = RuntimeData(api=MagicMock(), sites=sites, mqtt={12345: mqtt_client})

        # Attach runtime data to config entry
        mock_config_entry.runtime_data = runtime

        # Setup registered service sentinel
        hass.services.async_register(DOMAIN, "start_charging", MagicMock())

        with (
            patch.object(
                hass.config_entries, "async_unload_platforms", return_value=True
            ) as mock_unload,
        ):
            # Call unload
            result = await async_unload_entry(hass, mock_config_entry)

            # Verify unload was successful
            assert result is True

            # Verify MQTT client was stopped
            mqtt_client.stop.assert_called_once()

            # Verify coordinator was shut down
            coordinator.async_shutdown.assert_called_once()

            # Verify platforms were unloaded
            mock_unload.assert_called_once()

            # Leave ConfigEntry.runtime_data lifecycle to Home Assistant
            assert mock_config_entry.runtime_data is runtime

            # Services remain registered domain-wide across entry unload/reload cycles
            assert hass.services.has_service(DOMAIN, "start_charging")

            # Runtime data lives on ConfigEntry, not hass.data
            assert DOMAIN not in hass.data

    @pytest.mark.asyncio
    async def test_async_unload_entry_keeps_services_registered(self, hass, mock_config_entry):
        """Test services stay registered after unloading an entry."""
        mqtt_client = MagicMock()
        mqtt_client.stop = MagicMock(return_value=None)
        coordinator = MagicMock()
        coordinator.async_shutdown = AsyncMock()
        mock_config_entry.runtime_data = RuntimeData(
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
            mqtt={12345: mqtt_client},
        )
        hass.services.async_register(DOMAIN, "start_charging", MagicMock())

        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            assert await async_unload_entry(hass, mock_config_entry) is True

        mqtt_client.stop.assert_called_once()
        coordinator.async_shutdown.assert_awaited_once()
        assert hass.services.has_service(DOMAIN, "start_charging")

    @pytest.mark.asyncio
    async def test_remove_config_entry_device_rejects_current_station(
        self, hass, mock_config_entry
    ):
        """Test current station devices cannot be removed from the registry."""
        mock_config_entry.runtime_data = RuntimeData(
            api=MagicMock(),
            sites={
                12345: make_site_runtime(
                    stations={
                        "station1_uuid": make_station_runtime(
                            station_uuid="station1_uuid",
                            serial="STATION1",
                            station_client=MagicMock(),
                        )
                    }
                )
            },
            mqtt={},
        )
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "12345:STATION1:station1_uuid")}

        assert (
            await async_remove_config_entry_device(hass, mock_config_entry, device_entry) is False
        )

    @pytest.mark.asyncio
    async def test_remove_config_entry_device_rejects_current_modern_station_identifier(
        self, hass, mock_config_entry
    ):
        """Test modern station identifiers are treated as current registry devices."""
        mock_config_entry.runtime_data = RuntimeData(
            api=MagicMock(),
            sites={
                12345: make_site_runtime(
                    stations={
                        "station1_uuid": make_station_runtime(
                            station_uuid="station1_uuid",
                            serial="STATION1",
                            control_location_id=67890,
                            station_client=MagicMock(),
                        )
                    }
                )
            },
            mqtt={},
        )
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "station:12345:67890:STATION1")}

        assert (
            await async_remove_config_entry_device(hass, mock_config_entry, device_entry) is False
        )

    @pytest.mark.asyncio
    async def test_remove_config_entry_device_allows_stale_station(self, hass, mock_config_entry):
        """Test stale station devices can be removed from the registry."""
        mock_config_entry.runtime_data = RuntimeData(
            api=MagicMock(),
            sites={
                12345: make_site_runtime(
                    stations={
                        "station1_uuid": make_station_runtime(
                            station_uuid="station1_uuid",
                            serial="STATION1",
                            station_client=MagicMock(),
                        )
                    }
                )
            },
            mqtt={},
        )
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "12345:OLD_STATION:old_station_uuid")}

        assert await async_remove_config_entry_device(hass, mock_config_entry, device_entry) is True

    @pytest.mark.asyncio
    async def test_remove_config_entry_device_rejects_without_runtime_data(
        self, hass, mock_config_entry
    ):
        """Test device removal is rejected when current devices cannot be determined."""
        mock_config_entry.runtime_data = None
        device_entry = MagicMock()
        device_entry.identifiers = {(DOMAIN, "12345:STATION1:station1_uuid")}

        assert (
            await async_remove_config_entry_device(hass, mock_config_entry, device_entry) is False
        )
