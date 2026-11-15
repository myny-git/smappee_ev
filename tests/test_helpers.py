from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.smappee_ev.const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT
import custom_components.smappee_ev.helpers as helpers
from custom_components.smappee_ev.models.state import ConnectorState


def test_dashboard_property_value_supports_value_and_values_payloads():
    assert helpers.dashboard_property_value({"value": {"value": "7"}}) == "7"
    assert helpers.dashboard_property_value({"values": [{"Integer": "66"}]}) == "66"
    assert (
        helpers.dashboard_property_value({"values": [{"Quantity": {"value": "24", "unit": "A"}}]})
        == "24"
    )


def test_dashboard_property_value_rejects_missing_or_malformed_payloads():
    assert helpers.dashboard_property_value({}) is None
    assert helpers.dashboard_property_value({"values": []}) is None
    assert helpers.dashboard_property_value({"value": {"unknown": "shape"}}) is None


def test_make_unique_id_station():
    uid = helpers.make_unique_id(1, "SER123", "STUUID", None, "mqtt_connected")
    assert uid == "1:SER123:STUUID:mqtt_connected"


def test_make_unique_id_connector():
    uid = helpers.make_unique_id(2, "SER999", "STX", "CONN1", "power_total")
    assert uid == "2:SER999:STX:CONN1:power_total"


def _next_total(last: float | None, candidate: float | None) -> float | None:
    """A single reading without a receive time never confirms a reset."""
    value, evidence = helpers.next_total_increasing(
        last, candidate, received=None, reset_evidence=helpers.NO_RESET_EVIDENCE
    )
    assert evidence == helpers.NO_RESET_EVIDENCE
    return value


def test_next_total_increasing_basic():
    assert _next_total(None, None) is None
    assert _next_total(None, 5) == 5
    # Decrease rejected
    assert _next_total(10, 9) == 10
    # Reset / zero rejected once we have a previous value
    assert _next_total(10, 0) == 10
    # Increase accepted
    assert _next_total(10, 15) == 15
    # A single reading never confirms a reset.
    assert _next_total(1000, 2) == 1000


_START = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _feed(last: float, readings: list[tuple[float, float]]) -> list[float | None]:
    """Feed (minutes after start, reading) pairs and return the totals shown."""
    evidence = helpers.NO_RESET_EVIDENCE
    shown: list[float | None] = []
    value: float | None = last
    for minutes, reading in readings:
        value, evidence = helpers.next_total_increasing(
            value,
            reading,
            received=_START + timedelta(minutes=minutes),
            reset_evidence=evidence,
        )
        shown.append(value)
    return shown


def test_reset_rule_constants_match_home_assistant_and_require_persistence():
    assert helpers.TOTAL_RESET_RATIO == 0.9
    assert helpers.TOTAL_RESET_CONFIRMATIONS == 3
    assert timedelta(minutes=5) == helpers.TOTAL_RESET_MIN_DURATION


def test_real_reset_with_a_quickly_rising_new_meter_is_followed():
    """A replaced meter that counts up again is shown once it has lasted five minutes."""
    shown = _feed(1000.0, [(0, 1.0), (2, 1.6), (4, 2.3), (5, 2.9), (6, 3.4)])
    assert shown == [1000.0, 1000.0, 1000.0, 2.9, 3.4]


def test_burst_of_low_readings_then_normal_values_never_resets():
    """Three wrong readings within a minute must not reset the statistics.

    Accepting them would make the energy dashboard count the whole old total
    again as consumption when the normal readings return.
    """
    shown = _feed(1000.0, [(0, 2.0), (0.2, 2.0), (0.4, 2.0), (1, 1000.4), (2, 1000.6)])
    assert shown == [1000.0, 1000.0, 1000.0, 1000.4, 1000.6]


def test_falling_low_series_restarts_the_evidence():
    """A new meter only counts up; a lower reading cannot confirm a reset."""
    # 2.0 -> 1.0 restarts the evidence at minute 3, so five minutes run from there.
    shown = _feed(1000.0, [(0, 2.0), (3, 1.0), (5, 1.1), (7, 1.2), (8, 1.3)])
    assert shown == [1000.0, 1000.0, 1000.0, 1000.0, 1.3]


def test_old_meter_reading_between_low_readings_discards_them():
    """A reading close to the old total proves the old meter is still there."""
    shown = _feed(1000.0, [(0, 2.0), (3, 2.1), (4, 995.0), (5, 2.2), (6, 2.3)])
    assert shown == [1000.0] * 5


def test_zero_reading_neither_resets_nor_breaks_a_real_reset():
    """Zero is an incomplete reading: it is ignored, not counted for or against."""
    shown = _feed(1000.0, [(0, 2.0), (2, 0.0), (4, 2.1), (6, 2.2)])
    assert shown == [1000.0, 1000.0, 1000.0, 2.2]


def test_reevaluating_one_measurement_does_not_confirm_a_reset():
    """Entities re-read their state often; only new measurements count."""
    shown = _feed(1000.0, [(0, 2.0)] * 5)
    assert shown == [1000.0] * 5


def test_safe_sum_valid():
    assert helpers.safe_sum([1, 2, 3]) == 6.0
    # Accepts numeric strings
    assert helpers.safe_sum(["1", "2.5"]) == 3.5


def test_safe_sum_invalid():
    assert helpers.safe_sum([]) is None
    assert helpers.safe_sum([1, "x"]) is None
    # Not a list/tuple -> None
    assert helpers.safe_sum({"a": 1}) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("reported_min", "reported_max", "expected"),
    [
        (6, 20, (6, 20)),
        (10, 20, (10, 20)),
        (0, 0, (6, 32)),
        (-1, -1, (6, 32)),
        (-1, 32, (6, 32)),
        (6, 6, (6, 6)),
        (0, 32, (0, 32)),
        (6, 0, (6, 32)),
        (6, 4, (6, 32)),
        (10, 8, (6, 32)),
    ],
)
def test_resolve_connector_current_range_uses_valid_pair_or_previous(
    reported_min, reported_max, expected
):
    assert (
        helpers.resolve_connector_current_range(
            previous_min=6,
            previous_max=32,
            reported_min=reported_min,
            reported_max=reported_max,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("previous_min", "previous_max", "reported_min", "reported_max"),
    [
        (None, None, 6, 0),
        (None, None, 0, 0),
        (0, 0, None, None),
        (0, 0, 0, 0),
        (-1, -1, None, None),
        (-1, 32, None, None),
        (None, None, -1, 32),
        (10, 8, 10, 8),
        (None, None, None, None),
    ],
)
def test_resolve_connector_current_range_falls_back_to_defaults_on_startup(
    previous_min, previous_max, reported_min, reported_max
):
    assert helpers.resolve_connector_current_range(
        previous_min=previous_min,
        previous_max=previous_max,
        reported_min=reported_min,
        reported_max=reported_max,
    ) == (DEFAULT_MIN_CURRENT, DEFAULT_MAX_CURRENT)


@pytest.mark.parametrize(("percentage", "expected"), [(0, 6.0), (50, 19.0), (100, 32.0)])
def test_percentage_to_current_maps_across_the_connector_range(percentage, expected):
    assert helpers.percentage_to_current(percentage, 6, 32) == expected


@pytest.mark.parametrize("percentage", [0, 50, 100])
def test_percentage_to_current_keeps_a_fixed_range_fixed(percentage):
    assert helpers.percentage_to_current(percentage, 6, 6) == 6.0


def test_percentage_to_current_safely_handles_an_invalid_range():
    assert helpers.percentage_to_current(100, 6, 0) == 6.0


def test_current_to_percentage_is_the_inverse_and_clamps():
    assert helpers.current_to_percentage(6, 6, 32) == 0
    assert helpers.current_to_percentage(19, 6, 32) == 50
    assert helpers.current_to_percentage(32, 6, 32) == 100
    assert helpers.current_to_percentage(48, 6, 32) == 100
    assert helpers.current_to_percentage(1, 6, 32) == 0


def test_connector_percentage_setpoint_prefers_the_reported_percentage():
    state = ConnectorState(
        connector_number=1,
        min_current=6,
        max_current=32,
        selected_current_limit=32.0,
        selected_percentage_limit=0,
    )

    assert helpers.connector_percentage_setpoint(state) == 0


def test_connector_percentage_setpoint_falls_back_to_the_ampere_setpoint():
    state = ConnectorState(
        connector_number=1,
        min_current=6,
        max_current=32,
        selected_current_limit=19.0,
        selected_percentage_limit=None,
    )

    assert helpers.connector_percentage_setpoint(state) == 50


def test_connector_percentage_setpoint_is_none_when_unknown():
    assert helpers.connector_percentage_setpoint(None) is None
    assert helpers.connector_percentage_setpoint(ConnectorState(connector_number=1)) is None


@pytest.mark.parametrize(
    ("connector_number", "expected"),
    [(2, "2"), (None, "uuid")],
)
def test_connector_label_prefers_number_over_uuid_tail(connector_number, expected):
    """The connector device is named "<station> | Connector <label>".

    A numbered connector must show its number; without one, the UUID tail keeps
    the label short yet distinct, never the full UUID.
    """
    client = SimpleNamespace(connector_number=connector_number)
    assert helpers.build_connector_id(client, "connector-uuid") == expected
