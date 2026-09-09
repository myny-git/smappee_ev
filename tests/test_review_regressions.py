"""Behavioral regressions for service targeting, MQTT partial data and site IDs."""

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
import time
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.smappee_ev import (
    async_setup_entry,
    async_unload_entry,
    number,
    sensor,
    services,
)
from custom_components.smappee_ev.api.dashboard_client import (
    SmappeeDashboardClient,
    _retry_after_seconds,
)
from custom_components.smappee_ev.api.errors import SmappeeConnectionError, SmappeeRateLimitError
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.coordinators.power import _pick
from custom_components.smappee_ev.mqtt_bootstrap import (
    async_save_snapshot,
    build_cached_runtime,
    snapshot_from_runtime,
)
from custom_components.smappee_ev.mqtt_recovery import _async_recover
from custom_components.smappee_ev.registry import (
    async_migrate_site_setting_ids,
    site_setting_unique_id,
)
from custom_components.smappee_ev.runtime_lifecycle import _async_shutdown_runtime_resources
from tests import test_mqtt_bootstrap as bootstrap
from tests.test_dashboard_client import _Response, _Session
from tests.test_mqtt_bootstrap import dashboard

entry = bootstrap.entry
online = bootstrap.online


@pytest.mark.parametrize(
    ("service", "extra", "method"),
    [
        ("start_charging", {}, "start_charging"),
        ("pause_charging", {}, "pause_charging"),
        ("stop_charging", {}, "stop_charging"),
        ("resume_charging", {}, "set_charging_mode"),
        ("set_charging_mode", {"mode": "SOLAR"}, "set_charging_mode"),
        ("set_current", {"current": 10}, "set_current"),
    ],
)
async def test_registered_service_requires_unambiguous_station(
    hass, entry, online, service, extra, method
):
    entry.runtime_data = online
    clients = [
        bucket.connectors[f"connector-{index}"].connector_client
        for index, bucket in enumerate(online.sites[1].stations.values(), start=1)
    ]
    for client in clients:
        setattr(client, method, AsyncMock())
    await services.register_services(hass)
    data = {"service_location_id": 1, "connector_id": 1, **extra}
    with patch("custom_components.smappee_ev.services._iter_loaded_entries", return_value=[entry]):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(DOMAIN, service, data, blocking=True)
        assert err.value.translation_key == "ambiguous_connector"
        for client in clients:
            getattr(client, method).assert_not_awaited()
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, service, {**data, "station_serial": "unknown"}, blocking=True
            )
        for client in clients:
            getattr(client, method).assert_not_awaited()
        await hass.services.async_call(
            DOMAIN, service, {**data, "station_serial": "station-2"}, blocking=True
        )
        getattr(clients[0], method).assert_not_awaited()
        getattr(clients[1], method).assert_awaited_once()


@pytest.mark.parametrize("scope", ["site", "station", "connector"])
@pytest.mark.parametrize("metric", ["power", "current", "energy", "combined", "zero"])
async def test_mqtt_partial_messages_preserve_other_measurements(online, scope, metric):
    site = online.sites[1]
    coord = (
        site.site_coordinator if scope == "site" else site.stations["station-1"].station_coordinator
    )
    state = (
        (coord.data.site if scope == "site" else coord.data.station)
        if scope != "connector"
        else coord.data.connectors["connector-1"]
    )
    prefix = "" if scope == "connector" else "grid_"
    energy_attr = "energy_import_kwh" if scope == "connector" else "grid_energy_import_kwh"
    setattr(state, prefix + "power_phases", [100])
    setattr(state, prefix + "power_total", 100)
    setattr(state, prefix + "current_phases", [2.0])
    setattr(state, energy_attr, 3.0)
    topic = "measurements/separate-topic"
    group = {"power": [0], "current": [0], "energy": [0]}
    coord._power_index_maps_by_topic = {
        topic: {
            "grid": group if scope != "connector" else {},
            "pv": {},
            "cars": {"connector-1": group} if scope == "connector" else {},
        }
    }
    payloads = {
        "power": {"activePowerData": [200]},
        "current": {"currentData": [4000]},
        "energy": {"importActiveEnergyData": [5000]},
        "combined": {
            "activePowerData": [200],
            "currentData": [4000],
            "importActiveEnergyData": [5000],
        },
        "zero": {"activePowerData": [0], "currentData": [0], "importActiveEnergyData": [0]},
    }
    coord.apply_mqtt_properties(topic, payloads[metric])
    power = 0 if metric == "zero" else 200 if metric in ("power", "combined") else 100
    current = 0.0 if metric == "zero" else 4.0 if metric in ("current", "combined") else 2.0
    energy = 0.0 if metric == "zero" else 5.0 if metric in ("energy", "combined") else 3.0
    assert getattr(state, prefix + "power_phases") == [power]
    assert getattr(state, prefix + "power_total") == power
    assert getattr(state, prefix + "current_phases") == [current]
    assert getattr(state, energy_attr) == energy


@pytest.mark.parametrize("values", [[], [1], [1, None], [1, "invalid"], [1, float("nan")]])
async def test_incomplete_phase_group_never_invents_zero(online, values):
    coord = online.sites[1].stations["station-1"].station_coordinator
    conn = coord.data.connectors["connector-1"]
    conn.power_phases, conn.power_total = [100, 200], 300
    coord._apply_connector_values(
        conn, {"activePowerData": values, "currentData": [3000, 4000]}, [0, 1], [0, 1], []
    )
    assert conn.power_phases == [100, 200]
    assert conn.power_total == 300
    assert conn.current_phases == [3.0, 4.0]
    assert _pick([0, 0], [0, 1]) == [0, 0]


@pytest.mark.parametrize("metric", ["capacity_maximum_power", "overload_maximum_load"])
async def test_site_setting_migration_preserves_entity_and_customizations(
    hass, entry, online, metric
):
    entry.runtime_data = online
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "number",
        DOMAIN,
        f"1:REMOVED-STATION:site-1:number:{metric}",
        config_entry=entry,
        suggested_object_id="custom_setting",
    )
    registry.async_update_entity(
        old.entity_id, name="My setting", disabled_by=er.RegistryEntryDisabler.USER
    )
    add = MagicMock()
    await number.async_setup_entry(hass, entry, add)
    migrated = registry.async_get(old.entity_id)
    assert migrated.unique_id == site_setting_unique_id(1, metric)
    assert migrated.name == "My setting"
    assert migrated.disabled_by is er.RegistryEntryDisabler.USER
    assert migrated.id == old.id
    before = {
        entity.unique_id for entity in add.call_args.args[0] if entity.translation_key == metric
    }
    original = online.sites[1].stations
    try:
        online.sites[1].stations = {"station-2": original["station-2"]}
        await number.async_setup_entry(hass, entry, add)
        after = {
            entity.unique_id for entity in add.call_args.args[0] if entity.translation_key == metric
        }
        assert before == after == {migrated.unique_id}
    finally:
        online.sites[1].stations = original


async def test_site_setting_migration_keeps_existing_canonical_and_foreign_entries(hass, entry):
    registry = er.async_get(hass)
    canonical = registry.async_get_or_create(
        "number", DOMAIN, site_setting_unique_id(1, "capacity_maximum_power"), config_entry=entry
    )
    legacy = registry.async_get_or_create(
        "number", DOMAIN, "1:OLD:site-1:number:capacity_maximum_power", config_entry=entry
    )
    foreign = registry.async_get_or_create(
        "sensor", DOMAIN, "1:OLD:site-1:number:capacity_maximum_power", config_entry=entry
    )
    async_migrate_site_setting_ids(hass, entry)
    assert registry.async_get(canonical.entity_id).unique_id == canonical.unique_id
    assert registry.async_get(legacy.entity_id).unique_id == legacy.unique_id
    assert registry.async_get(foreign.entity_id).unique_id == foreign.unique_id


@pytest.mark.parametrize("method", ["login", "refresh", "request"])
async def test_rate_limit_blocks_new_requests_across_client_recreation(method):
    response = _Response(429)
    response.headers = {"Retry-After": "120"}
    success = _Response(
        200, {"token": "access", "tokenExpirationTimestamp": int(time.time() * 1000) + 600000}
    )
    session = _Session(posts=[response, success], requests=[response, success])
    api = dashboard(session)
    api._token, api._token_expires_at_ms = "access", int(time.time() * 1000) + 600000

    async def call(client):
        if method == "request":
            return await client._request("GET", "example")
        return await getattr(client, f"async_{method}")()

    with pytest.raises(SmappeeRateLimitError) as err:
        await call(api)
    assert err.value.retry_after == 120
    replacement = SmappeeDashboardClient(
        username="user",
        password="password",  # noqa: S106 - synthetic credential
        refresh_token="refresh",  # noqa: S106 - synthetic credential
        session=session,
        token_update_callback=MagicMock(),
        maintenance_state=api._maintenance_state,
    )
    with pytest.raises(SmappeeRateLimitError):
        await call(replacement)
    assert len(session.request_calls if method == "request" else session.post_calls) == 1
    api._maintenance_state.retry_after_monotonic = 0
    assert await call(api) is True


@pytest.mark.parametrize(
    ("value", "expected"), [(None, 30), ("bad", 30), ("-1", 30), ("0", 0), ("900", 900)]
)
def test_retry_after_values(value, expected):
    assert _retry_after_seconds(value) == expected


def test_retry_after_http_date():
    deadline = datetime.now(UTC) + timedelta(seconds=120)
    assert 118 <= _retry_after_seconds(format_datetime(deadline, usegmt=True)) <= 120


@pytest.mark.parametrize("method", ["login", "refresh", "request"])
async def test_http_408_is_connection_outage(method):
    api = dashboard(_Session(posts=[_Response(408)], requests=[_Response(408)]))
    api._token, api._token_expires_at_ms = "access", int(time.time() * 1000) + 600000
    request = (
        api._request("GET", "example") if method == "request" else getattr(api, f"async_{method}")()
    )
    with pytest.raises(SmappeeConnectionError):
        await request


async def test_recovery_respects_retry_after_beyond_backoff_cap(hass, entry, online):
    cached = build_cached_runtime(hass, entry, dashboard(), snapshot_from_runtime(online, entry))
    entry.runtime_data = cached
    prepare = AsyncMock(side_effect=[SmappeeRateLimitError(1200), asyncio.CancelledError()])
    try:
        with patch(
            "custom_components.smappee_ev.mqtt_recovery.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            with pytest.raises(asyncio.CancelledError):
                await _async_recover(hass, entry, cached, prepare)
            assert [call.args[0] for call in sleep.await_args_list] == [30, 1200]
        assert not cached.stopping
    finally:
        await _async_shutdown_runtime_resources(cached)


async def test_cached_current_topic_is_routed_and_marks_connector_fresh(hass, entry, online):
    site = online.sites[1]
    topic = "servicelocation/site-uuid/current"
    channel = {
        "protocol": "MQTT",
        "name": topic,
        "userName": "mqtt-user",
        "password": "mqtt-secret",
        "aspectPaths": [{"path": "$.currentData[0]"}],
    }
    site.highlevel_configs[1]["measurements"][1]["updateChannels"]["current"] = channel
    for index, bucket in enumerate(site.stations.values(), start=1):
        bucket.station_coordinator._power_index_maps_by_topic[topic] = {
            "grid": {},
            "pv": {},
            "cars": {f"connector-{index}": {"current": [index - 1]}},
        }
    cached = build_cached_runtime(hass, entry, dashboard(), snapshot_from_runtime(online, entry))
    try:
        bucket = cached.sites[1].stations["station-1"]
        entity = sensor.ConnectorPowerSensor(
            bucket.station_coordinator,
            bucket.connectors["connector-1"].connector_client,
            1,
            "station-1",
            "connector-1",
        )
        mqtt = cached.mqtt[1]
        mqtt._on_conn(True)
        assert not entity.available
        mqtt._on_properties(topic, {"currentData": [4000, 5000]})
        assert entity.available
        conn = bucket.station_coordinator.data.connectors["connector-1"]
        assert conn.current_phases == [4.0]
        assert conn.power_total is None
    finally:
        await _async_shutdown_runtime_resources(cached)


@pytest.mark.parametrize("status", [408, 429])
async def test_http_outage_during_real_discovery_uses_mqtt_cache(
    hass, entry, online, hass_storage, status
):
    await async_save_snapshot(hass, entry, online)
    response = _Response(status)
    response.headers = {"Retry-After": "120"}
    session = _Session(posts=[response])
    with (
        patch("custom_components.smappee_ev.async_get_clientsession", return_value=session),
        patch("custom_components.smappee_ev._start_runtime_background_work"),
        patch("custom_components.smappee_ev.start_recovery"),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch.object(hass.config_entries, "async_unload_platforms", return_value=True),
    ):
        assert await async_setup_entry(hass, entry)
        assert entry.runtime_data.mode.value == "mqtt_only"
        assert await async_unload_entry(hass, entry)


@pytest.mark.parametrize("scope", ["site", "station"])
@pytest.mark.parametrize("voltages", [[2300], [2300, 2310, 2320]])
async def test_voltage_only_accepts_reported_phases_without_zero_fill(online, scope, voltages):
    site = online.sites[1]
    coord = (
        site.site_coordinator if scope == "site" else site.stations["station-1"].station_coordinator
    )
    state = coord.data.site if scope == "site" else coord.data.station
    topic = "servicelocation/site/voltage"
    coord._power_index_maps_by_topic = {topic: {"grid": {}, "pv": {}, "cars": {}}}
    coord.apply_mqtt_properties(topic, {"phaseVoltageData": voltages})
    assert state.grid_voltage_phases == [value // 10 for value in voltages]
    coord.apply_mqtt_properties(topic, {})
    assert state.grid_voltage_phases == [value // 10 for value in voltages]
