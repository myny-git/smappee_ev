"""Run Dashboard recovery through real discovery, coordinators and cleanup."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed
import pytest

from custom_components.smappee_ev import _async_prepare_runtime
from custom_components.smappee_ev.api.errors import SmappeeServerError
from custom_components.smappee_ev.models.runtime_data import RuntimeMode
from custom_components.smappee_ev.mqtt_bootstrap import build_cached_runtime
from custom_components.smappee_ev.mqtt_recovery import _async_recover
from custom_components.smappee_ev.mqtt_setup import _iter_mqtt_clients
from custom_components.smappee_ev.runtime_lifecycle import _async_shutdown_runtime_resources
from custom_components.smappee_ev.state_updates import update_connector
from tests import test_mqtt_bootstrap as bootstrap
from tests.test_functional_flows import _FakeDashboard

entry = bootstrap.entry
online = bootstrap.online
snapshot = bootstrap.snapshot


@pytest.fixture
def recovery_dashboard():
    """Mock only the external Dashboard boundary, retaining realistic payloads."""
    client = _FakeDashboard()
    client.async_get_capacity_protection = AsyncMock(return_value={})
    client.async_get_overload_protection = AsyncMock(return_value={})
    client.async_get_appliances = AsyncMock(return_value=[])
    client.async_get_load_management = AsyncMock(return_value={"optimizationStrategy": "NONE"})
    return client


@pytest.fixture
def probe_coordinators(monkeypatch):
    """Observe real coordinator construction and refresh without substituting it."""
    from custom_components.smappee_ev import runtime_assembly

    created = []
    refresh = runtime_assembly._async_initial_refresh

    async def observe(coord):
        created.append(coord)
        await refresh(coord)

    monkeypatch.setattr(runtime_assembly, "_async_initial_refresh", observe)
    return created


def assert_probe_closed(coordinators):
    assert coordinators
    for coord in coordinators:
        assert coord.config_entry is None
        assert coord._shutdown_requested
        assert coord._unsub_refresh is None
        assert coord._freshness_unsub is None
        assert coord._request_refresh_task is None
        if hasattr(coord, "_session_tracking_started"):
            assert not coord._session_tracking_started
            assert coord._dashboard_refresh_task is None


@pytest.mark.parametrize("first_failure", [None, "station", "connector", "discovery"])
async def test_loaded_entry_recovers_through_real_prepare_and_closes_probes_before_reload(
    hass, entry, snapshot, recovery_dashboard, probe_coordinators, first_failure
):
    """A failed probe must preserve MQTT; a later complete probe reloads once."""
    cached = build_cached_runtime(hass, entry, bootstrap.dashboard(), snapshot)
    entry.runtime_data = cached
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    initial_states = [
        deepcopy(bucket.station_coordinator.data)
        for site in cached.sites.values()
        for bucket in site.stations.values()
    ]
    healthy_devices = deepcopy(recovery_dashboard.async_get_smart_devices.return_value)
    attempts = 0

    async def next_probe(_delay):
        nonlocal attempts
        if attempts and probe_coordinators:
            assert_probe_closed(probe_coordinators)
            assert not cached.stopping
        attempts += 1
        # Each probe fetches devices for discovery, then once more for shared
        # station/connector polling. Restore normal responses before retrying.
        devices = recovery_dashboard.async_get_smart_devices
        devices.side_effect = None
        devices.return_value = healthy_devices
        recovery_dashboard.async_get_service_locations_full_details.side_effect = None
        if attempts == 1:
            if first_failure == "station":
                devices.side_effect = [healthy_devices, SmappeeServerError("REST offline")]
            elif first_failure == "connector":
                devices.side_effect = [healthy_devices, [healthy_devices[0]]]
            elif first_failure == "discovery":
                recovery_dashboard.async_get_service_locations_full_details.side_effect = (
                    SmappeeServerError("discovery offline")
                )
        assert attempts <= 2, "A recovered Dashboard must not keep retrying"

    async def reload_after_cleanup(entry_id):
        assert entry_id == entry.entry_id
        assert_probe_closed(probe_coordinators)
        assert entry.runtime_data is cached
        assert not cached.stopping
        return True

    try:
        with (
            patch("custom_components.smappee_ev.mqtt_recovery.asyncio", wraps=asyncio) as clock,
            patch(
                "custom_components.smappee_ev.mqtt_recovery._create_dashboard_client",
                return_value=recovery_dashboard,
            ),
            patch.object(entry, "async_on_unload", wraps=entry.async_on_unload) as on_unload,
            patch.object(
                hass.config_entries, "async_reload", side_effect=reload_after_cleanup
            ) as reload,
            patch(
                "custom_components.smappee_ev.api.mqtt_gateway.SmappeeMqtt.start", new=AsyncMock()
            ) as mqtt_start,
        ):
            clock.sleep = AsyncMock(side_effect=next_probe)
            await _async_recover(hass, entry, cached, _async_prepare_runtime)
            await hass.async_block_till_done()
            reload.assert_awaited_once_with(entry.entry_id)
            assert attempts == (1 if first_failure is None else 2)
            on_unload.assert_not_called()
            mqtt_start.assert_not_awaited()
            assert cached.mode is RuntimeMode.MQTT_ONLY
            assert [
                bucket.station_coordinator.data
                for site in cached.sites.values()
                for bucket in site.stations.values()
            ] == initial_states
            for mqtt in cached.mqtt.values():
                assert all(not client._stop.is_set() for client in _iter_mqtt_clients(mqtt))
    finally:
        await _async_shutdown_runtime_resources(cached)


@pytest.mark.parametrize("phase", ["site", "station"])
async def test_cancelled_real_prepare_closes_every_created_coordinator(
    hass, entry, snapshot, recovery_dashboard, probe_coordinators, monkeypatch, phase
):
    """Cancellation before a runtime is returned still closes temporary resources."""
    from custom_components.smappee_ev.coordinator import SmappeeSiteCoordinator

    cached = build_cached_runtime(hass, entry, bootstrap.dashboard(), snapshot)
    entry.runtime_data = cached
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    started = asyncio.Event()
    blocked = asyncio.Event()
    if phase == "site":

        async def wait_for_power_map(_coord):
            started.set()
            await blocked.wait()

        monkeypatch.setattr(SmappeeSiteCoordinator, "_ensure_power_index_map", wait_for_power_map)
    else:
        devices = recovery_dashboard.async_get_smart_devices.return_value
        calls = 0

        async def wait_for_rest(_sid):
            nonlocal calls
            calls += 1
            if calls == 2:
                started.set()
                await blocked.wait()
            return devices

        recovery_dashboard.async_get_smart_devices.side_effect = wait_for_rest
    task = asyncio.create_task(_async_prepare_runtime(hass, entry, recovery_dashboard))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_probe_closed(probe_coordinators)
        assert len(probe_coordinators) == (1 if phase == "site" else 2)
        assert entry.runtime_data is cached
        assert not cached.stopping
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await _async_shutdown_runtime_resources(cached)


async def test_real_recovery_auth_failure_cleans_up_and_starts_one_reauth(
    hass, entry, snapshot, recovery_dashboard, probe_coordinators
):
    """A probe auth failure propagates through real refresh and leaves MQTT live."""
    cached = build_cached_runtime(hass, entry, bootstrap.dashboard(), snapshot)
    entry.runtime_data = cached
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    devices = recovery_dashboard.async_get_smart_devices.return_value
    recovery_dashboard.async_get_smart_devices.side_effect = [
        devices,
        ConfigEntryAuthFailed("refresh token expired"),
    ]
    try:
        with (
            patch("custom_components.smappee_ev.mqtt_recovery.asyncio", wraps=asyncio) as clock,
            patch(
                "custom_components.smappee_ev.mqtt_recovery._create_dashboard_client",
                return_value=recovery_dashboard,
            ),
            patch.object(entry, "async_start_reauth_if_available") as reauth,
            patch.object(entry, "async_on_unload", wraps=entry.async_on_unload) as on_unload,
            patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload,
        ):
            clock.sleep = AsyncMock()
            await _async_recover(hass, entry, cached, _async_prepare_runtime)
            await hass.async_block_till_done()
            reauth.assert_called_once_with(hass)
            assert cached.recovery.status == "reauth_required"
            assert cached.recovery.last_error_category == "authentication"
            assert cached.recovery.next_attempt is None
            assert cached.recovery.attempts == 1
            reload.assert_not_awaited()
            on_unload.assert_not_called()
            assert_probe_closed(probe_coordinators)
            assert not cached.stopping
            assert entry.runtime_data is cached
    finally:
        await _async_shutdown_runtime_resources(cached)


async def test_normal_setup_keeps_entry_and_uses_homeassistant_first_refresh(
    hass, entry, recovery_dashboard, probe_coordinators, monkeypatch
):
    """Fixing a loaded recovery entry must retain HA's setup refresh semantics."""
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    entry._async_set_state(hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
    first_refreshes = []
    refresh = DataUpdateCoordinator.async_config_entry_first_refresh

    async def observe_first_refresh(coord):
        first_refreshes.append(coord)
        await refresh(coord)

    monkeypatch.setattr(
        DataUpdateCoordinator, "async_config_entry_first_refresh", observe_first_refresh
    )
    runtime = await _async_prepare_runtime(hass, entry, recovery_dashboard)
    try:
        assert len(first_refreshes) == 2
        assert first_refreshes == probe_coordinators
        assert all(coord.config_entry is entry for coord in first_refreshes)
        assert all(coord.last_update_success for coord in first_refreshes)
        assert runtime.mode is RuntimeMode.NORMAL
    finally:
        await _async_shutdown_runtime_resources(runtime)


@pytest.mark.parametrize(
    ("value", "expected_percentage", "expected_current"),
    [("missing", 50, 19.0), (None, 50, 19.0), ("invalid", 50, 19.0), (0, 0, 6.0), (65, 65, 22.9)],
)
async def test_real_rest_poll_preserves_missing_percentage_and_reconciles_explicit_limits(
    hass, entry, recovery_dashboard, value, expected_percentage, expected_current
):
    """Exercise payload parsing, REST merge and publication together."""
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    runtime = await _async_prepare_runtime(hass, entry, recovery_dashboard)
    try:
        bucket = next(iter(runtime.sites[317418].stations.values()))
        coord = bucket.station_coordinator
        uuid = next(iter(bucket.connectors))
        update_connector(
            coord,
            uuid,
            selected_percentage_limit=50,
            selected_current_limit=19.0,
            optimization_strategy="NONE",
            power_total=3200,
        )
        devices = deepcopy(recovery_dashboard.async_get_smart_devices.return_value)
        devices[1]["properties"] = (
            [] if value == "missing" else [{"spec": {"name": "percentageLimit"}, "value": value}]
        )
        recovery_dashboard.async_get_smart_devices.return_value = devices
        coord._force_smartdevice_refresh = True
        await coord.async_refresh()
        assert coord.last_update_success
        state = coord.data.connectors[uuid]
        assert state.selected_percentage_limit == expected_percentage
        assert state.selected_current_limit == expected_current
        assert state.power_total == 3200
        assert state.api_available
    finally:
        await _async_shutdown_runtime_resources(runtime)


@pytest.mark.parametrize("failure_phase", ["discovery", "rest"])
async def test_multi_site_recovery_waits_for_all_sites_and_closes_successful_siblings(
    hass, entry, snapshot, recovery_dashboard, probe_coordinators, failure_phase
):
    """A healthy site's probe is cleaned up when a second physical site fails."""
    id_map = {317418: 327418, 317443: 327443}

    def second_site(value):
        if isinstance(value, dict):
            return {key: second_site(item) for key, item in value.items()}
        if isinstance(value, list):
            return [second_site(item) for item in value]
        if isinstance(value, int):
            return id_map.get(value, value)
        if isinstance(value, str):
            for original, replacement in (
                ("STATION123", "STATION456"),
                ("GATEWAY123", "GATEWAY456"),
                ("CONTROL123", "CONTROL456"),
                ("site-uuid", "other-site-uuid"),
                ("station-location-uuid", "other-station-location-uuid"),
                ("station-uuid", "other-station-uuid"),
                ("connector-uuid-1", "other-connector-uuid-1"),
                ("station-device", "other-station-device"),
                ("connector-device-1", "other-connector-device-1"),
            ):
                value = value.replace(original, replacement)
        return value

    locations = recovery_dashboard.async_get_service_locations_full_details.return_value
    recovery_dashboard.async_get_service_locations_full_details.return_value = (
        locations + second_site(locations)
    )
    highlevel = recovery_dashboard.async_get_highlevel_configuration.side_effect
    devices = recovery_dashboard.async_get_smart_devices.return_value
    details = recovery_dashboard.async_get_charging_station_details.return_value
    attempts = 0
    device_calls = {}

    async def highlevel_for_site(sid):
        if sid in id_map.values():
            if attempts == 1 and failure_phase == "discovery":
                raise SmappeeServerError("second site discovery offline")
            original = next(key for key, value in id_map.items() if value == sid)
            return second_site(highlevel(original))
        return highlevel(sid)

    async def devices_for_site(sid):
        device_calls[sid] = device_calls.get(sid, 0) + 1
        if sid == 327443:
            if attempts == 1 and failure_phase == "rest" and device_calls[sid] == 2:
                raise SmappeeServerError("second site REST offline")
            return second_site(devices)
        return devices

    recovery_dashboard.async_get_highlevel_configuration.side_effect = highlevel_for_site
    recovery_dashboard.async_get_smart_devices.side_effect = devices_for_site
    recovery_dashboard.async_get_charging_station_details.side_effect = lambda serial: (
        second_site(details) if serial == "STATION456" else details
    )
    cached = build_cached_runtime(hass, entry, bootstrap.dashboard(), snapshot)
    entry.runtime_data = cached
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)

    async def next_probe(_delay):
        nonlocal attempts
        if attempts:
            assert_probe_closed(probe_coordinators)
            assert any(
                getattr(coord, "site_location_id", None) == 317418 for coord in probe_coordinators
            )
            assert not cached.stopping
        attempts += 1
        device_calls.clear()
        assert attempts <= 2

    async def reload_after_cleanup(_entry_id):
        assert_probe_closed(probe_coordinators)
        assert {
            coord.site_location_id
            for coord in probe_coordinators
            if hasattr(coord, "site_location_id")
        } == {317418, 327418}
        return True

    try:
        with (
            patch("custom_components.smappee_ev.mqtt_recovery.asyncio", wraps=asyncio) as clock,
            patch(
                "custom_components.smappee_ev.mqtt_recovery._create_dashboard_client",
                return_value=recovery_dashboard,
            ),
            patch.object(
                hass.config_entries, "async_reload", side_effect=reload_after_cleanup
            ) as reload,
            patch.object(entry, "async_on_unload", wraps=entry.async_on_unload) as unload,
        ):
            clock.sleep = AsyncMock(side_effect=next_probe)
            await _async_recover(hass, entry, cached, _async_prepare_runtime)
            await hass.async_block_till_done()
            reload.assert_awaited_once_with(entry.entry_id)
            unload.assert_not_called()
            assert attempts == 2
            assert cached.recovery.attempts == 2
            assert cached.recovery.status == "recovered"
            assert entry.runtime_data is cached
    finally:
        await _async_shutdown_runtime_resources(cached)
