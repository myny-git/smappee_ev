"""Test the Smappee EV diagnostics."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.smappee_ev.data import ConnectorState, IntegrationData, StationState
from custom_components.smappee_ev.diagnostics import REDACT_KEYS, async_get_config_entry_diagnostics
from tests.factories import make_config_entry, make_runtime_data, make_site, make_station_bucket


@pytest.fixture
def mock_integration():
    """Create mock integration."""
    integration = MagicMock()
    integration.version = "1.0.0"
    return integration


@pytest.fixture
def mock_runtime_data():
    """Create mock runtime data with sites, mqtt, etc."""
    runtime = make_runtime_data(
        api=None,
        sites={
            12345: make_site(
                stations={
                    "station-uuid-1": make_station_bucket(
                        coordinator=MagicMock(),
                        station_client=MagicMock(serial_id="STATION001"),
                        connector_clients={
                            "connector-uuid-1": MagicMock(),
                            "connector-uuid-2": MagicMock(),
                        },
                    )
                }
            )
        },
        mqtt={12345: MagicMock()},
    )

    # Configure coordinator data
    station_data = MagicMock()
    station_data.mqtt_connected = True
    station_data.led_brightness = 50
    station_data.grid_power_total = 1000
    station_data.pv_power_total = 2000
    station_data.house_consumption_power = 500
    station_data.last_mqtt_rx = 1634567890

    connector1_data = MagicMock()
    connector1_data.connector_number = 1
    connector1_data.available = True
    connector1_data.session_state = "AVAILABLE"
    connector1_data.power_total = 0

    connector2_data = MagicMock()
    connector2_data.connector_number = 2
    connector2_data.available = True
    connector2_data.session_state = "CHARGING"
    connector2_data.power_total = 7200

    coordinator_data = MagicMock()
    coordinator_data.station = station_data
    coordinator_data.connectors = {
        "connector-uuid-1": connector1_data,
        "connector-uuid-2": connector2_data,
    }

    runtime.sites[12345]["stations"]["station-uuid-1"]["coordinator"].data = coordinator_data

    return runtime


@pytest.fixture
def mock_config_entry(mock_runtime_data):
    """Create mock config entry with runtime data."""
    entry = make_config_entry(runtime_data=mock_runtime_data)
    entry.data = {
        "username": "test_user",
        "password": "test_password",
        "client_id": "client123",
        "client_secret": "secret456",
        "access_token": "token789",
        "refresh_token": "refresh123",
    }
    entry.options = {
        "client_id": "client123",
        "client_secret": "secret456",
    }
    entry.entry_id = "entry_id_123"
    entry.title = "Smappee EV — test_user"
    entry.domain = "smappee_ev"

    # Add state attribute
    state = MagicMock()
    state.name = "loaded"
    entry.state = state

    return entry


class TestDiagnostics:
    """Test the diagnostics functions."""

    @pytest.mark.asyncio
    async def test_async_get_config_entry_diagnostics(
        self, hass, mock_config_entry, mock_integration
    ):
        """Test diagnostics data is properly generated and sensitive data is redacted."""
        with patch("homeassistant.loader.async_get_integration", return_value=mock_integration):
            diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        # Verify structure
        assert "sites" in diagnostics
        assert "config_entry_data" in diagnostics
        assert "options" in diagnostics
        assert "meta" in diagnostics
        assert "sites_detail" in diagnostics
        assert "stations" in diagnostics
        assert "connectors" in diagnostics
        assert "summary" in diagnostics
        assert "dashboard" in diagnostics

        # Check sites list
        assert diagnostics["sites"] == [12345]

        # Verify sensitive data is redacted
        for key in REDACT_KEYS:
            if key in mock_config_entry.data:
                assert diagnostics["config_entry_data"][key] != mock_config_entry.data[key]
                assert "**REDACTED**" in diagnostics["config_entry_data"][key]

            if key in mock_config_entry.options:
                assert diagnostics["options"][key] != mock_config_entry.options[key]
                assert "**REDACTED**" in diagnostics["options"][key]

        # Check meta
        assert diagnostics["meta"]["entry_id"] == "entry_id_123"
        assert diagnostics["meta"]["title"] == "Smappee EV — **REDACTED**"
        assert diagnostics["meta"]["state"] == "loaded"
        assert diagnostics["meta"]["domain"] == "smappee_ev"
        assert diagnostics["meta"]["service_locations_total"] == 1
        assert diagnostics["meta"]["stations_total"] == 1
        assert diagnostics["meta"]["connectors_total"] == 2
        assert diagnostics["meta"]["connector_states_total"] == 2
        # In the test, the version_manifest is None because we're mocking the loader.async_get_integration call
        # but not actually returning the version in the diagnostics test
        assert "version_manifest" in diagnostics["meta"]
        assert diagnostics["summary"]["service_location_ids_count"] == 1
        assert diagnostics["summary"]["carcharger_clients_count"] == 2
        assert diagnostics["dashboard"]["configured"] is False

        # Check site details
        assert len(diagnostics["sites_detail"]) == 1
        site_detail = diagnostics["sites_detail"][0]
        assert site_detail["service_location_id"] == 12345
        assert site_detail["name_present"] is True
        assert site_detail["uuid"] == "test...uuid"
        assert site_detail["serial"] == "SERI...L123"
        assert site_detail["station_count"] == 1
        assert site_detail["connector_count"] == 2
        assert site_detail["mqtt_configured"] is True
        assert site_detail["mqtt_connected_any"] is True
        assert site_detail["mqtt"]["configured"] is True

        # Check stations
        assert len(diagnostics["stations"]) == 1
        station = diagnostics["stations"][0]
        assert station["station_uuid"] == "stat...id-1"
        assert station["station_handle"]["serial_id"] == "STAT...N001"
        assert station["available"] is not None
        assert station["led_brightness"] == 50
        assert station["grid_power_total"] == 1000
        assert station["pv_power_total"] == 2000
        assert station["house_consumption_power"] == 500
        assert station["mqtt_connected"] is True
        assert station["connector_client_count"] == 2
        assert station["connector_state_count"] == 2

        # Check connectors
        assert len(diagnostics["connectors"]) == 2

        # At least one connector should be in charging state
        charging_connectors = [
            c for c in diagnostics["connectors"] if c["session_state"] == "CHARGING"
        ]
        assert len(charging_connectors) >= 1
        charging = charging_connectors[0]
        assert charging["connector_uuid"] == "conn...id-2"
        assert charging["has_state"] is True
        assert charging["power_total"] == 7200

        # At least one connector should be available
        available_connectors = [
            c for c in diagnostics["connectors"] if c["session_state"] == "AVAILABLE"
        ]
        assert len(available_connectors) >= 1
        available = available_connectors[0]
        assert available["power_total"] == 0

    @pytest.mark.asyncio
    async def test_diagnostics_with_empty_runtime(self, hass):
        """Test diagnostics with empty or missing runtime data."""
        entry = make_config_entry(runtime_data=None)
        entry.data = {"username": "test_user"}
        entry.options = {}
        entry.entry_id = "entry_id_123"
        entry.title = "Smappee EV — test_user"
        entry.domain = "smappee_ev"

        # Add state attribute
        state = MagicMock()
        state.name = "loaded"
        entry.state = state

        with patch(
            "homeassistant.loader.async_get_integration", side_effect=Exception("Test error")
        ):
            diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        # Verify it handles missing runtime data gracefully
        assert diagnostics["sites"] == []
        assert len(diagnostics["sites_detail"]) == 0
        assert len(diagnostics["stations"]) == 0
        assert len(diagnostics["connectors"]) == 0
        assert diagnostics["meta"]["stations_total"] == 0
        assert diagnostics["meta"]["connectors_total"] == 0
        assert diagnostics["summary"]["service_location_ids_count"] == 0
        assert diagnostics["meta"]["version_manifest"] is None

    @pytest.mark.asyncio
    async def test_diagnostics_with_partial_data(self, hass, mock_runtime_data):
        """Test diagnostics with partial station/connector data."""
        entry = make_config_entry()

        # Create runtime with incomplete data
        runtime = mock_runtime_data
        # Remove coordinator data
        station = runtime.sites[12345]["stations"]["station-uuid-1"]
        station["coordinator"].data = None

        entry.runtime_data = runtime
        entry.data = {"username": "test_user"}
        entry.options = {}
        entry.entry_id = "entry_id_123"
        entry.title = "Smappee EV — test_user"
        entry.domain = "smappee_ev"
        entry.state = None  # Test with missing state

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        # Should keep topology details even when coordinator data is missing.
        assert len(diagnostics["stations"]) == 1
        assert len(diagnostics["connectors"]) == 2
        assert diagnostics["meta"]["stations_total"] == 1
        assert diagnostics["meta"]["connectors_total"] == 2
        assert diagnostics["meta"]["connector_states_total"] == 0
        assert diagnostics["meta"]["state"] is None

    @pytest.mark.asyncio
    async def test_diagnostics_never_leaks_sensitive_entry_or_runtime_values(self, hass):
        """Catch regressions where diagnostics expose credentials, serials, or UUIDs."""
        secrets = {
            "username": "USER_SECRET_ALPHA_1234",
            "password": "PASS_SECRET_BRAVO_5678",
            "dashboard_refresh_token": "REFRESH_SECRET_CHARLIE_9012",
            "site_serial_number": "SERIAL_SECRET_DELTA_3456",
            "serviceLocationUuid": "SITE_UUID_SECRET_ECHO_7890",
            "station_uuid": "STATION_UUID_SECRET_FOXTROT_2468",
            "connector_uuid": "CONNECTOR_UUID_SECRET_GOLF_1357",
        }

        station_client = SimpleNamespace(
            service_location_id=12345,
            serial=secrets["site_serial_number"],
            serial_id=secrets["site_serial_number"],
            charging_station_serial=secrets["site_serial_number"],
            smart_device_uuid=secrets["station_uuid"],
            smart_device_id=None,
            dashboard_device_id=None,
            connector_number=None,
            is_station=True,
        )
        connector_client = SimpleNamespace(
            service_location_id=12345,
            serial=secrets["site_serial_number"],
            serial_id=None,
            charging_station_serial=secrets["site_serial_number"],
            smart_device_uuid=secrets["connector_uuid"],
            smart_device_id=None,
            dashboard_device_id=None,
            connector_number=1,
            is_station=False,
        )
        coordinator = SimpleNamespace(
            data=IntegrationData(
                station=StationState(mqtt_connected=True),
                connectors={
                    secrets["connector_uuid"]: ConnectorState(
                        connector_number=1,
                        dashboard_device_uuid=secrets["connector_uuid"],
                    )
                },
            )
        )
        mqtt_client = SimpleNamespace(
            _slu=secrets["serviceLocationUuid"],
            _slu_id=12345,
            _client_id=secrets["username"],
            _serial=secrets["site_serial_number"],
            _slus=(secrets["serviceLocationUuid"],),
            _mqtt_specs=(),
        )
        runtime = make_runtime_data(
            api=SimpleNamespace(
                username=secrets["username"],
                password=secrets["password"],
                refresh_token=secrets["dashboard_refresh_token"],
                _token="ACCESS_SECRET_HOTEL_8642",  # noqa: S106 - fake diagnostics token
                _token_expires_at_ms=123,
            ),
            sites={
                12345: {
                    "name": "Sensitive Site",
                    "serviceLocationUuid": secrets["serviceLocationUuid"],
                    "deviceSerialNumber": secrets["site_serial_number"],
                    "stations": {
                        secrets["station_uuid"]: {
                            "coordinator": coordinator,
                            "station_client": station_client,
                            "connector_clients": {
                                secrets["connector_uuid"]: connector_client,
                            },
                        }
                    },
                }
            },
            mqtt={12345: mqtt_client},
        )
        entry = make_config_entry(runtime_data=runtime)
        entry.data = {
            "username": secrets["username"],
            "password": secrets["password"],
            "dashboard_refresh_token": secrets["dashboard_refresh_token"],
            "site_serial_number": secrets["site_serial_number"],
            "serviceLocationUuid": secrets["serviceLocationUuid"],
            "station_uuid": secrets["station_uuid"],
            "connector_uuid": secrets["connector_uuid"],
        }
        entry.options = {
            "refresh_token": "OPTIONS_REFRESH_SECRET_INDIA_9753",
            "smart_device_uuid": secrets["connector_uuid"],
        }
        entry.entry_id = "entry_id_123"
        entry.title = f"Smappee EV — {secrets['username']}"
        entry.domain = "smappee_ev"
        entry.state = None

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        serialized = json.dumps(diagnostics, sort_keys=True)
        for secret in (*secrets.values(), "OPTIONS_REFRESH_SECRET_INDIA_9753"):
            assert secret not in serialized
        assert diagnostics["config_entry_data"]["username"] == "**REDACTED**"
        assert diagnostics["config_entry_data"]["site_serial_number"] == "**REDACTED**"
        assert diagnostics["options"]["smart_device_uuid"] == "**REDACTED**"
        assert diagnostics["meta"]["title"] == "Smappee EV — **REDACTED**"
        assert diagnostics["sites_detail"][0]["uuid"] == "SITE...7890"
        assert diagnostics["sites_detail"][0]["serial"] == "SERI...3456"
        assert diagnostics["stations"][0]["station_uuid"] == "STAT...2468"
        assert diagnostics["connectors"][0]["connector_uuid"] == "CONN...1357"
