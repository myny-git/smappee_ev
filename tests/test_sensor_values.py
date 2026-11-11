from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.sensor import ATTR_LAST_RESET, SensorExtraStoredData, SensorStateClass
from homeassistant.core import State
import pytest
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.entity import SmappeeConnectorEntity
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
    SmappeeSupportGridSensor,
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

    assert grid.translation_key == "grid_currents"
    assert not hasattr(grid, "_attr_name")
    assert grid.native_value == 6.667
    assert grid.extra_state_attributes == {"L1": 1.1111, "L2": 2.2222, "L3": 3.3333}
    assert pv.translation_key == "pv_currents"
    assert not hasattr(pv, "_attr_name")
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
        (SmappeeEvseStatusSensor, None),
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

    coordinator.measurement_freshness = SimpleNamespace(received={})
    coordinator._complete_control = {}
    assert entity.extra_state_attributes == {
        "measurement_received": None,
        "control_observation": None,
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
    entity._restored_value = "available"

    assert entity.native_value == "available"


@pytest.mark.parametrize(
    ("sensor_cls", "field", "known"),
    [
        (SmappeeChargingStateSensor, "session_state", "started"),
        (SmappeeEvseStatusSensor, "status_current", "charging"),
    ],
)
@pytest.mark.parametrize("raw", ["FUTURE_FIRMWARE_STATUS", "", "   ", None, 42])
def test_enum_sensor_unknown_values_remain_publishable_and_keep_raw_status(
    sensor_cls, field, known, raw
):
    state = ConnectorState(connector_number=1)
    setattr(state, field, raw)
    coordinator = _coordinator(connector=state)
    entity = sensor_cls(coordinator, _api(), 42, "station-uuid", "conn-1")
    entity.platform_data = SimpleNamespace(
        platform_name="smappee_ev", domain="sensor", default_language_platform_translations={}
    )
    if sensor_cls is SmappeeEvseStatusSensor:
        entity._restored_value = "available"
    # HA's actual state property rejects invalid ENUM values, not native_value.
    assert entity.state == (
        "available" if raw is None and sensor_cls is SmappeeEvseStatusSensor else None
    )
    assert entity.extra_state_attributes == ({"raw_status": raw} if raw is not None else None)
    setattr(state, field, f" {known.upper()} ")
    assert entity.state == known
    assert entity.extra_state_attributes == {"raw_status": f" {known.upper()} "}


def test_evse_status_rejects_invalid_restored_enum_value():
    coordinator = _coordinator()
    entity = SmappeeEvseStatusSensor(coordinator, _api(), 42, "station-uuid", "conn-1")
    coordinator.data = None
    entity._restored_value = "FUTURE_FIRMWARE_STATUS"
    entity.platform_data = SimpleNamespace(
        platform_name="smappee_ev", domain="sensor", default_language_platform_translations={}
    )
    assert entity.state is None


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


def test_session_energy_resets_statistics_at_each_session_start():
    """Each session counts from zero again; last_reset marks where it began."""
    coordinator = _coordinator(
        recent_sessions=[{"smartDeviceId": "device-1", "energy": 1.5, "from": 1_700_000_000_000}]
    )
    entity = ConnectorSessionEnergySensor(coordinator, _api(), 42, "station-uuid", "conn-1")

    assert entity.state_class is SensorStateClass.TOTAL
    assert entity.last_reset == datetime.fromtimestamp(1_700_000_000, tz=UTC)

    coordinator.data.recent_sessions = [
        {"smartDeviceId": "device-1", "energy": 0.1, "from": 1_700_100_000_000}
    ]
    assert entity.last_reset == datetime.fromtimestamp(1_700_100_000, tz=UTC)

    coordinator.data.recent_sessions = []
    assert entity.last_reset is None


def test_support_grid_is_shown_in_whole_amperes():
    entity = SmappeeSupportGridSensor(_coordinator(), _api(), 42, "station-uuid", "conn-1")

    assert entity.suggested_display_precision == 0


def test_session_energy_ignores_late_answers_with_older_session_data():
    """Energy and last_reset come from one session and never step back.

    A late Dashboard answer can still list the previous session, or an older
    energy value of the current one. Following it would make the statistics
    count the previous session again as a new cycle.
    """
    previous = {"smartDeviceId": "device-1", "energy": 12.0, "from": 1_700_000_000_000}
    current = {"smartDeviceId": "device-1", "energy": 0.4, "from": 1_700_100_000_000}
    coordinator = _coordinator(recent_sessions=[previous])
    entity = ConnectorSessionEnergySensor(coordinator, _api(), 42, "station-uuid", "conn-1")
    assert (entity.native_value, entity.last_reset) == (
        12.0,
        datetime.fromtimestamp(1_700_000_000, tz=UTC),
    )

    coordinator.data.recent_sessions = [current, previous]
    current_reset = datetime.fromtimestamp(1_700_100_000, tz=UTC)
    assert (entity.native_value, entity.last_reset) == (0.4, current_reset)

    # Late answer: the new session is missing again.
    coordinator.data.recent_sessions = [previous]
    assert (entity.native_value, entity.last_reset) == (0.4, current_reset)
    assert entity.extra_state_attributes["session_started_at"] == current_reset.isoformat()

    # Late answer: an older energy value of the current session.
    coordinator.data.recent_sessions = [{**current, "energy": 1.5}]
    assert entity.native_value == 1.5
    coordinator.data.recent_sessions = [{**current, "energy": 0.9}]
    assert (entity.native_value, entity.last_reset) == (1.5, current_reset)

    # The next session still takes over.
    coordinator.data.recent_sessions = [
        {"smartDeviceId": "device-1", "energy": 0.1, "from": 1_700_200_000_000}
    ]
    assert (entity.native_value, entity.last_reset) == (
        0.1,
        datetime.fromtimestamp(1_700_200_000, tz=UTC),
    )


def test_session_energy_picks_the_newest_session_whatever_the_api_order():
    previous = {"smartDeviceId": "device-1", "energy": 12.0, "from": 1_700_000_000_000}
    current = {"smartDeviceId": "device-1", "energy": 0.4, "from": 1_700_100_000_000}
    coordinator = _coordinator(recent_sessions=[previous, current])
    entity = ConnectorSessionEnergySensor(coordinator, _api(), 42, "station-uuid", "conn-1")

    assert (entity.native_value, entity.last_reset) == (
        0.4,
        datetime.fromtimestamp(1_700_100_000, tz=UTC),
    )


def test_session_energy_single_connector_fallback_picks_the_newest_session():
    coordinator = _coordinator(
        recent_sessions=[
            {"energy": 12.0, "from": 1_700_000_000},
            {"energy": 0.4, "from": 1_700_100_000},
        ]
    )
    entity = ConnectorSessionEnergySensor(coordinator, _api(), 42, "station-uuid", "different")

    assert entity.native_value == 0.4


@pytest.mark.asyncio
async def test_session_energy_does_not_step_back_after_a_restart():
    """The last reported session survives a restart through the restored state."""
    previous = {"smartDeviceId": "device-1", "energy": 12.0, "from": 1_700_000_000_000}
    coordinator = _coordinator(recent_sessions=[previous])
    entity = ConnectorSessionEnergySensor(coordinator, _api(), 42, "station-uuid", "conn-1")
    current_reset = datetime.fromtimestamp(1_700_100_000, tz=UTC)
    entity.async_get_last_sensor_data = AsyncMock(
        return_value=SensorExtraStoredData(native_value=0.4, native_unit_of_measurement="kWh")
    )
    entity.async_get_last_state = AsyncMock(
        return_value=State("sensor.session", "0.4", {"last_reset": current_reset.isoformat()})
    )

    with patch.object(SmappeeConnectorEntity, "async_added_to_hass", AsyncMock()):
        await entity.async_added_to_hass()

    # The first answer after the restart still lists only the previous session.
    assert (entity.native_value, entity.last_reset) == (0.4, current_reset)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "attributes"),
    [(None, {}), (SensorExtraStoredData(1.0, "kWh"), {}), (None, {"last_reset": "x"})],
)
async def test_session_energy_without_complete_restored_state_follows_the_api(stored, attributes):
    previous = {"smartDeviceId": "device-1", "energy": 12.0, "from": 1_700_000_000_000}
    entity = ConnectorSessionEnergySensor(
        _coordinator(recent_sessions=[previous]), _api(), 42, "station-uuid", "conn-1"
    )
    entity.async_get_last_sensor_data = AsyncMock(return_value=stored)
    entity.async_get_last_state = AsyncMock(
        return_value=State("sensor.session", "1.0", attributes) if attributes else None
    )

    with patch.object(SmappeeConnectorEntity, "async_added_to_hass", AsyncMock()):
        await entity.async_added_to_hass()

    assert entity.native_value == 12.0


def test_session_energy_empty_answer_is_unknown_and_never_counts_twice():
    """An empty answer shows unknown; the older session after it is still ignored."""
    previous = {"smartDeviceId": "device-1", "energy": 12.0, "from": 1_700_000_000_000}
    current = {"smartDeviceId": "device-1", "energy": 0.4, "from": 1_700_100_000_000}
    current_reset = datetime.fromtimestamp(1_700_100_000, tz=UTC)
    coordinator = _coordinator(recent_sessions=[current, previous])
    entity = ConnectorSessionEnergySensor(coordinator, _api(), 42, "station-uuid", "conn-1")
    assert (entity.native_value, entity.last_reset) == (0.4, current_reset)

    # Home Assistant skips unknown states in long-term statistics.
    coordinator.data.recent_sessions = []
    assert (entity.native_value, entity.last_reset) == (None, None)

    coordinator.data.recent_sessions = [previous]
    assert (entity.native_value, entity.last_reset) == (0.4, current_reset)

    coordinator.data.recent_sessions = [{**current, "energy": 0.8}, previous]
    assert (entity.native_value, entity.last_reset) == (0.8, current_reset)


async def test_session_energy_restore_round_trip_through_home_assistant(hass):
    """What the sensor stores is what Home Assistant's restore cache gives back."""
    current = {"smartDeviceId": "device-1", "energy": 0.4, "from": 1_700_100_000_000}
    current_reset = datetime.fromtimestamp(1_700_100_000, tz=UTC)
    before = ConnectorSessionEnergySensor(
        _coordinator(recent_sessions=[current]), _api(), 42, "station-uuid", "conn-1"
    )
    stored_state = State("sensor.smappee_session", "0.4", before.state_attributes)
    assert stored_state.attributes[ATTR_LAST_RESET] == current_reset.isoformat()
    mock_restore_cache_with_extra_data(
        hass, [(stored_state, before.extra_restore_state_data.as_dict())]
    )

    previous = {"smartDeviceId": "device-1", "energy": 12.0, "from": 1_700_000_000_000}
    after = ConnectorSessionEnergySensor(
        _coordinator(recent_sessions=[previous]), _api(), 42, "station-uuid", "conn-1"
    )
    after.hass = hass
    after.entity_id = "sensor.smappee_session"
    with patch.object(SmappeeConnectorEntity, "async_added_to_hass", AsyncMock()):
        await after.async_added_to_hass()

    assert (after.native_value, after.last_reset) == (0.4, current_reset)
