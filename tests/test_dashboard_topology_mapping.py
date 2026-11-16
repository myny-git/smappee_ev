"""Dashboard discovery: parsing devices and mapping connectors to stations."""

from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import pytest

from custom_components.smappee_ev.api.errors import SmappeeAuthenticationError
from custom_components.smappee_ev.dashboard_discovery import (
    create_dashboard_client,
    dashboard_fetch_devices,
    dashboard_fetch_highlevel_configs,
    fallback_dashboard_connector_mapping,
    fetch_dashboard_connector_mapping,
    load_dashboard_topologies,
)
from custom_components.smappee_ev.topology import (
    _find_in,
    _uuid_from_dashboard_channel,
    device_station_serial,
    fallback_highlevel_connector_mapping,
    is_connector,
    normalize_connector_mapping_station_keys,
    safe_str,
)
from tests.helpers import raises_translated


def _configured_dashboard(**methods):
    dashboard = MagicMock()
    dashboard._token = "token"  # noqa: S105 - fake test token
    dashboard.refresh_token = None
    dashboard.username = None
    dashboard.password = None
    for name, value in methods.items():
        setattr(dashboard, name, value)
    return dashboard


def test_dashboard_device_parsing_helpers_handle_fallback_shapes():
    assert safe_str(None) is None
    assert safe_str(" none ") is None
    assert safe_str(" null ") is None
    assert safe_str(" SERIAL ") == "SERIAL"

    assert is_connector({"carCharger": {}}) is True
    assert is_connector({"type": {"category": "CARCHARGER"}}) is True
    assert is_connector({"type": "CARCHARGER"}) is True
    assert is_connector({"type": "SENSOR"}) is False

    assert (
        _find_in(
            {
                "configurationProperties": [
                    {
                        "spec": {"name": "device.serial.number"},
                        "value": {"value": "SERIAL-FROM-CONFIG"},
                    }
                ]
            },
            "serialNumber",
        )
        == "SERIAL-FROM-CONFIG"
    )
    assert (
        _find_in(
            {
                "properties": [
                    {
                        "spec": {"name": "deviceSerial"},
                        "value": "SERIAL-FROM-PROPS",
                    }
                ]
            },
            "serialNumber",
        )
        == "SERIAL-FROM-PROPS"
    )
    assert _find_in({"configurationProperties": [{"spec": {"name": "other"}}]}) is None


def test_dashboard_channel_uuid_helpers_extract_connector_and_station_ids():
    assert _uuid_from_dashboard_channel({"carCharger": "bad"}) is None
    assert (
        _uuid_from_dashboard_channel(
            {
                "carCharger": {
                    "chargingStateUpdateChannel": "servicelocation/site/devices/connector-uuid/property/state"
                }
            }
        )
        == "connector-uuid"
    )
    assert (
        _uuid_from_dashboard_channel(
            {
                "carCharger": {
                    "chargingStateUpdateChannel": {
                        "name": "servicelocation/site/devices/channel-uuid/property/state"
                    }
                }
            }
        )
        == "channel-uuid"
    )
    assert (
        device_station_serial(
            {
                "serialNumber": " ",
                "uuid": "station-uuid",
            }
        )
        == "station-uuid"
    )


@pytest.mark.asyncio
async def test_dashboard_load_topologies_raises_not_ready_without_charging_stations():
    empty = _configured_dashboard(
        async_get_service_locations_full_details=AsyncMock(return_value=[])
    )
    with raises_translated(ConfigEntryNotReady, "no_charging_stations"):
        await load_dashboard_topologies(empty)


@pytest.mark.asyncio
async def test_dashboard_fetch_helpers_handle_errors_duplicates_and_bad_shapes():
    dashboard = _configured_dashboard(
        async_get_smart_devices=AsyncMock(side_effect=[RuntimeError("boom"), {"bad": "shape"}]),
        async_get_highlevel_configuration=AsyncMock(
            side_effect=[{"ok": 1}, RuntimeError("skip"), {"duplicate": "not called"}]
        ),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await dashboard_fetch_devices(dashboard, 1)
    with pytest.raises(TypeError, match="malformed"):
        await dashboard_fetch_devices(dashboard, 1)
    configs = await dashboard_fetch_highlevel_configs(dashboard, [10, 20, 10])

    assert configs == {10: {"ok": 1}}
    assert dashboard.async_get_highlevel_configuration.await_count == 2


@pytest.mark.asyncio
async def test_optional_dashboard_discovery_never_swallows_authentication_errors():
    auth_error = SmappeeAuthenticationError("reauth required")
    dashboard = _configured_dashboard(
        async_get_charging_station_details=AsyncMock(side_effect=auth_error),
        async_get_highlevel_configuration=AsyncMock(side_effect=auth_error),
    )
    station = {"serialNumber": "STATION-1", "type": "CHARGINGSTATION"}

    with pytest.raises(ConfigEntryAuthFailed, match="reauth required"):
        await fetch_dashboard_connector_mapping(dashboard, [station])
    with pytest.raises(ConfigEntryAuthFailed, match="reauth required"):
        await dashboard_fetch_highlevel_configs(dashboard, [100])

    dashboard.async_get_smart_devices = AsyncMock(side_effect=auth_error)
    with pytest.raises(ConfigEntryAuthFailed, match="reauth required"):
        await dashboard_fetch_devices(dashboard, 100)

    dashboard.async_get_service_locations_full_details = AsyncMock(side_effect=auth_error)
    with pytest.raises(ConfigEntryAuthFailed, match="reauth required"):
        await load_dashboard_topologies(dashboard)


@pytest.mark.asyncio
async def test_fetch_dashboard_connector_mapping_extracts_station_led_and_connectors():
    station = {"serialNumber": "STATION-1", "type": "CHARGINGSTATION"}
    connector_device = {
        "id": "connector-device-id",
        "uuid": "connector-device-uuid",
        "type": {"category": "CARCHARGER"},
        "carCharger": {
            "chargingStateUpdateChannel": {
                "name": "servicelocation/site/etc/devices/connector-channel-uuid/property/chargingstate"
            }
        },
    }
    dashboard = _configured_dashboard(
        async_get_charging_station_details=AsyncMock(
            return_value={
                "name": "Garage Charger",
                "chargingStation": {"model": "EV Wall"},
                "modules": [
                    {
                        "position": 1,
                        "smartDevice": {
                            "id": "led-id",
                            "uuid": "led-uuid",
                            "name": "LED Ring",
                            "type": {"category": "LED"},
                        },
                    },
                    {"position": 2, "smartDevice": connector_device},
                    {"position": 3, "smartDevice": {"type": {"category": "SENSOR"}}},
                ],
            }
        )
    )

    mapping = await fetch_dashboard_connector_mapping(dashboard, [station])

    assert mapping["STATION-1"]["station_model"] == "EV Wall"
    assert mapping["STATION-1"]["station_name"] == "Garage Charger"
    assert mapping["STATION-1"]["led_devices"]["led-id"]["uuid"] == "led-uuid"
    assert mapping["STATION-1"]["connectors"]["connector-channel-uuid"]["position"] == 2


@pytest.mark.asyncio
async def test_fetch_dashboard_connector_mapping_ignores_incomplete_dashboard_shapes():
    dashboard = _configured_dashboard(
        async_get_charging_station_details=AsyncMock(
            side_effect=[
                {"modules": []},
                ["not-a-dict"],
                {
                    "modules": [
                        "not-a-module",
                        {"position": 1},
                        {"position": 2, "smartDevice": {"type": {"category": "LED"}}},
                        {
                            "position": 3,
                            "smartDevice": {
                                "id": "non-connector",
                                "type": {"category": "SENSOR"},
                            },
                        },
                        {
                            "position": 4,
                            "smartDevice": {
                                "id": "connector-without-uuid",
                                "type": {"category": "CARCHARGER"},
                            },
                        },
                    ]
                },
            ]
        )
    )

    mapping = await fetch_dashboard_connector_mapping(
        dashboard,
        [
            {"type": "CHARGINGSTATION"},
            {"serialNumber": "STATION-1", "type": "CHARGINGSTATION"},
            {"serialNumber": "STATION-2", "type": "CHARGINGSTATION"},
            {"serialNumber": "STATION-3", "type": "CHARGINGSTATION"},
        ],
    )

    assert mapping == {"STATION-1": {"connectors": {}}, "STATION-3": {"connectors": {}}}
    assert dashboard.async_get_charging_station_details.await_count == 3


def test_fallback_dashboard_connector_mapping_rejects_ambiguous_or_unserialed_stations():
    connector = {"uuid": "connector-uuid", "id": "connector-id", "type": "CARCHARGER"}

    assert (
        fallback_dashboard_connector_mapping(
            [{"serialNumber": "STATION-1"}, {"serialNumber": "STATION-2"}],
            [connector],
        )
        == {}
    )
    assert fallback_dashboard_connector_mapping([{"type": "CHARGINGSTATION"}], [connector]) == {}


def test_fallback_dashboard_connector_mapping_position_precedence_and_uuid_skip():
    mapping = fallback_dashboard_connector_mapping(
        [{"serialNumber": "STATION-1"}],
        [
            {"id": "missing-uuid", "type": "CARCHARGER"},
            {
                "uuid": "connector-number-wins",
                "id": "connector-1",
                "connectorNumber": 2,
                "position": 1,
                "type": "CARCHARGER",
            },
            {
                "uuid": "position-wins-over-index",
                "id": "connector-2",
                "position": 3,
                "type": "CARCHARGER",
            },
            {
                "uuid": "index-fallback",
                "smartDeviceId": "connector-3",
                "type": "CARCHARGER",
            },
        ],
    )

    connectors = mapping["STATION-1"]["connectors"]
    assert set(connectors) == {
        "connector-number-wins",
        "position-wins-over-index",
        "index-fallback",
    }
    assert connectors["connector-number-wins"]["id"] == "connector-1"
    assert connectors["connector-number-wins"]["position"] == 2
    assert connectors["position-wins-over-index"]["position"] == 3
    assert connectors["index-fallback"]["id"] == "connector-3"
    assert connectors["index-fallback"]["position"] == 4


def test_fallback_highlevel_connector_mapping_uses_measurement_position_and_appliance_uuid():
    mapping = fallback_highlevel_connector_mapping(
        "STATION-1",
        {
            1: {
                "measurements": [
                    {
                        "type": "APPLIANCE",
                        "name": "EV Wall - 2",
                        "appliance": {"type": "CAR_CHARGER", "uuid": "appliance-uuid"},
                    },
                    {"type": "GRID", "appliance": {"type": "CAR_CHARGER", "uuid": "ignored"}},
                ]
            }
        },
    )

    connector = mapping["STATION-1"]["connectors"]["appliance-uuid"]
    assert connector["position"] == 2
    assert connector["station_serial"] == "STATION-1"


def test_fallback_highlevel_connector_mapping_ignores_invalid_measurements():
    mapping = fallback_highlevel_connector_mapping(
        "STATION-1",
        {
            1: {
                "measurements": [
                    "bad",
                    {"type": "GRID", "uuid": "grid-uuid"},
                    {"type": "APPLIANCE", "appliance": {"type": "FRIDGE", "uuid": "fridge"}},
                    {"type": "APPLIANCE", "category": "CAR_CHARGER"},
                ]
            }
        },
    )

    assert mapping == {}
    assert fallback_highlevel_connector_mapping(None, {1: {"measurements": []}}) == {}


def test_fallback_highlevel_connector_mapping_uses_expected_uuid_fallback_order():
    def mapping_for(measurement):
        return fallback_highlevel_connector_mapping(
            "STATION-1",
            {1: {"measurements": [measurement]}},
        )["STATION-1"]["connectors"]

    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "uuid": "measurement-uuid",
                "smartDeviceUuid": "measurement-smart-uuid",
                "appliance": {"type": "CAR_CHARGER", "uuid": "appliance-uuid"},
            }
        )
    ) == {"measurement-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "smartDeviceUuid": "measurement-smart-uuid",
                "deviceUuid": "measurement-device-uuid",
                "appliance": {"type": "CAR_CHARGER", "uuid": "appliance-uuid"},
            }
        )
    ) == {"measurement-smart-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "deviceUuid": "measurement-device-uuid",
                "appliance": {"type": "CAR_CHARGER", "uuid": "appliance-uuid"},
            }
        )
    ) == {"measurement-device-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "appliance": {
                    "type": "CAR_CHARGER",
                    "uuid": "appliance-uuid",
                    "smartDeviceUuid": "appliance-smart-uuid",
                },
            }
        )
    ) == {"appliance-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "appliance": {
                    "type": "CAR_CHARGER",
                    "smartDeviceUuid": "appliance-smart-uuid",
                    "deviceUuid": "appliance-device-uuid",
                },
            }
        )
    ) == {"appliance-smart-uuid"}
    assert set(
        mapping_for(
            {
                "type": "APPLIANCE",
                "appliance": {"type": "CAR_CHARGER", "deviceUuid": "appliance-device-uuid"},
            }
        )
    ) == {"appliance-device-uuid"}


def test_normalize_connector_mapping_preserves_station_metadata():
    mapping = {
        "6220017988": {
            "station_name": "Garage charger",
            "station_model": "Smappee EV Wall",
            "led_serial": "5130086592",
            "connectors": {
                1: {"uuid": "connector-uuid"},
            },
        }
    }

    result = normalize_connector_mapping_station_keys(mapping, None)

    assert result["6220017988"]["station_name"] == "Garage charger"
    assert result["6220017988"]["station_model"] == "Smappee EV Wall"
    assert result["6220017988"]["led_serial"] == "5130086592"
    assert result["6220017988"]["connectors"][1]["uuid"] == "connector-uuid"


def test_normalize_connector_mapping_preserves_orphan_metadata_on_fallback():
    mapping = {
        "": {
            "station_name": "Fallback charger",
            "station_model": "Smappee EV Wall",
            "connectors": {
                1: {"uuid": "connector-uuid"},
            },
        }
    }

    result = normalize_connector_mapping_station_keys(
        mapping,
        fallback_station_serial="6220017988",
    )

    assert result["6220017988"]["station_name"] == "Fallback charger"
    assert result["6220017988"]["station_model"] == "Smappee EV Wall"
    assert result["6220017988"]["connectors"][1]["uuid"] == "connector-uuid"


def test_normalize_connector_mapping_keeps_existing_station_metadata_over_fallback():
    mapping = {
        "6220017988": {
            "station_name": "Explicit station",
            "connectors": {},
        },
        "": {
            "station_name": "Fallback station",
            "connectors": {
                1: {"uuid": "connector-uuid"},
            },
        },
    }

    result = normalize_connector_mapping_station_keys(
        mapping,
        fallback_station_serial="6220017988",
    )

    assert result["6220017988"]["station_name"] == "Explicit station"
    assert result["6220017988"]["connectors"][1]["uuid"] == "connector-uuid"


def test_create_dashboard_client_persists_refresh_token_updates():
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {"username": "user", "password": "pass"}

    client = create_dashboard_client(hass, entry, MagicMock())
    client._token_update_callback({"dashboard_refresh_token": "new-refresh"})

    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        data={
            "username": "user",
            "password": "pass",
            "dashboard_refresh_token": "new-refresh",
        },
    )
