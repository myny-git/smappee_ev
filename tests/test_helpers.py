import custom_components.smappee_ev.helpers as helpers


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
