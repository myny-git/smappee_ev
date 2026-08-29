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


def test_update_total_increasing_basic():
    assert helpers.update_total_increasing(None, None) is None
    assert helpers.update_total_increasing(None, 5) == 5
    # Decrease rejected
    assert helpers.update_total_increasing(10, 9) == 10
    # Reset / zero rejected once we have a previous value
    assert helpers.update_total_increasing(10, 0) == 10
    # Increase accepted
    assert helpers.update_total_increasing(10, 15) == 15


def test_safe_sum_valid():
    assert helpers.safe_sum([1, 2, 3]) == 6.0
    # Accepts numeric strings
    assert helpers.safe_sum(["1", "2.5"]) == 3.5


def test_safe_sum_invalid():
    assert helpers.safe_sum([]) is None
    assert helpers.safe_sum([1, "x"]) is None
    # Not a list/tuple -> None
    assert helpers.safe_sum({"a": 1}) is None  # type: ignore[arg-type]


def test_percentage_to_current_maps_across_the_connector_range():
    assert helpers.percentage_to_current(0, 6, 32) == 6.0
    assert helpers.percentage_to_current(50, 6, 32) == 19.0
    assert helpers.percentage_to_current(100, 6, 32) == 32.0


def test_percentage_to_current_survives_a_collapsed_range():
    # A connector reporting max <= min must not divide by zero.
    assert helpers.percentage_to_current(100, 6, 6) == 7.0
    assert helpers.percentage_to_current(100, 6, 0) == 7.0


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
