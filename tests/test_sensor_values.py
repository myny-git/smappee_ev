from unittest.mock import MagicMock

import pytest

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState
from custom_components.smappee_ev.sensor import (
    ConnCurrentL1,
    ConnCurrentL2,
    ConnCurrentL3,
    ConnectorCurrentASensor,
    ConnectorPowerSensor,
    ConnectorSessionEnergySensor,
    ConnEnergyImport,
    SmappeeChargingStateSensor,
    SmappeeEVCCStateSensor,
    SmappeeEvseStatusSensor,
    StationAlwaysOnPower,
    StationGridCurrentL1,
    StationGridCurrentL2,
    StationGridCurrentL3,
    StationGridCurrents,
    StationGridEnergyExport,
    StationGridEnergyImport,
    StationGridPower,
    StationGridVoltageL1,
    StationGridVoltageL2,
    StationGridVoltageL3,
    StationHouseConsumptionPower,
    StationPvCurrentL1,
    StationPvCurrentL2,
    StationPvCurrentL3,
    StationPvCurrents,
    StationPvEnergyImport,
    StationPvPower,
    _nested_value,
    _session_ts_to_datetime,
)


def _api(connector_number: int | None = 1) -> MagicMock:
    api = MagicMock(spec=SmappeeDeviceHandle)
    api.serial = "STATION123"
    api.charging_station_serial = "STATION123"
    api.connector_number = connector_number
    api.smart_device_id = "device-1"
    return api


def _coordinator(
    station: StationState | None = None,
    connector: ConnectorState | None = None,
    recent_sessions: list[dict] | None = None,
) -> MagicMock:
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.last_update_success = True
    coordinator.station_client = MagicMock(serial_id="SERIAL123")
    coordinator.data = IntegrationData(
        station=station or StationState(),
        connectors={"conn-1": connector or ConnectorState(connector_number=1)},
        recent_sessions=recent_sessions or [],
    )
    coordinator.connector_clients = {"conn-1": _api()}
    return coordinator


@pytest.mark.parametrize(
    ("sensor_cls", "expected"),
    [
        (StationAlwaysOnPower, 111.0),
        (StationGridPower, 123.0),
        (StationHouseConsumptionPower, 456.0),
        (StationPvPower, 789.0),
        (StationGridEnergyImport, 10.5),
        (StationGridEnergyExport, 1.2),
        (StationPvEnergyImport, 3.4),
        (StationGridCurrentL1, 1.1),
        (StationGridCurrentL2, 2.2),
        (StationGridCurrentL3, 3.3),
        (StationPvCurrentL1, 4.4),
        (StationPvCurrentL2, 5.5),
        (StationPvCurrentL3, 6.6),
        (StationGridVoltageL1, 230.0),
        (StationGridVoltageL2, 231.0),
        (StationGridVoltageL3, 232.0),
    ],
)
def test_station_sensor_native_values(sensor_cls, expected):
    coordinator = _coordinator(
        StationState(
            always_on_power=111,
            grid_power_total=123,
            house_consumption_power=456,
            pv_power_total=789,
            grid_energy_import_kwh=10.5,
            grid_energy_export_kwh=1.2,
            pv_energy_import_kwh=3.4,
            grid_current_phases=[1.1, 2.2, 3.3],
            pv_current_phases=[4.4, 5.5, 6.6],
            grid_voltage_phases=[230, 231, 232],
        )
    )

    entity = sensor_cls(coordinator, _api(), 42, "station-uuid")

    assert entity.native_value == expected


def test_station_phase_total_sensors_include_phase_attributes():
    coordinator = _coordinator(
        StationState(
            grid_current_phases=[1.1111, 2.2222, 3.3333],
            pv_current_phases=[4.4444, 5.5555, 6.6666],
        )
    )

    grid = StationGridCurrents(coordinator, _api(), 42, "station-uuid")
    pv = StationPvCurrents(coordinator, _api(), 42, "station-uuid")

    assert grid._attr_name == "Grid current (L1-L3)"
    assert grid.native_value == 6.667
    assert grid.extra_state_attributes == {"L1": 1.1111, "L2": 2.2222, "L3": 3.3333}
    assert pv._attr_name == "PV current (L1-L3)"
    assert pv.native_value == 16.666
    assert pv.extra_state_attributes == {"L1": 4.4444, "L2": 5.5555, "L3": 6.6666}


@pytest.mark.parametrize(
    ("sensor_cls", "expected"),
    [
        (ConnCurrentL1, 7.0),
        (ConnCurrentL2, 8.0),
        (ConnCurrentL3, 9.0),
        (ConnectorPowerSensor, 7200.0),
        (ConnectorCurrentASensor, 24.0),
        (ConnEnergyImport, 15.25),
        # "Started" (not "Charging") is used here: "Charging" is a real EVSE-status
        # value (see SmappeeEvseStatusSensor below) but is not a documented
        # chargingState/session_state value, see docs/HA_integration.md and #251.
        (SmappeeChargingStateSensor, "started"),
        (SmappeeEVCCStateSensor, "B"),
        (SmappeeEvseStatusSensor, "c1"),
    ],
)
def test_connector_sensor_native_values(sensor_cls, expected):
    coordinator = _coordinator(
        connector=ConnectorState(
            connector_number=1,
            current_phases=[7, 8, 9],
            power_total=7200,
            energy_import_kwh=15.25,
            session_state="Started",
            evcc_state="B",
            status_current="C1",
        )
    )

    entity = sensor_cls(coordinator, _api(), 42, "station-uuid", "conn-1")

    assert entity.native_value == expected


@pytest.mark.parametrize(
    ("raw_session_state", "expected"),
    [
        ("Initialize", "initialize"),
        ("Started", "started"),
        ("Suspended", "suspended"),
        ("Stopped", "stopped"),
    ],
)
def test_charging_state_supports_documented_session_states(raw_session_state, expected):
    """Regression test for #251: charging_state must accept every documented value.

    ConnectorState.session_state defaults to "Initialize" until the first API
    fetch completes, so this value must always be a supported ENUM option or
    Home Assistant raises a ValueError when writing the entity state.
    """
    coordinator = _coordinator(
        connector=ConnectorState(connector_number=1, session_state=raw_session_state)
    )
    entity = SmappeeChargingStateSensor(coordinator, _api(), 42, "station-uuid", "conn-1")

    assert entity.native_value == expected
    assert entity.native_value in entity.options


def test_charging_state_default_connector_state_is_supported():
    """A brand-new ConnectorState() (before any API fetch) must not crash the sensor."""
    coordinator = _coordinator(connector=ConnectorState(connector_number=1))
    entity = SmappeeChargingStateSensor(coordinator, _api(), 42, "station-uuid", "conn-1")

    assert entity.native_value == "initialize"
    assert entity.native_value in entity.options


def test_connector_current_total_returns_none_for_invalid_phase_value():
    coordinator = _coordinator(
        connector=ConnectorState(connector_number=1, current_phases=[1, "x"])
    )
    entity = ConnectorCurrentASensor(coordinator, _api(), 42, "station-uuid", "conn-1")

    assert entity.native_value is None


def test_evcc_state_attributes_and_restored_fallback():
    coordinator = _coordinator(
        connector=ConnectorState(
            connector_number=1,
            iec_status="B1",
            session_state="Charging",
            raw_charging_mode="SMART",
            optimization_strategy="EXCESS_ONLY",
            paused=True,
            status_current="C1",
        )
    )
    entity = SmappeeEVCCStateSensor(coordinator, _api(), 42, "station-uuid", "conn-1")

    assert entity.extra_state_attributes == {
        "iec_status": "B1",
        "session_state": "Charging",
        "charging_mode": "SMART",
        "optimization_strategy": "EXCESS_ONLY",
        "paused": True,
        "status_current": "C1",
    }

    coordinator.data = None
    entity._restored_value = "A"
    entity._restored_attributes = {"iec_status": "A1"}

    assert entity.native_value == "A"
    assert entity.extra_state_attributes == {"iec_status": "A1"}


def test_evse_status_restored_fallback_without_current_state():
    coordinator = _coordinator()
    entity = SmappeeEvseStatusSensor(coordinator, _api(), 42, "station-uuid", "conn-1")

    coordinator.data = None
    entity._restored_value = "B1"

    assert entity.native_value == "B1"


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        ({"smartDeviceId": "device-1", "energy": 2.5}, 2.5),
        (
            {"connector": {"number": 1}, "chargingStationSerial": "STATION123", "energy": "3.456"},
            3.46,
        ),
    ],
)
def test_session_energy_sensor_matches_alternate_connector_identifiers(session, expected):
    coordinator = _coordinator(recent_sessions=[session])
    entity = ConnectorSessionEnergySensor(coordinator, _api(), 42, "station-uuid", "conn-1")

    assert entity.native_value == expected


def test_session_energy_sensor_uses_single_connector_energy_fallback():
    coordinator = _coordinator(recent_sessions=[{"energy": 4.2, "from": 1_700_000_000}])
    entity = ConnectorSessionEnergySensor(coordinator, _api(), 42, "station-uuid", "different")

    assert entity.native_value == 4.2
    assert "duration_minutes" not in entity.extra_state_attributes
    assert "duration_formatted" not in entity.extra_state_attributes
    assert "from" not in entity.extra_state_attributes


def test_session_helpers_ignore_empty_missing_and_invalid_values():
    assert (
        _nested_value({"outer": {"value": ""}, "fallback": "ok"}, ("outer", "value"), ("fallback",))
        == "ok"
    )
    assert _nested_value({"outer": {}}, ("outer", "value")) is None
    assert _session_ts_to_datetime("not-a-timestamp") is None
    assert _session_ts_to_datetime(999_999_999_999_999_999_999) is None
