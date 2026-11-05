"""Tracking and automation evidence must belong to the actual source packet."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev.api.mqtt_gateway import SmappeeMqtt
from custom_components.smappee_ev.mqtt_bootstrap import build_cached_runtime, snapshot_from_runtime
from custom_components.smappee_ev.runtime_assembly import _tracking_serials_for_topologies
from custom_components.smappee_ev.sensor import (
    ConnectorPowerSensor,
    ConnectorSessionEnergySensor,
    SmappeeEVCCStateSensor,
)
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


@pytest.mark.parametrize(
    ("mapping", "expected"),
    [
        ({"11": "house-connect", "22": "charger-connect"}, ["house-connect", "charger-connect"]),
        ({"11": "house-connect"}, ["house-connect"]),
        ({}, []),
        (None, ["legacy-connect", "legacy-connect"]),
    ],
)
async def test_tracking_uses_each_physical_gateway(mapping, expected):
    mqtt = SmappeeMqtt(
        service_location_uuid="site-uuid",
        service_location_uuids=["site-uuid", "charger-uuid"],
        service_location_ids_by_uuid={"site-uuid": 11, "charger-uuid": "22"},
        service_location_id=11,
        serial_number="legacy-connect",
        client_id="test",
        on_properties=MagicMock(),
        tracking_serials=mapping,
    )
    mqtt._client = SimpleNamespace(publish=AsyncMock())
    await mqtt._publish_tracking_once()
    messages = mqtt._client.publish.call_args_list
    assert [json.loads(call.args[1])["serialNumber"] for call in messages] == expected
    assert all(call.args[0].startswith("servicelocation/") for call in messages)


def test_discovery_refuses_conflicting_or_missing_gateway():
    first = SimpleNamespace(
        site_location_id=11,
        site_gateway_serial="house-connect",
        control_location_id=22,
        control_gateway_serial="charger-connect",
    )
    assert _tracking_serials_for_topologies([first]) == {
        "11": "house-connect",
        "22": "charger-connect",
    }
    conflict = SimpleNamespace(**{**vars(first), "control_gateway_serial": "other-connect"})
    assert _tracking_serials_for_topologies([first, conflict]) == {"11": "house-connect"}
    first.control_gateway_serial = None
    assert _tracking_serials_for_topologies([first]) == {"11": "house-connect"}


def test_cached_runtime_preserves_tracking_mapping(hass, entry, online):
    site = online.sites[1]
    site.tracking_serials = {"1": "house-connect", "2": "charger-connect"}
    snapshot = snapshot_from_runtime(online, entry)
    cached = build_cached_runtime(hass, entry, bootstrap.dashboard(), snapshot)
    assert cached.sites[1].tracking_serials == site.tracking_serials
    clients = cached.sites[1].mqtt_clients
    clients = clients if isinstance(clients, list) else [clients]
    assert all(client._tracking_serials == site.tracking_serials for client in clients)


async def test_unchanged_zero_power_updates_exposed_receive_clock(online, monkeypatch):
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    client = bucket.connectors["connector-1"].connector_client
    entity = ConnectorPowerSensor(coord, client, 1, "station-1", "connector-1")
    notify = MagicMock()
    monkeypatch.setattr(coord, "async_update_listeners", notify)
    clock = MagicMock(wraps=datetime)
    monkeypatch.setattr("custom_components.smappee_ev.coordinators.freshness.datetime", clock)
    for second in (0, 10):
        now = datetime(2026, 10, 1, tzinfo=UTC) + timedelta(seconds=second)
        clock.now.return_value = now
        coord.apply_mqtt_properties(bootstrap.TOPIC, {"activePowerData": [0, 0]})
        assert entity.extra_state_attributes["measurement_received"] == now.isoformat()
    assert entity.native_value == 0
    assert notify.call_count == 2
    before = entity.extra_state_attributes
    coord.apply_mqtt_properties(bootstrap.TOPIC, {"currentData": [1000, 1000]})
    assert entity.extra_state_attributes == before


@pytest.mark.parametrize(
    "missing", ["iecStatus", "status", "chargingMode", "optimizationStrategy", "chargingState"]
)
async def test_partial_control_packet_retains_original_complete_observation(online, missing):
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    client = bucket.connectors["connector-1"].connector_client
    entity = SmappeeEVCCStateSensor(coord, client, 1, "station-1", "connector-1")
    topic = "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/connector-1/property/chargingstate"
    complete = {
        "iecStatus": {"current": "C2"},
        "status": {"current": "CHARGING"},
        "chargingMode": "NORMAL",
        "optimizationStrategy": "NONE",
        "chargingState": "STARTED",
    }
    coord.apply_mqtt_properties(topic, complete)
    before = deepcopy(entity.extra_state_attributes)
    partial = {**complete, "chargingState": "SUSPENDED"}
    partial.pop(missing)
    coord.apply_mqtt_properties(topic, partial)
    after = entity.extra_state_attributes
    assert after["control_observation"] == before["control_observation"]
    assert after["measurement_received"] == before["measurement_received"]
    assert before["control_observation"]["charging_mode"] == "NORMAL"


@pytest.mark.parametrize("bad", [None, "", " ", 42, False, {}])
async def test_invalid_control_fields_do_not_create_complete_observation(online, bad):
    coord = online.sites[1].stations["station-1"].station_coordinator
    topic = "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/connector-1/property/chargingstate"
    coord.apply_mqtt_properties(
        topic,
        {
            "iecStatus": {"current": "C2"},
            "status": {"current": "CHARGING"},
            "chargingMode": bad,
            "optimizationStrategy": "NONE",
            "chargingState": "STARTED",
        },
    )
    assert "connector-1" not in coord._complete_control
    assert ("connector-1", "control_complete") not in coord.measurement_freshness.received


async def test_session_query_evidence_advances_only_after_success(online, monkeypatch):
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    client = bucket.connectors["connector-1"].connector_client
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(client, "async_get_recent_sessions", query)
    monkeypatch.setattr(
        "custom_components.smappee_ev.coordinators.session_tracking._now", lambda: 100
    )
    await coord._async_get_recent_sessions()
    assert coord._session_query_success == {"connector-1": 100}
    query.side_effect = RuntimeError("query failed")
    monkeypatch.setattr(
        "custom_components.smappee_ev.coordinators.session_tracking._now", lambda: 200
    )
    with pytest.raises(RuntimeError, match="query failed"):
        await coord._async_get_recent_sessions()
    assert coord._session_query_success == {"connector-1": 100}


async def test_session_entity_update_is_read_only_and_exposes_query_evidence(online, monkeypatch):
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    client = bucket.connectors["connector-1"].connector_client
    entity = ConnectorSessionEnergySensor(coord, client, 1, "station-1", "connector-1")
    refresh = AsyncMock()
    monkeypatch.setattr(coord, "_async_refresh_recent_sessions", refresh)
    await entity.async_update()
    refresh.assert_awaited_once_with("EV controller", force=True)
    coord.data.recent_sessions = [
        {
            "connectorUuid": "connector-1",
            "energy": 1.25,
            "from": 1_781_273_310_000,
            "to": 1_781_273_910_000,
        }
    ]
    coord._session_query_success["connector-1"] = 123
    attrs = entity.extra_state_attributes
    assert attrs["session_queried_at"] == 123
    assert attrs["session_started_at"] == datetime.fromtimestamp(1_781_273_310, UTC).isoformat()
    assert attrs["session_ended_at"] == datetime.fromtimestamp(1_781_273_910, UTC).isoformat()
