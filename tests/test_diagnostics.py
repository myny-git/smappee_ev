"""Test the Smappee EV diagnostics."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.smappee_ev.coordinators.power import PowerMixin
from custom_components.smappee_ev.diagnostics import (
    REDACT_KEYS,
    _dashboard_info,
    _handle_info,
    _mqtt_client_count,
    _mqtt_info,
    _obfuscate,
    _power_mapping_info,
    _redact_nested_text_values,
    _redact_text_values,
    _safe_len,
    _safe_sorted,
    async_get_config_entry_diagnostics,
)
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState
from tests.factories import (
    make_config_entry,
    make_connector_runtime,
    make_runtime_data,
    make_site_runtime,
    make_station_runtime,
)


def test_diagnostics_small_helpers_are_stable_and_defensive():
    assert _obfuscate(None) is None
    assert _obfuscate("") is None
    assert _obfuscate("A") == "***"
    assert _obfuscate("AB") == "A***B"
    assert _safe_len(123) == 0
    assert _safe_len([1, 2, 3]) == 3
    assert _safe_sorted({"b", "a"}) == ["a", "b"]
    assert _safe_sorted("not-a-container-list") == []

    assert _redact_text_values(123, ["secret"]) == 123
    assert _redact_nested_text_values(
        ("secret", {"nested": "keep secret"}),
        ["secret"],
    ) == ["**REDACTED**", {"nested": "keep **REDACTED**"}]

    assert _handle_info(None) == {}
    assert _mqtt_info(None) == {"configured": False}
    assert _mqtt_client_count(None) == 0
    assert _mqtt_client_count({1: [object(), object()], 2: object(), 3: None}) == 3


def test_diagnostics_mqtt_and_dashboard_helpers_cover_fallback_shapes():
    mqtt_one = SimpleNamespace(
        _slu="SITE_UUID_123456",
        _slu_id=123,
        _client_id="CLIENT_ID_123456",
        _serial="SERIAL_123456",
        _slus=("SITE_UUID_123456",),
        _mqtt_specs=(
            SimpleNamespace(
                service_location_id=123,
                role="grid",
                metric="activePower",
                topic="servicelocation/SITE_UUID_123456/power",
                username="user",
                password="pass",  # noqa: S106 - fake diagnostics password
                aspect_paths=("path/SITE_UUID_123456",),
            ),
        ),
    )
    mqtt_two = SimpleNamespace(_mqtt_specs=())

    info = _mqtt_info([mqtt_one, mqtt_two], ["SITE_UUID_123456"])

    assert info["configured"] is True
    assert info["client_count"] == 2
    assert info["clients"][0]["spec_count"] == 1
    assert info["clients"][0]["specs"][0]["aspect_paths"] == ["path/**REDACTED**"]

    assert _dashboard_info(None) == {"configured": False}
    assert _dashboard_info(SimpleNamespace(api=None)) == {"configured": False}
    dashboard = SimpleNamespace(
        username="user",
        password=None,
        refresh_token="refresh",  # noqa: S106 - fake diagnostics token
        _token="token",  # noqa: S106 - fake diagnostics token
        _token_expires_at_ms=123,
    )
    assert _dashboard_info(SimpleNamespace(dashboard=dashboard)) == {
        "configured": True,
        "client_type": "SimpleNamespace",
        "username_present": True,
        "password_present": False,
        "refresh_token_present": True,
        "access_token_present": True,
        "token_expires_at_present": True,
    }


@pytest.fixture
def mock_integration():
    """Create mock integration."""
    integration = MagicMock()
    integration.version = "1.0.0"
    return integration


@pytest.fixture
def mock_runtime_data():
    """Create mock runtime data with sites, mqtt, etc."""
    coordinator = MagicMock()
    runtime = make_runtime_data(
        api=None,
        sites={
            12345: make_site_runtime(
                site_location_id=12345,
                site_uuid="test-uuid",
                gateway_serial="SERIAL123",
                stations={
                    "station-uuid-1": make_station_runtime(
                        site_location_id=12345,
                        control_location_id=12345,
                        station_uuid="station-uuid-1",
                        coordinator=coordinator,
                        station_client=MagicMock(serial_id="STATION001"),
                        connectors={
                            "connector-uuid-1": make_connector_runtime(
                                connector_key="connector-uuid-1",
                                connector_uuid="connector-uuid-1",
                                connector_position=1,
                                connector_client=MagicMock(),
                            ),
                            "connector-uuid-2": make_connector_runtime(
                                connector_key="connector-uuid-2",
                                connector_uuid="connector-uuid-2",
                                connector_position=2,
                                connector_client=MagicMock(),
                            ),
                        },
                    )
                },
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

    coordinator.data = coordinator_data

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
        # A plain MagicMock coordinator (no real _highlevel_configs/connector_clients)
        # must degrade to safe defaults instead of raising.
        assert station["power_mapping"]["available"] is True
        assert station["power_mapping"]["mapping_cache_state"] == "not_initialized"
        assert station["power_mapping"]["measurements"] == []

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
        station = runtime.sites[12345].stations["station-uuid-1"]
        station.station_coordinator.data = None

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
            _mqtt_specs=(
                SimpleNamespace(
                    service_location_id=12345,
                    role="grid",
                    metric="activePower",
                    topic=f"servicelocation/{secrets['serviceLocationUuid']}/power",
                    username=secrets["username"],
                    password=secrets["password"],
                    aspect_paths=[
                        f"station/{secrets['station_uuid']}",
                        {"connector": f"device/{secrets['connector_uuid']}"},
                    ],
                ),
            ),
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
                12345: make_site_runtime(
                    site_location_id=12345,
                    site_name="Sensitive Site",
                    site_uuid=secrets["serviceLocationUuid"],
                    gateway_serial=secrets["site_serial_number"],
                    stations={
                        secrets["station_uuid"]: make_station_runtime(
                            site_location_id=12345,
                            control_location_id=12345,
                            station_uuid=secrets["station_uuid"],
                            serial=secrets["site_serial_number"],
                            coordinator=coordinator,
                            station_client=station_client,
                            connectors={
                                secrets["connector_uuid"]: make_connector_runtime(
                                    connector_key=secrets["connector_uuid"],
                                    connector_uuid=secrets["connector_uuid"],
                                    connector_position=1,
                                    connector_client=connector_client,
                                )
                            },
                        )
                    },
                )
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
        spec = diagnostics["sites_detail"][0]["mqtt"]["specs"][0]
        assert spec["username_present"] is True
        assert spec["password_present"] is True
        assert spec["aspect_paths"] == [
            "station/**REDACTED**",
            {"connector": "device/**REDACTED**"},
        ]

    @pytest.mark.asyncio
    async def test_diagnostics_redacts_multi_site_runtime(self, hass):
        """Keep redaction intact for multi-site runtimes."""
        site_a_uuid = "SITE_A_UUID_SECRET_1111"
        site_b_uuid = "SITE_B_UUID_SECRET_2222"
        station_a_uuid = "STATION_A_UUID_SECRET_3333"
        station_b_uuid = "STATION_B_UUID_SECRET_4444"
        connector_a_uuid = "CONNECTOR_A_UUID_SECRET_5555"
        connector_b_uuid = "CONNECTOR_B_UUID_SECRET_6666"
        serial_a = "SERIAL_A_SECRET_7777"
        serial_b = "SERIAL_B_SECRET_8888"

        def _site_runtime(
            *,
            service_location_id: int,
            site_uuid: str,
            station_uuid: str,
            connector_uuid: str,
            serial: str,
        ):
            station_client = SimpleNamespace(
                service_location_id=service_location_id,
                serial=serial,
                serial_id=serial,
                charging_station_serial=serial,
                smart_device_uuid=station_uuid,
                smart_device_id=f"STATION_DEVICE_{service_location_id}",
                dashboard_device_id=f"STATION_DASH_{service_location_id}",
                connector_number=None,
                is_station=True,
            )
            connector_client = SimpleNamespace(
                service_location_id=service_location_id,
                serial=serial,
                serial_id=serial,
                charging_station_serial=serial,
                smart_device_uuid=connector_uuid,
                smart_device_id=f"CONNECTOR_DEVICE_{service_location_id}",
                dashboard_device_id=f"CONNECTOR_DASH_{service_location_id}",
                connector_number=1,
                is_station=False,
            )
            coordinator = SimpleNamespace(
                data=IntegrationData(
                    station=StationState(mqtt_connected=True),
                    connectors={
                        connector_uuid: ConnectorState(
                            connector_number=1,
                            dashboard_device_id=f"CONNECTOR_DASH_{service_location_id}",
                            dashboard_device_uuid=connector_uuid,
                        )
                    },
                )
            )
            return make_site_runtime(
                site_location_id=service_location_id,
                site_uuid=site_uuid,
                gateway_serial=serial,
                stations={
                    station_uuid: make_station_runtime(
                        site_location_id=service_location_id,
                        control_location_id=service_location_id,
                        station_uuid=station_uuid,
                        serial=serial,
                        coordinator=coordinator,
                        station_client=station_client,
                        connectors={
                            connector_uuid: make_connector_runtime(
                                connector_key=connector_uuid,
                                connector_uuid=connector_uuid,
                                connector_position=1,
                                connector_client=connector_client,
                            )
                        },
                    )
                },
            )

        mqtt_a = SimpleNamespace(
            _slu=site_a_uuid,
            _slu_id=11111,
            _client_id="CLIENT_A_SECRET_9999",
            _serial=serial_a,
            _slus=(site_a_uuid,),
            _mqtt_specs=(),
        )
        mqtt_b = SimpleNamespace(
            _slu=site_b_uuid,
            _slu_id=22222,
            _client_id="CLIENT_B_SECRET_0000",
            _serial=serial_b,
            _slus=(site_b_uuid,),
            _mqtt_specs=(),
        )
        runtime = make_runtime_data(
            sites={
                11111: _site_runtime(
                    service_location_id=11111,
                    site_uuid=site_a_uuid,
                    station_uuid=station_a_uuid,
                    connector_uuid=connector_a_uuid,
                    serial=serial_a,
                ),
                22222: _site_runtime(
                    service_location_id=22222,
                    site_uuid=site_b_uuid,
                    station_uuid=station_b_uuid,
                    connector_uuid=connector_b_uuid,
                    serial=serial_b,
                ),
            },
            mqtt={11111: mqtt_a, 22222: mqtt_b},
        )
        entry = make_config_entry(runtime_data=runtime)
        entry.data = {"username": "USER_MULTI_SECRET_1234"}
        entry.options = {}
        entry.title = "Smappee EV"

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        assert diagnostics["summary"]["service_location_ids_count"] == 2
        assert diagnostics["meta"]["mqtt_clients_total"] == 2
        assert [site["mqtt_configured"] for site in diagnostics["sites_detail"]] == [True, True]
        serialized = json.dumps(diagnostics, sort_keys=True)
        for secret in (
            site_a_uuid,
            site_b_uuid,
            station_a_uuid,
            station_b_uuid,
            connector_a_uuid,
            connector_b_uuid,
            serial_a,
            serial_b,
            "CLIENT_A_SECRET_9999",
            "CLIENT_B_SECRET_0000",
        ):
            assert secret not in serialized

    @pytest.mark.asyncio
    async def test_diagnostics_redacts_dataclass_runtime(self, hass):
        """Diagnostics should read and redact typed runtime containers."""
        site_uuid = "SITE_DATACLASS_SECRET_1111"
        station_uuid = "STATION_DATACLASS_SECRET_2222"
        connector_uuid = "CONNECTOR_DATACLASS_SECRET_3333"
        serial = "SERIAL_DATACLASS_SECRET_4444"
        connector_client = SimpleNamespace(
            service_location_id=44444,
            serial=serial,
            serial_id=serial,
            charging_station_serial=serial,
            smart_device_uuid=connector_uuid,
            smart_device_id="CONNECTOR_DEVICE_DATACLASS_SECRET_5555",
            dashboard_device_id="CONNECTOR_DASH_DATACLASS_SECRET_6666",
            connector_number=1,
            is_station=False,
        )
        station_client = SimpleNamespace(
            service_location_id=44444,
            serial=serial,
            serial_id=serial,
            charging_station_serial=serial,
            smart_device_uuid=station_uuid,
            smart_device_id="STATION_DEVICE_DATACLASS_SECRET_7777",
            dashboard_device_id="STATION_DASH_DATACLASS_SECRET_8888",
            connector_number=None,
            is_station=True,
        )
        coordinator = SimpleNamespace(
            data=IntegrationData(
                station=StationState(mqtt_connected=True),
                connectors={
                    connector_uuid: ConnectorState(
                        connector_number=1,
                        dashboard_device_id="CONNECTOR_DASH_DATACLASS_SECRET_6666",
                        dashboard_device_uuid=connector_uuid,
                    )
                },
            )
        )
        mqtt_client = SimpleNamespace(
            _slu=site_uuid,
            _slu_id=44444,
            _client_id="CLIENT_DATACLASS_SECRET_9999",
            _serial=serial,
            _slus=(site_uuid,),
            _mqtt_specs=(),
        )
        runtime = make_runtime_data(
            api=None,
            mqtt={44444: mqtt_client},
            sites={
                44444: make_site_runtime(
                    site_location_id=44444,
                    site_name="Dataclass Site",
                    site_function_type="SERVICELOCATION",
                    site_uuid=site_uuid,
                    gateway_serial=serial,
                    gateway_type="Infinity",
                    measurement_location_ids=[44444],
                    mqtt_clients=mqtt_client,
                    stations={
                        station_uuid: make_station_runtime(
                            site_location_id=44444,
                            control_location_id=44445,
                            station_uuid=station_uuid,
                            serial=serial,
                            station_client=station_client,
                            coordinator=coordinator,
                            connectors={
                                connector_uuid: make_connector_runtime(
                                    connector_key=connector_uuid,
                                    connector_uuid=connector_uuid,
                                    connector_position=1,
                                    connector_client=connector_client,
                                )
                            },
                        )
                    },
                )
            },
        )
        entry = make_config_entry(runtime_data=runtime)
        entry.data = {}
        entry.options = {}
        entry.title = "Smappee EV"

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        assert diagnostics["meta"]["service_locations_total"] == 1
        assert diagnostics["meta"]["stations_total"] == 1
        assert diagnostics["meta"]["connectors_total"] == 1
        assert diagnostics["sites_detail"][0]["uuid"] == "SITE...1111"
        assert diagnostics["sites_detail"][0]["serial"] == "SERI...4444"
        assert diagnostics["stations"][0]["station_uuid"] == "STAT...2222"
        assert diagnostics["connectors"][0]["connector_uuid"] == "CONN...3333"
        serialized = json.dumps(diagnostics, sort_keys=True)
        for secret in (
            site_uuid,
            station_uuid,
            connector_uuid,
            serial,
            "CLIENT_DATACLASS_SECRET_9999",
            "CONNECTOR_DASH_DATACLASS_SECRET_6666",
        ):
            assert secret not in serialized


class _FakePowerCoord:
    """Minimal double exposing the *real* PowerMixin resolver methods.

    Reusing the production `_connector_uuid_for_highlevel_measurement` and
    `_connector_position_from_measurement` implementations (instead of
    re-mocking their behaviour) keeps this regression test honest: it fails
    if that resolver logic itself ever changes in a way that would affect
    diagnostics output.
    """

    _connector_position_from_measurement = staticmethod(
        PowerMixin._connector_position_from_measurement
    )
    _connector_uuid_for_highlevel_measurement = PowerMixin._connector_uuid_for_highlevel_measurement

    def __init__(self, connector_clients, highlevel_configs, power_index_maps_by_topic):
        self.connector_clients = connector_clients
        self._highlevel_configs = highlevel_configs
        self._power_index_maps_by_topic = power_index_maps_by_topic


def test_power_mapping_info_reveals_discovery_power_classification_mismatch():
    """Regression test for #251.

    A car-charger measurement whose `category` is not "CAR_CHARGER" but whose
    `appliance.type` is must still show up as a car-charger measurement in
    diagnostics (`discovery_classification`), even though the current
    `coordinators.power` inline classification (`power_classification`) skips
    it - proving the MQTT power index map is never populated for this
    measurement because of the classification precedence mismatch, not
    because the connector-uuid resolver fails.
    """
    connector_clients = {"connector-uuid-1": SimpleNamespace(connector_number=1)}
    measurement = {
        "type": "APPLIANCE",
        "category": "ELECTRICITY",
        "name": "Wallbox",
        "appliance": {"type": "CAR_CHARGER", "uuid": "connector-uuid-1"},
    }
    coord = _FakePowerCoord(
        connector_clients=connector_clients,
        highlevel_configs={12345: {"measurements": [measurement]}},
        power_index_maps_by_topic=None,
    )

    info = _power_mapping_info(coord)

    assert info["available"] is True
    assert info["mapping_cache_state"] == "not_initialized"
    assert info["known_connector_count"] == 1
    assert info["known_connectors"] == [
        {"alias": "connector_1", "connector_number": 1, "connector_uuid": "conn...id-1"}
    ]
    assert info["car_charger_measurement_count"] == 1

    (meas_out,) = info["measurements"]
    assert meas_out["discovery_classification"] == "car_charger"
    assert meas_out["power_classification"] is None
    assert meas_out["would_enter_power_car_branch"] is False
    # The resolver itself works fine - it is never reached in production
    # because `power_classification` is None for this measurement.
    assert meas_out["resolved"] is True
    assert meas_out["resolved_connector"] == "connector_1"
    assert meas_out["name_present"] is True

    # Privacy: no raw name, uuid, or free-text identifier ever leaks.
    serialized = json.dumps(info, sort_keys=True)
    assert "Wallbox" not in serialized
    assert "connector-uuid-1" not in serialized


def test_power_mapping_info_mapping_cache_tri_state():
    """`mapping_cache_state` must distinguish not-yet-built from built-but-empty."""
    assert _power_mapping_info(None) == {"available": False}

    not_initialized = _FakePowerCoord({}, {}, None)
    assert _power_mapping_info(not_initialized)["mapping_cache_state"] == "not_initialized"

    empty = _FakePowerCoord({}, {}, {})
    empty_info = _power_mapping_info(empty)
    assert empty_info["mapping_cache_state"] == "empty"
    assert empty_info["car_mapping_count"] == 0

    mapped = _FakePowerCoord(
        connector_clients={"connector-uuid-1": SimpleNamespace(connector_number=1)},
        highlevel_configs={},
        power_index_maps_by_topic={
            "servicelocation/SITE_UUID_123456/power": {
                "grid": {"power": [0], "current": [], "cons": [], "energy": []},
                "pv": {"power": [], "current": [], "cons": [], "energy": []},
                "cars": {"connector-uuid-1": {"position": 1, "serial": None}},
            }
        },
    )
    mapped_info = _power_mapping_info(mapped)
    assert mapped_info["mapping_cache_state"] == "mapped"
    assert mapped_info["car_mapping_count"] == 1
    (topic,) = mapped_info["topics"]
    assert topic["grid_present"] is True
    assert topic["pv_present"] is False
    assert topic["car_mapping_count"] == 1
    assert topic["mapped_connectors"] == ["connector_1"]
