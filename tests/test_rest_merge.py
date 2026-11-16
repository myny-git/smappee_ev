"""Merging a smart-device REST poll into the current station and connector state."""

import pytest

from custom_components.smappee_ev.const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT
from custom_components.smappee_ev.coordinator import SmappeeStationCoordinator
from custom_components.smappee_ev.models.state import ConnectorState, StationState


def test_station_rest_merge_helpers_preserve_mqtt_rich_state():
    prev_station = StationState(led_brightness=25, dashboard_led_device_id="led-id")
    rest_station = StationState(led_brightness=None, api_available=False)
    merged_station = SmappeeStationCoordinator._merge_station_rest_state(prev_station, rest_station)

    assert SmappeeStationCoordinator._merge_station_rest_state(None, rest_station) is rest_station
    assert merged_station.led_brightness == 25
    assert merged_station.dashboard_led_device_id == "led-id"
    assert merged_station.api_available is False

    prev_connector = ConnectorState(
        connector_number=1,
        session_state="Charging",
        selected_current_limit=16,
        selected_mode="STANDARD",
        min_current=6,
        max_current=32,
        power_total=123,
    )
    rest_connector = ConnectorState(
        connector_number=2,
        session_state="Paused",
        selected_current_limit=None,
        selected_percentage_limit=80,
        selected_mode=None,
        min_current=8,
        max_current=24,
        support_grid=10,
        api_available=False,
    )
    merged_connector = SmappeeStationCoordinator._merge_connector_rest_state(
        prev_connector, rest_connector
    )

    assert (
        SmappeeStationCoordinator._merge_connector_rest_state(None, rest_connector)
        is rest_connector
    )
    assert merged_connector.connector_number == 2
    assert merged_connector.session_state == "Paused"
    assert merged_connector.selected_current_limit == 16
    assert merged_connector.selected_percentage_limit == 80
    assert merged_connector.selected_mode == "STANDARD"
    assert merged_connector.power_total == 123
    assert merged_connector.api_available is False


def test_rest_merge_repairs_a_stale_mqtt_derived_current_limit():
    """A REST poll must be able to correct the Ampere setpoint it derives."""
    prev_connector = ConnectorState(
        connector_number=1,
        session_state="Charging",
        selected_current_limit=32.0,
        selected_percentage_limit=100,
        optimization_strategy="NONE",
        min_current=6,
        max_current=32,
    )
    rest_connector = ConnectorState(
        connector_number=1,
        session_state="Charging",
        selected_percentage_limit=0,
        min_current=6,
        max_current=32,
        api_available=True,
    )

    merged = SmappeeStationCoordinator._merge_connector_rest_state(prev_connector, rest_connector)

    assert merged.selected_current_limit == 6.0
    assert merged.selected_percentage_limit == 0


@pytest.mark.parametrize(
    ("reported_min", "reported_max", "expected_min", "expected_max"),
    [
        (6, 0, 6, 32),
        (6, 20, 6, 20),
        (10, 20, 10, 20),
        (10, 8, 6, 32),
    ],
)
def test_rest_merge_resolves_connector_current_range(
    reported_min, reported_max, expected_min, expected_max
):
    prev_connector = ConnectorState(connector_number=1, min_current=6, max_current=32)
    rest_connector = ConnectorState(
        connector_number=1,
        min_current=reported_min,
        max_current=reported_max,
    )

    merged = SmappeeStationCoordinator._merge_connector_rest_state(prev_connector, rest_connector)

    assert (merged.min_current, merged.max_current) == (expected_min, expected_max)


def test_rest_merge_derives_current_from_the_validated_range():
    prev_connector = ConnectorState(
        connector_number=1,
        optimization_strategy="NONE",
        min_current=6,
        max_current=32,
    )
    rest_connector = ConnectorState(
        connector_number=1,
        selected_percentage_limit=50,
        min_current=6,
        max_current=0,
    )

    merged = SmappeeStationCoordinator._merge_connector_rest_state(prev_connector, rest_connector)

    assert (merged.min_current, merged.max_current) == (6, 32)
    assert merged.selected_current_limit == 19.0


def test_rest_merge_invalid_startup_range_uses_defaults():
    rest_connector = ConnectorState(connector_number=1, min_current=10, max_current=8)

    merged = SmappeeStationCoordinator._merge_connector_rest_state(None, rest_connector)

    assert (merged.min_current, merged.max_current) == (
        DEFAULT_MIN_CURRENT,
        DEFAULT_MAX_CURRENT,
    )


def test_rest_merge_keeps_the_current_limit_under_an_optimization_strategy():
    """In SMART/SOLAR the percentage is optimizer-driven, not the user setpoint."""
    prev_connector = ConnectorState(
        connector_number=1,
        selected_current_limit=16.0,
        optimization_strategy="EXCESS_ONLY",
        min_current=6,
        max_current=32,
    )
    rest_connector = ConnectorState(
        connector_number=1,
        selected_percentage_limit=100,
        min_current=6,
        max_current=32,
    )

    merged = SmappeeStationCoordinator._merge_connector_rest_state(prev_connector, rest_connector)

    assert merged.selected_current_limit == 16.0


def test_rest_merge_keeps_the_current_limit_without_a_percentage():
    prev_connector = ConnectorState(
        connector_number=1,
        selected_current_limit=16.0,
        optimization_strategy="NONE",
        min_current=6,
        max_current=32,
    )
    rest_connector = ConnectorState(connector_number=1, min_current=6, max_current=32)

    merged = SmappeeStationCoordinator._merge_connector_rest_state(prev_connector, rest_connector)

    assert merged.selected_current_limit == 16.0
