"""Outage bootstrap, persistence, control isolation and Dashboard recovery."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.smappee_ev import (
    _async_prepare_runtime,
    _bootstrap_error_priority,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
    button,
    light,
    number,
    select,
    sensor,
    services,
    switch,
)
from custom_components.smappee_ev.api.dashboard_client import SmappeeDashboardClient
from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.api.errors import (
    SmappeeConnectionError,
    SmappeeMaintenanceError,
    SmappeeProtocolError,
    SmappeeServerError,
)
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.coordinator import (
    SmappeeSiteCoordinator,
    SmappeeStationCoordinator,
)
from custom_components.smappee_ev.models.runtime_data import (
    RuntimeData,
    RuntimeMode,
    SmappeeConnectorRuntime,
    SmappeeSiteRuntime,
    SmappeeStationRuntime,
)
from custom_components.smappee_ev.models.state import (
    ConnectorState,
    IntegrationData,
    SiteData,
    SiteState,
    StationState,
)
from custom_components.smappee_ev.mqtt_bootstrap import (
    async_load_snapshot,
    async_save_snapshot,
    bootstrap_store,
    build_cached_runtime,
    snapshot_from_runtime,
)
from custom_components.smappee_ev.mqtt_recovery import (
    _async_recover,
    _schedule_reload,
    start_recovery,
)
from custom_components.smappee_ev.runtime_lifecycle import _async_shutdown_runtime_resources
from tests.test_dashboard_client import _Response, _Session

TOPIC = "servicelocation/site-uuid/power"


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=6,
        unique_id="smappee_ev:user",
        data={"username": "user", "password": "password", "dashboard_refresh_token": "refresh"},
    )
    entry.add_to_hass(hass)
    return entry


def dashboard(session=None):
    return SmappeeDashboardClient(
        username="user",
        password="password",  # noqa: S106 - synthetic credential
        refresh_token="refresh",  # noqa: S106
        session=session or MagicMock(),
        token_update_callback=MagicMock(),
    )


@pytest.fixture
async def online(hass, entry):
    """Real coordinators with representative normalized discovery, no remote I/O."""
    api = dashboard()
    site = SmappeeSiteRuntime(
        site_location_id=1,
        site_name="Home",
        site_function_type="LOCATION",
        site_uuid="site-uuid",
        gateway_serial="gateway",
        gateway_type="GENIUS",
        control_location_ids=[1],
        measurement_location_ids=[1],
    )
    site_coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=1,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="gateway",
        gateway_type="GENIUS",
        update_interval=30,
        config_entry=entry,
    )
    site_coord.async_set_updated_data(SiteData(site=SiteState()))
    site.site_coordinator = site_coord
    channel = {
        "protocol": "MQTT",
        "name": TOPIC,
        "userName": "mqtt-user",
        "password": "mqtt-secret",
        "aspectPaths": [{"path": "$.activePowerData[0]"}],
    }
    site.highlevel_configs = {
        1: {
            "measurements": [
                {"type": "GRID", "updateChannels": {"activePower": channel}},
                {
                    "type": "APPLIANCE",
                    "appliance": {"type": "CAR_CHARGER"},
                    "updateChannels": {"activePower": channel},
                },
            ]
        }
    }
    for index in (1, 2):
        key = f"station-{index}"
        ckey = f"connector-{index}"
        client = SmappeeDeviceHandle(
            "gateway", key, key, 1, is_station=True, charging_station_serial=key, site_location_id=1
        )
        connector_client = SmappeeDeviceHandle(
            "gateway",
            ckey,
            ckey,
            1,
            connector_number=1,
            charging_station_serial=key,
            site_location_id=1,
        )
        coord = SmappeeStationCoordinator(
            hass,
            client,
            {ckey: connector_client},
            30,
            config_entry=entry,
            dashboard_client=api,
            site_name="Home",
            gateway_serial="gateway",
        )
        coord.async_set_updated_data(
            IntegrationData(
                station=StationState(), connectors={ckey: ConnectorState(connector_number=1)}
            )
        )
        coord._power_index_maps_by_topic = {
            TOPIC: {
                "grid": {},
                "pv": {},
                "cars": {
                    ckey: {
                        "position": 1,
                        "serial": None,
                        "power": [index],
                        "power_field": "activePowerData",
                    }
                },
            }
        }
        bucket = SmappeeStationRuntime(
            site_location_id=1,
            control_location_id=1,
            site_name="Home",
            gateway_serial="gateway",
            gateway_type="GENIUS",
            control_name="Home",
            control_uuid="site-uuid",
            control_function_type="LOCATION",
            station_name=key,
            charging_station_serial=key,
            charging_station_model="EV Wall",
            station_client=client,
            station_coordinator=coord,
            site_coordinator=site_coord,
            connectors={ckey: SmappeeConnectorRuntime(ckey, ckey, 1, connector_client)},
        )
        site.stations[key] = bucket
    site_coord._power_index_maps_by_topic = {
        TOPIC: {"grid": {"power": [0], "power_field": "activePowerData"}, "pv": {}, "cars": {}}
    }
    runtime = RuntimeData(api=api, dashboard=api, sites={1: site}, mqtt={})
    yield runtime
    await _async_shutdown_runtime_resources(runtime)


@pytest.fixture
def snapshot(online, entry):
    return snapshot_from_runtime(online, entry)


async def test_private_store_roundtrip_and_entry_removal(
    hass, entry, online, snapshot, hass_storage
):
    await async_save_snapshot(hass, entry, online)
    loaded = await async_load_snapshot(hass, entry)
    assert loaded["sites"] == snapshot["sites"]
    store = bootstrap_store(hass, entry)
    assert store._private is True
    assert store._atomic_writes is True
    assert "mqtt-secret" in json.dumps(loaded)
    assert '"recent_sessions"' not in json.dumps(loaded)
    assert '"password": "password"' not in json.dumps(loaded)
    await async_remove_entry(hass, entry)
    assert await async_load_snapshot(hass, entry) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(schema_version=2),
        lambda s: s.update(entry_id="other"),
        lambda s: s.update(account="other"),
        lambda s: s.update(fetched_at="not-a-date"),
        lambda s: s.update(sites=[]),
        lambda s: s["sites"].append(deepcopy(s["sites"][0])),
        lambda s: s["sites"][0].update(specs=[]),
        lambda s: s["sites"][0].update(stations={}),
        lambda s: s["sites"][0].update(power_maps={TOPIC: {"cars": {"unknown": {}}}}),
        lambda s: s["sites"][0]["power_maps"][TOPIC]["grid"].update(power=[-1]),
        lambda s: s["sites"][0]["stations"]["station-1"]["metadata"].update(site_location_id=9),
        lambda s: s["sites"][0]["stations"]["station-1"]["connectors"]["connector-1"][
            "metadata"
        ].update(connector_key="wrong"),
    ],
)
async def test_invalid_snapshot_is_ignored(hass, entry, snapshot, change, hass_storage, caplog):
    change(snapshot)
    await bootstrap_store(hass, entry).async_save(snapshot)
    assert await async_load_snapshot(hass, entry) is None
    assert all(
        "mqtt-secret" not in record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.smappee_ev")
    )


async def test_incomplete_bootstrap_keeps_previous_snapshot(hass, entry, online, hass_storage):
    await async_save_snapshot(hass, entry, online)
    before = await async_load_snapshot(hass, entry)
    online.sites[1].stations["station-1"].station_coordinator.data.station.api_available = False
    await async_save_snapshot(hass, entry, online)
    assert await async_load_snapshot(hass, entry) == before


async def test_cached_runtime_routes_live_data_and_preserves_ids(hass, entry, snapshot, online):
    api = dashboard()
    cached = build_cached_runtime(hass, entry, api, snapshot)
    entry.runtime_data = cached
    try:
        mqtt = cached.mqtt[1]
        assert len(mqtt._mqtt_specs) == 2
        site = cached.sites[1]
        for key, bucket in site.stations.items():
            ckey, connector = next(iter(bucket.connectors.items()))
            entity = sensor.ConnectorPowerSensor(
                bucket.station_coordinator, connector.connector_client, 1, key, ckey
            )
            old = online.sites[1].stations[key]
            old_entity = sensor.ConnectorPowerSensor(
                old.station_coordinator, old.connectors[ckey].connector_client, 1, key, ckey
            )
            assert entity.unique_id == old_entity.unique_id
            assert entity.available is False
            assert bucket.station_coordinator.update_interval is None
            assert not bucket.station_coordinator._session_tracking_started
        mqtt._on_conn(True)
        assert entity.available is False  # Broker connection alone is insufficient.
        mqtt._on_properties(TOPIC, {"activePowerData": [100, 200, 300]})
        assert site.site_coordinator.data.site.grid_power_total == 100
        assert (
            site.stations["station-1"]
            .station_coordinator.data.connectors["connector-1"]
            .power_total
            == 200
        )
        assert entity.available is True
        assert entity.native_value == 300
        with patch(
            "custom_components.smappee_ev.entity._utcnow",
            return_value=datetime.now(UTC) + timedelta(minutes=6),
        ):
            assert entity.available is False
        mqtt._on_conn(False)
        assert entity.available is False
        assert bucket.station_coordinator.update_interval is None
        api._session.request.assert_not_called()
    finally:
        await _async_shutdown_runtime_resources(cached)


@pytest.mark.parametrize("platform", [button, light, number, select, switch])
async def test_all_controls_unavailable_in_cached_runtime(hass, entry, snapshot, platform):
    cached = build_cached_runtime(hass, entry, dashboard(), snapshot)
    entry.runtime_data = cached
    try:
        add = MagicMock()
        await platform.async_setup_entry(hass, entry, add)
        entities = add.call_args.args[0]
        assert entities
        assert all(not entity.available for entity in entities)
    finally:
        await _async_shutdown_runtime_resources(cached)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("set_charging_mode", ("STANDARD",)),
        ("pause_charging", ()),
        ("stop_charging", ()),
        ("set_brightness", (10,)),
        ("set_available", ()),
        ("set_unavailable", ()),
        ("restart_charging_station", ()),
        ("set_offline_charging_config", (True, 6)),
    ],
)
async def test_direct_handle_commands_blocked_before_io(hass, entry, snapshot, method, args):
    api = dashboard()
    cached = build_cached_runtime(hass, entry, api, snapshot)
    try:
        client = cached.sites[1].stations["station-1"].station_client
        with pytest.raises(HomeAssistantError) as err:
            await getattr(client, method)(*args)
        assert err.value.translation_key == "mqtt_only"
        api._session.request.assert_not_called()
        api._session.post.assert_not_called()
    finally:
        await _async_shutdown_runtime_resources(cached)


async def test_domain_service_and_site_number_blocked(hass, entry, snapshot):
    api = dashboard()
    cached = build_cached_runtime(hass, entry, api, snapshot)
    entry.runtime_data = cached
    entry.mock_state(hass, ConfigEntryState.LOADED)
    try:
        call = ServiceCall(
            domain=DOMAIN, service="start_charging", data={"connector_id": 1}, hass=hass
        )
        with pytest.raises(HomeAssistantError) as err:
            await services.handle_start_charging(call)
        assert err.value.translation_key == "mqtt_only"
        for cls in (
            number.SmappeeCapacityMaximumPowerNumber,
            number.SmappeeOverloadMaximumLoadNumber,
        ):
            entity = cls(
                coordinator=cached.sites[1].stations["station-1"].station_coordinator, sid=1
            )
            with pytest.raises(HomeAssistantError):
                await entity.async_set_native_value(5)
        api._session.request.assert_not_called()
    finally:
        await _async_shutdown_runtime_resources(cached)


@pytest.mark.parametrize(
    "error", [SmappeeMaintenanceError, SmappeeConnectionError, SmappeeServerError]
)
async def test_setup_outage_uses_saved_snapshot(hass, entry, online, error, hass_storage):
    await async_save_snapshot(hass, entry, online)
    with (
        patch("custom_components.smappee_ev._async_prepare_runtime", side_effect=error("outage")),
        patch("custom_components.smappee_ev._start_mqtt_clients") as start,
        patch("custom_components.smappee_ev.start_recovery") as recover,
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch.object(hass.config_entries, "async_unload_platforms", return_value=True),
    ):
        assert await async_setup_entry(hass, entry)
        assert entry.runtime_data.mode is RuntimeMode.MQTT_ONLY
        start.assert_called_once()
        recover.assert_called_once()
        assert await async_unload_entry(hass, entry)
        assert entry.runtime_data.stopping


@pytest.mark.parametrize(
    "error",
    [
        SmappeeMaintenanceError,
        SmappeeConnectionError,
        SmappeeServerError,
        ConfigEntryAuthFailed,
        SmappeeProtocolError,
    ],
)
async def test_no_snapshot_or_disallowed_error_never_starts_mqtt(hass, entry, error):
    with (
        patch("custom_components.smappee_ev._async_prepare_runtime", side_effect=error("failed")),
        patch("custom_components.smappee_ev._start_mqtt_clients") as start,
    ):
        with pytest.raises((ConfigEntryNotReady, ConfigEntryAuthFailed, SmappeeProtocolError)):
            await async_setup_entry(hass, entry)
        start.assert_not_called()


async def test_normal_setup_saves_bootstrap(hass, entry, online, hass_storage):
    with (
        patch("custom_components.smappee_ev._async_prepare_runtime", return_value=online),
        patch("custom_components.smappee_ev._start_runtime_background_work"),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await async_setup_entry(hass, entry)
    assert entry.runtime_data.mode is RuntimeMode.NORMAL
    assert await async_load_snapshot(hass, entry) is not None


async def test_recovery_requires_full_discovery_then_schedules_one_reload(
    hass, entry, snapshot, online
):
    cached = build_cached_runtime(hass, entry, dashboard(), snapshot)
    entry.runtime_data = cached
    prepare = AsyncMock(side_effect=[SmappeeServerError("offline"), online])
    try:
        with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
            with patch(
                "custom_components.smappee_ev.mqtt_recovery.asyncio.sleep", new=AsyncMock()
            ) as sleep:
                await _async_recover(hass, entry, cached, prepare)
                assert prepare.await_count == 2
                assert sleep.await_args_list[0].args == (30.0,)
            await asyncio.sleep(0)
            await hass.async_block_till_done()
            reload.assert_awaited_once_with(entry.entry_id)
            assert online.stopping  # Probe resources closed before reload.
    finally:
        await _async_shutdown_runtime_resources(cached)


async def test_recovery_auth_failure_starts_reauth_and_keeps_monitoring(hass, entry, snapshot):
    cached = build_cached_runtime(hass, entry, dashboard(), snapshot)
    entry.runtime_data = cached
    try:
        with (
            patch("custom_components.smappee_ev.mqtt_recovery.asyncio.sleep", new=AsyncMock()),
            patch.object(entry, "async_start_reauth_if_available") as reauth,
            patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload,
        ):
            await _async_recover(
                hass, entry, cached, AsyncMock(side_effect=ConfigEntryAuthFailed())
            )
            reauth.assert_called_once_with(hass)
            reload.assert_not_awaited()
            assert not cached.stopping
    finally:
        await _async_shutdown_runtime_resources(cached)


async def test_unload_cancels_recovery_and_freshness_callbacks(hass, entry, snapshot):
    cached = build_cached_runtime(hass, entry, dashboard(), snapshot)
    entry.runtime_data = cached
    probe = AsyncMock()
    start_recovery(hass, entry, cached, probe)
    tasks = list(cached.background_tasks)
    assert len(tasks) == 1
    await _async_shutdown_runtime_resources(cached)
    assert all(task.done() for task in tasks)
    assert not cached.cleanup_callbacks
    probe.assert_not_awaited()
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        _schedule_reload(hass, entry, cached)
        reload.assert_not_awaited()


@pytest.mark.parametrize("failure", [aiohttp.ClientConnectionError, TimeoutError])
@pytest.mark.parametrize("method", ["login", "refresh", "request"])
async def test_transport_failure_entering_response_propagates(failure, method):
    context = MagicMock()
    context.__aenter__ = AsyncMock(side_effect=failure("private-network-details"))
    session = MagicMock()
    session.post.return_value = session.request.return_value = context
    api = dashboard(session)
    api.async_ensure_auth = AsyncMock(return_value=True)
    request = (
        api._request("GET", "example") if method == "request" else getattr(api, f"async_{method}")()
    )
    with pytest.raises(SmappeeConnectionError) as err:
        await request
    assert "private-network-details" not in str(err.value)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_http_outage_does_not_report_setting_success(status):
    api = dashboard(_Session(posts=[_Response(status, text="private-server-body")]))
    with pytest.raises(SmappeeServerError) as err:
        await api.async_set_capacity_protection(1, True, 5)
    assert "private-server-body" not in str(err.value)


async def test_invalid_login_content_type_is_not_a_connection_outage():
    response = _Response(200, text="unexpected HTML")
    response.json = AsyncMock(side_effect=aiohttp.ContentTypeError(MagicMock(), ()))
    api = dashboard(_Session(posts=[response]))
    with pytest.raises(SmappeeProtocolError):
        await api.async_login()


@pytest.mark.parametrize("reverse", [False, True])
async def test_bootstrap_auth_wins_over_outage_and_success_is_cleaned_up(
    hass, entry, online, reverse
):
    topology_a, topology_b = MagicMock(site_location_id=1), MagicMock(site_location_id=2)
    outcomes = [SmappeeMaintenanceError("maintenance"), ConfigEntryAuthFailed("reauth")]
    if reverse:
        outcomes.reverse()
    with (
        patch(
            "custom_components.smappee_ev._load_dashboard_topologies",
            return_value=[topology_a, topology_b],
        ),
        patch("custom_components.smappee_ev._prepare_site_topologies", side_effect=outcomes),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await _async_prepare_runtime(hass, entry, dashboard())
    assert _bootstrap_error_priority(asyncio.CancelledError()) < _bootstrap_error_priority(
        ConfigEntryAuthFailed()
    )
    assert _bootstrap_error_priority(ValueError()) < _bootstrap_error_priority(SmappeeServerError())


async def test_entry_loaded_through_outage_reload_and_normal_recovery(
    hass, entry, online, hass_storage, enable_custom_integrations
):
    """Exercise real HA platform setup/unload and stable registry identities."""
    from homeassistant.helpers import entity_registry as er

    await async_save_snapshot(hass, entry, online)
    with (
        patch(
            "custom_components.smappee_ev._async_prepare_runtime",
            side_effect=SmappeeMaintenanceError("maintenance"),
        ) as prepare,
        patch("custom_components.smappee_ev.api.mqtt_gateway.SmappeeMqtt.start", new=AsyncMock()),
        patch("custom_components.smappee_ev.start_recovery"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.mode is RuntimeMode.MQTT_ONLY
        registry = er.async_get(hass)
        before = {
            e.unique_id: e.entity_id
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        }
        assert len(before) > 20
        assert all(
            state.state == "unavailable"
            for state in hass.states.async_all()
            if state.domain in {"button", "light", "number", "select", "switch"}
        )
        old = entry.runtime_data
        assert await hass.config_entries.async_reload(entry.entry_id)
        assert old.stopping
        assert entry.runtime_data.mode is RuntimeMode.MQTT_ONLY
        # Dashboard recovery commits the normal runtime through the same reload path.
        prepare.side_effect = None
        prepare.return_value = online
        for station in online.sites[1].stations.values():
            station.station_coordinator.async_start_session_tracking = MagicMock()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.mode is RuntimeMode.NORMAL
        after = {
            e.unique_id: e.entity_id
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        }
        assert before == after
        assert any(
            state.state != "unavailable"
            for state in hass.states.async_all()
            if state.domain == "number"
        )
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cached_multi_credential_connections_require_all_transports(hass, entry, snapshot):
    snapshot["sites"][0]["specs"][1]["username"] = "second-user"
    snapshot["sites"][0]["specs"][1]["password"] = "second-secret"  # noqa: S105 - synthetic credential
    cached = build_cached_runtime(hass, entry, dashboard(), snapshot)
    try:
        clients = cached.mqtt[1]
        assert len(clients) == 2
        clients[0]._on_conn(True)
        coord = cached.sites[1].site_coordinator
        assert not coord.mqtt_transport_connected
        clients[1]._on_conn(True)
        assert coord.mqtt_transport_connected
        clients[0]._on_conn(False)
        assert not coord.mqtt_transport_connected
    finally:
        await _async_shutdown_runtime_resources(cached)


async def test_cached_diagnostics_redact_mqtt_credentials(hass, entry, snapshot):
    from custom_components.smappee_ev.diagnostics import async_get_config_entry_diagnostics

    cached = build_cached_runtime(hass, entry, dashboard(), snapshot)
    entry.runtime_data = cached
    try:
        data = await async_get_config_entry_diagnostics(hass, entry)
        assert data["runtime_mode"] == "mqtt_only"
        text = json.dumps(data)
        assert "mqtt-secret" not in text
        assert "mqtt-user" not in text
        assert TOPIC not in text
    finally:
        await _async_shutdown_runtime_resources(cached)


async def test_live_mqtt_updates_preserve_scheduled_rest_poll(hass, online):
    """Frequent push updates must not postpone the poll that detects REST recovery."""
    coord = online.sites[1].stations["station-1"].station_coordinator
    with patch.object(coord, "_async_update_data", new=AsyncMock(return_value=coord.data)) as poll:
        remove_listener = coord.async_add_listener(MagicMock())
        try:
            scheduled_poll = coord._unsub_refresh
            assert scheduled_poll is not None
            for power in (200, 201, 202):
                coord.apply_mqtt_properties(TOPIC, {"activePowerData": [100, power, 300]})
                assert coord._unsub_refresh is scheduled_poll
            async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=31))
            await hass.async_block_till_done()
            poll.assert_awaited_once()
        finally:
            remove_listener()


async def test_running_coordinator_keeps_mqtt_during_outage_and_recovers(hass, online):
    """Real REST merge and entity availability across an outage without a reload."""
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    connector = bucket.connectors["connector-1"]
    power = sensor.ConnectorPowerSensor(
        coord, connector.connector_client, 1, "station-1", "connector-1"
    )
    control = button.SmappeeStationActionButton(
        coordinator=coord,
        api_client=bucket.station_client,
        sid=1,
        station_uuid="station-1",
        action="restart_charging_station",
    )
    coord.apply_mqtt_connection_change(True)
    with patch.object(
        online.dashboard, "_request", new=AsyncMock(side_effect=SmappeeServerError("outage"))
    ) as request:
        await coord.async_refresh()
        assert not control.available
        coord.apply_mqtt_properties(TOPIC, {"activePowerData": [100, 200, 300]})
        assert power.available
        assert power.native_value == 200
        assert not coord.data.connectors["connector-1"].api_available
        request.side_effect = lambda _method, path, **_kwargs: (
            [{"id": "connector-1"}] if path.endswith("/smart/devices") else {}
        )
        await coord.async_refresh()
        assert control.available
        assert coord.data.connectors["connector-1"].api_available
        assert power.available
        assert power.native_value == 200


async def test_cancelled_discovery_cleans_up_already_prepared_site(hass, entry, online):
    """Cancel recovery while one site is ready and another still awaits Dashboard."""
    pending_started = asyncio.Event()

    async def prepare_site(_hass, topologies, *_args, **_kwargs):
        if topologies[0].site_location_id == 1:
            return online.sites[1], None
        pending_started.set()
        await asyncio.Event().wait()
        return None

    with (
        patch(
            "custom_components.smappee_ev._load_dashboard_topologies",
            return_value=[MagicMock(site_location_id=1), MagicMock(site_location_id=2)],
        ),
        patch("custom_components.smappee_ev._prepare_site_topologies", side_effect=prepare_site),
    ):
        task = asyncio.create_task(_async_prepare_runtime(hass, entry, dashboard()))
        await pending_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    for station in online.sites[1].stations.values():
        assert station.station_coordinator._shutting_down
        assert station.station_coordinator._shutdown_requested
