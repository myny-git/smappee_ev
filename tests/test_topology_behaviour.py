"""Behavioural tests for station topology assembly and Dashboard topology parsing.

These tests pin down how charging stations and connectors are discovered and
merged, because mistakes here show up for users as duplicate devices, missing
connectors, connectors on the wrong station, or two homes merged together.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.smappee_ev.api.discovery import SmappeeLocationTopology
from custom_components.smappee_ev.runtime_assembly import (
    _deduplicate_stations,
    _prepare_control_stations,
    prepare_site_topologies,
)
from custom_components.smappee_ev.topology import (
    device_connector_uuid,
    fallback_assign,
    fallback_highlevel_connector_mapping,
)
from tests.factories import make_connector_runtime, make_led_runtime, make_station_runtime

_ASSEMBLY = "custom_components.smappee_ev.runtime_assembly"


def _topology(
    control_location_id: int,
    *,
    charging_station_serial: str | None = None,
    site_location_id: int = 100,
    control_name: str = "Charger",
    site_gateway_serial: str | None = "GATEWAY-1",
) -> SmappeeLocationTopology:
    return SmappeeLocationTopology(
        site_location_id=site_location_id,
        site_location_uuid=f"site-{site_location_id}",
        site_name="Home",
        site_function_type="ELECTRICITY",
        control_location_id=control_location_id,
        control_location_uuid=f"control-{control_location_id}",
        control_name=control_name,
        control_function_type="CHARGINGSTATION",
        measurement_location_ids=[site_location_id],
        charging_station_serial=charging_station_serial,
        site_gateway_serial=site_gateway_serial,
        site_gateway_type="Genius",
        control_gateway_serial=None,
        control_gateway_type=None,
        write_access=True,
    )


def _dashboard(
    devices_by_sid: dict[int, list[dict[str, Any]]],
    details_by_serial: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Return a configured Dashboard client double serving canned payloads."""
    details_by_serial = details_by_serial or {}

    async def smart_devices(sid: int) -> list[dict[str, Any]]:
        return devices_by_sid[sid]

    async def station_details(serial: str) -> Any:
        return details_by_serial.get(serial)

    async def highlevel(_sid: int) -> dict[str, Any]:
        return {"measurements": []}

    return SimpleNamespace(
        _token="token",  # noqa: S106 - marks the double as a configured client
        async_get_smart_devices=smart_devices,
        async_get_charging_station_details=station_details,
        async_get_highlevel_configuration=highlevel,
    )


def _station_device(serial: str | None, uuid: str | None = None) -> dict[str, Any]:
    device: dict[str, Any] = {"type": {"category": "CHARGINGSTATION"}}
    if serial is not None:
        device["serialNumber"] = serial
    if uuid is not None:
        device.update(uuid=uuid, id=f"{uuid}-id")
    return device


def _car_device(uuid: str, **extra: Any) -> dict[str, Any]:
    return {"uuid": uuid, "id": f"{uuid}-id", "type": {"category": "CARCHARGER"}, **extra}


def _connector_module(uuid: str, position: int) -> dict[str, Any]:
    return {"position": position, "smartDevice": _car_device(uuid)}


def _positions(bucket: Any) -> dict[str, int | None]:
    return {key: connector.connector_position for key, connector in bucket.connectors.items()}


# ---------------------------------------------------------------------------
# Station deduplication across control locations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_station_advertised_by_two_control_locations_becomes_one_device():
    """A station seen from its own and a parent control location must not be duplicated.

    Prevents a second Home Assistant device (with duplicated entities) for the same
    physical charger, and prevents the parent location's name from replacing the
    station's own control location.
    """
    station = _station_device("ST-1", uuid="st-uuid")
    devices = [station, _car_device("c1")]
    dashboard = _dashboard(
        {200: devices, 300: devices},
        {"ST-1": {"modules": [_connector_module("c1", 1)]}},
    )
    direct = _topology(200, charging_station_serial="ST-1", control_name="Garage charger")
    parent = _topology(300, charging_station_serial=None, control_name="Parent location")

    with (
        patch(f"{_ASSEMBLY}._create_site_coordinator", AsyncMock(return_value=MagicMock())),
        patch(f"{_ASSEMBLY}._create_coordinators", AsyncMock()),
        patch(f"{_ASSEMBLY}.setup_mqtt", return_value=MagicMock()),
    ):
        site, _mqtt = await prepare_site_topologies(
            MagicMock(), [parent, direct], 60, "client", dashboard_client=dashboard
        )

    assert site is not None
    assert list(site.stations) == ["st-uuid"]
    bucket = site.stations["st-uuid"]
    assert bucket.control_location_id == 200
    assert bucket.station_name == "Garage charger"
    assert set(bucket.connectors) == {"c1"}
    assert site.control_location_ids == [200, 300]


def test_duplicate_station_fills_missing_metadata_without_overwriting_known_values():
    """The kept station only borrows what it lacks from its duplicate.

    Prevents losing the model/name/gateway when the first match has incomplete
    Dashboard data, and prevents the duplicate's connector/LED objects or values
    from replacing the ones already wired to the kept station.
    """
    kept_connector = make_connector_runtime(connector_key="c1")
    kept_led = make_led_runtime(led_key="led-1")
    kept = make_station_runtime(
        control_location_id=200,
        station_uuid="st-uuid",
        serial="ST-1",
        site_name="Kept site",
        gateway_serial=None,
        station_name=None,
        station_model=None,
        connectors={"c1": kept_connector},
        led_devices={"led-1": kept_led},
    )
    duplicate = make_station_runtime(
        control_location_id=300,
        station_uuid="st-uuid",
        serial="ST-1",
        site_name="Duplicate site",
        gateway_serial="GATEWAY-9",
        station_name="Garage charger",
        station_model="EV Wall",
        connectors={
            "c1": make_connector_runtime(connector_key="c1", connector_position=7),
            "c2": make_connector_runtime(connector_key="c2", connector_position=2),
        },
        led_devices={
            "led-1": make_led_runtime(led_key="led-1", led_device_name="Other"),
            "led-2": make_led_runtime(led_key="led-2"),
        },
    )

    result = _deduplicate_stations(
        [(_topology(200), {"st-uuid": kept}), (_topology(300), {"st-uuid": duplicate})]
    )

    assert list(result) == ["st-uuid"]
    station = result["st-uuid"]
    assert station is kept
    assert station.control_location_id == 200
    assert station.site_name == "Kept site"
    assert station.gateway_serial == "GATEWAY-9"
    assert station.station_name == "Garage charger"
    assert station.charging_station_model == "EV Wall"
    assert station.connectors["c1"] is kept_connector
    assert _positions(station) == {"c1": 1, "c2": 2}
    assert station.led_devices["led-1"] is kept_led
    assert set(station.led_devices) == {"led-1", "led-2"}


@pytest.mark.parametrize("direct_first", [True, False])
def test_direct_control_location_owns_the_station_regardless_of_order(direct_first):
    """The location that directly controls the station wins over one that only lists it.

    Prevents commands and MQTT routing from being attached to a parent location
    just because it happened to be processed first.
    """
    direct = make_station_runtime(control_location_id=200, serial="ST-1", station_model=None)
    listed = make_station_runtime(control_location_id=300, serial="ST-1", station_model="EV Wall")
    direct_candidate = (_topology(200, charging_station_serial="ST-1"), {"st-uuid": direct})
    listed_candidate = (_topology(300), {"st-uuid": listed})
    candidates = (
        [direct_candidate, listed_candidate]
        if direct_first
        else [listed_candidate, direct_candidate]
    )

    result = _deduplicate_stations(candidates)

    assert list(result) == ["st-uuid"]
    assert result["st-uuid"].control_location_id == 200
    assert result["st-uuid"].charging_station_model == "EV Wall"


@pytest.mark.parametrize(
    ("second_control_id", "expect_warning"),
    [(300, True), (200, False)],
    ids=["two-locations-claim-control", "same-location-listed-twice"],
)
def test_conflicting_direct_control_claims_keep_first_match_and_warn(
    caplog, second_control_id, expect_warning
):
    """Two locations both claiming direct control is surfaced, never silently resolved.

    Prevents a nondeterministic owner (flapping between restarts) and makes the
    ambiguous Dashboard topology visible in the log; a repeat from the same
    location is not a conflict and must not warn.
    """
    first = make_station_runtime(control_location_id=200, serial="ST-1")
    second = make_station_runtime(control_location_id=second_control_id, serial="ST-1")

    with caplog.at_level(logging.WARNING, logger=_ASSEMBLY):
        result = _deduplicate_stations(
            [
                (_topology(200, charging_station_serial="ST-1"), {"st-a": first}),
                (_topology(second_control_id, charging_station_serial="ST-1"), {"st-b": second}),
            ]
        )

    assert list(result) == ["st-a"]
    assert result["st-a"] is first
    warned = any("multiple service locations" in r.getMessage() for r in caplog.records)
    assert warned is expect_warning


@pytest.mark.parametrize(
    ("serial_a", "serial_b", "expected_keys"),
    [
        ("ST-1", " st-1 ", ["st-a"]),
        (None, None, ["st-a", "st-b"]),
        ("ST-1", "ST-2", ["st-a", "st-b"]),
    ],
    ids=["serial-case-and-whitespace", "no-serial-distinct-uuids", "different-serials"],
)
def test_station_identity_uses_normalized_serial_then_uuid(serial_a, serial_b, expected_keys):
    """Only the same physical station is merged.

    Prevents duplicates when the Dashboard formats one serial differently, and
    prevents two distinct stations without a serial from being collapsed into one.
    """
    station_a = make_station_runtime(control_location_id=200, serial=serial_a)
    station_b = make_station_runtime(control_location_id=300, serial=serial_b)

    result = _deduplicate_stations(
        [(_topology(200), {"st-a": station_a}), (_topology(300), {"st-b": station_b})]
    )

    assert list(result) == expected_keys


# ---------------------------------------------------------------------------
# Site preparation guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topologies_from_different_sites_are_refused():
    """Topologies of two physical sites must never be prepared as one site.

    Prevents two homes' chargers, grid sensors and MQTT subscriptions from being
    merged under a single site device.
    """
    with pytest.raises(ValueError, match="different physical sites"):
        await prepare_site_topologies(
            MagicMock(),
            [_topology(200, site_location_id=100), _topology(300, site_location_id=101)],
            60,
            "client",
        )


@pytest.mark.asyncio
async def test_empty_topology_list_prepares_nothing():
    """A site without control topologies yields no runtime instead of crashing setup."""
    assert await prepare_site_topologies(MagicMock(), [], 60, "client") == (None, None)


# ---------------------------------------------------------------------------
# Connector mapping fallbacks per control location
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_dashboard_access_no_station_is_invented():
    """Without Dashboard device data, no station is fabricated for the control location.

    Prevents creating devices with made-up identities that would later be orphaned.
    """
    stations, serial = await _prepare_control_stations(
        _topology(200, charging_station_serial="ST-1"), None, {}
    )

    assert stations == {}
    assert serial == "smappee-100"


@pytest.mark.asyncio
async def test_unavailable_station_details_fall_back_to_car_charger_devices():
    """When station details are unavailable, the car-charger smartdevices become its connectors.

    Prevents a single-station install from losing all connector entities whenever
    the Dashboard details endpoint fails; positions come from the device or its order.
    """
    dashboard = _dashboard(
        {
            200: [
                _station_device("ST-1", uuid="st-uuid"),
                _car_device("c1"),
                _car_device("c2", connectorNumber=2),
            ]
        },
        {"ST-1": None},
    )

    stations, _serial = await _prepare_control_stations(
        _topology(200, charging_station_serial="ST-1"), dashboard, {}
    )

    assert list(stations) == ["st-uuid"]
    assert _positions(stations["st-uuid"]) == {"c1": 1, "c2": 2}


@pytest.mark.asyncio
async def test_highlevel_configuration_identifies_station_connector_when_dashboard_cannot():
    """The highlevel CAR_CHARGER measurement places the connector on the topology's station.

    When station details fail and several station smartdevices are listed, the
    Dashboard fallback cannot choose; the highlevel configuration must then decide,
    yielding only the topology's station with its connector at the measured position.
    """
    dashboard = _dashboard(
        {
            200: [
                _station_device("OTHER-A", uuid="a-uuid"),
                _station_device("OTHER-B", uuid="b-uuid"),
                _car_device("c-uuid"),
                _car_device("unrelated"),
            ]
        }
    )
    highlevel = {
        200: {
            "measurements": [
                {
                    "type": "APPLIANCE",
                    "appliance": {"type": "CAR_CHARGER", "uuid": "c-uuid"},
                    "position": 2,
                }
            ]
        }
    }

    stations, _serial = await _prepare_control_stations(
        _topology(200, charging_station_serial="ST-1"), dashboard, highlevel
    )

    assert list(stations) == ["ST-1"]
    assert stations["ST-1"].charging_station_serial == "ST-1"
    assert _positions(stations["ST-1"]) == {"c-uuid": 2}


@pytest.mark.asyncio
async def test_station_without_uuid_is_built_from_the_connector_mapping_serial():
    """A station smartdevice lacking a UUID is rebuilt from the mapping's station serial.

    Prevents the charger (and its connectors) from disappearing entirely when the
    Dashboard lists the station only by serial number.
    """
    dashboard = _dashboard(
        {200: [_station_device("ST-1"), _car_device("c1")]},
        {"ST-1": {"modules": [_connector_module("c1", 1)]}},
    )

    stations, _serial = await _prepare_control_stations(_topology(200), dashboard, {})

    assert list(stations) == ["ST-1"]
    assert stations["ST-1"].charging_station_serial == "ST-1"
    assert set(stations["ST-1"].connectors) == {"c1"}


@pytest.mark.asyncio
async def test_unassignable_connectors_across_several_stations_are_not_guessed(caplog):
    """With several stations and no assignable connector, nothing is guessed and it is logged.

    Prevents connectors from being attached to an arbitrary station (wrong device,
    wrong commands) when the mapping cannot be resolved.
    """
    dashboard = _dashboard(
        {
            200: [
                _station_device("ST-1", uuid="st1-uuid"),
                _station_device("ST-2", uuid="st2-uuid"),
                _car_device("present"),
            ]
        }
    )
    mapping = {
        "ST-1": {"connectors": {"ghost-1": {"position": 1}}},
        "ST-2": {"connectors": {"ghost-2": {"position": 1}}},
    }

    with (
        patch(
            f"{_ASSEMBLY}.fetch_dashboard_connector_mapping",
            AsyncMock(return_value=mapping),
        ),
        caplog.at_level(logging.WARNING, logger=_ASSEMBLY),
    ):
        stations, _serial = await _prepare_control_stations(_topology(200), dashboard, {})

    assert set(stations) == {"st1-uuid", "st2-uuid"}
    assert all(not bucket.connectors for bucket in stations.values())
    assert any("no connectors could be assigned" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Topology parsing helpers
# ---------------------------------------------------------------------------


def _car_measurement(uuid: str, **fields: Any) -> dict[str, Any]:
    return {"type": "APPLIANCE", "appliance": {"type": "CAR_CHARGER"}, "uuid": uuid, **fields}


@pytest.mark.parametrize(
    ("fields", "expected_position"),
    [
        ({"position": 2, "name": "Car charger 1"}, 2),
        ({"connectorNumber": "2", "name": "Car charger 1"}, 2),
        ({"position": "left", "name": "Car charger - 2"}, 2),
        ({"name": "Car charger"}, 1),
    ],
    ids=["position-beats-name", "connector-number", "non-numeric-uses-name", "default-1"],
)
def test_connector_position_prefers_explicit_field_over_name(fields, expected_position):
    """The measurement's explicit position decides the connector number before its name.

    Prevents connector 2 being reported as connector 1 (wrong entity names and
    wrong current commands) when the user renamed the measurement, and keeps a
    usable position when the explicit field is not numeric.
    """
    mapping = fallback_highlevel_connector_mapping(
        "ST-1", {200: {"measurements": [_car_measurement("c1", **fields)]}}
    )

    assert mapping["ST-1"]["connectors"]["c1"]["position"] == expected_position


def test_two_connector_station_gets_connectors_one_and_two():
    """A two-connector station maps each measurement to its own connector position.

    Prevents both connectors collapsing onto position 1.
    """
    mapping = fallback_highlevel_connector_mapping(
        "ST-1",
        {
            200: {
                "measurements": [
                    _car_measurement("left", position=1, name="Car charger"),
                    _car_measurement("right", connectorNumber=2, name="Car charger"),
                ]
            }
        },
    )

    connectors = mapping["ST-1"]["connectors"]
    assert {uuid: info["position"] for uuid, info in connectors.items()} == {
        "left": 1,
        "right": 2,
    }


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        ("servicelocation/s/etc/devices/chan-uuid/property/chargingstate", "chan-uuid"),
        ("servicelocation/s/property/chargingstate", "dev-uuid"),
        ({"name": ""}, "dev-uuid"),
    ],
    ids=["channel-uuid", "channel-without-device-segment", "empty-channel"],
)
def test_connector_uuid_ignores_malformed_charging_state_channel(channel, expected):
    """A malformed charging-state channel never yields a bogus connector UUID.

    Prevents connector identities (and their entities) from being keyed on topic
    fragments instead of the device UUID.
    """
    device = {
        "uuid": "dev-uuid",
        "carCharger": {"chargingStateUpdateChannel": channel},
    }

    assert device_connector_uuid(device) == expected


# ---------------------------------------------------------------------------
# fallback_assign guards
# ---------------------------------------------------------------------------


def test_fallback_assign_puts_car_chargers_on_the_only_station_and_skips_unidentified():
    """The fallback attaches identifiable car chargers to the station; unidentified ones are skipped.

    Prevents connector entities without a UUID (which cannot be addressed or kept
    stable) from being created.
    """
    station = make_station_runtime(serial="ST-1", connectors={})
    stations = {"st-uuid": station}

    fallback_assign(
        stations,
        [_car_device("c1", connectorNumber=2), _car_device("c2"), {"type": "CARCHARGER"}],
        "GATEWAY-1",
        200,
    )

    assert _positions(station) == {"c1": 2, "c2": 1}
    assert station.connectors["c1"].connector_client.charging_station_serial == "ST-1"


def test_fallback_assign_never_reassigns_already_assigned_connectors():
    """Once any connector is assigned, the fallback leaves the stations untouched.

    Prevents connectors from other stations being piled onto the first station
    when the regular mapping already worked.
    """
    existing = make_connector_runtime(connector_key="c1")
    first = make_station_runtime(serial="ST-1", connectors={})
    second = make_station_runtime(serial="ST-2", connectors={"c1": existing})

    fallback_assign({"st-1": first, "st-2": second}, [_car_device("c9")], "GATEWAY-1", 200)

    assert first.connectors == {}
    assert second.connectors == {"c1": existing}


def test_fallback_assign_without_stations_is_a_no_op():
    """Without a station there is nothing to attach connectors to, and no crash."""
    stations: dict[str, Any] = {}

    fallback_assign(stations, [_car_device("c1")], "GATEWAY-1", 200)

    assert stations == {}
