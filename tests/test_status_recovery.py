"""Status failures must not block monitoring or require an integration reload."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.smappee_ev import async_setup_entry, binary_sensor, sensor
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.coordinators.status import (
    STATUS_SUBSYSTEMS,
    SmappeeStatusCoordinator,
)
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.runtime_lifecycle import _async_shutdown_runtime_resources
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


def status_session(*responses):
    response = AsyncMock()
    response.__aenter__.return_value = response
    response.raise_for_status = MagicMock()
    response.json.side_effect = responses
    session = MagicMock()
    session.get.return_value = response
    return session


async def test_pending_status_request_does_not_block_setup_and_is_cancelled_on_unload(
    hass, entry, online
):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def wait_for_response():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    session = status_session()
    session.get.return_value.__aenter__.side_effect = wait_for_response
    with (
        patch("custom_components.smappee_ev._async_prepare_runtime", return_value=online),
        patch("custom_components.smappee_ev._register_runtime_devices"),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch("custom_components.smappee_ev._start_runtime_background_work") as start_mqtt,
        patch("custom_components.smappee_ev.async_save_snapshot", new=AsyncMock()),
        patch(
            "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
            return_value=session,
        ),
    ):
        assert await asyncio.wait_for(async_setup_entry(hass, entry), timeout=1)
        await asyncio.wait_for(started.wait(), timeout=1)
        start_mqtt.assert_called_once()
        assert entry.runtime_data.status_coordinator is not None
        assert entry.runtime_data.status_coordinator.data is None
        timeout = session.get.call_args.kwargs["timeout"]
        assert timeout.total == 15
        assert timeout.connect == 5
        await _async_shutdown_runtime_resources(online)

    assert cancelled.is_set()
    assert not online.background_tasks


async def test_first_failure_recovers_on_timer_and_adds_new_subsystems_once(hass, freezer):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coord = SmappeeStatusCoordinator(hass, config_entry=entry)
    entry.runtime_data = RuntimeData(api=None, sites={}, mqtt={}, status_coordinator=coord)
    binary_entities = []
    sensor_entities = []
    await binary_sensor.async_setup_entry(
        hass, entry, lambda entities, *_: binary_entities.extend(entities)
    )
    await sensor.async_setup_entry(
        hass, entry, lambda entities, *_: sensor_entities.extend(entities)
    )
    assert len(binary_entities) == len(STATUS_SUBSYSTEMS)
    incidents = next(
        e for e in sensor_entities if isinstance(e, sensor.SmappeeActiveIncidentsSensor)
    )
    payload = {
        "subsystems": [
            {"subsystem": "api", "status": "ok"},
            {"subsystem": "new-service", "status": "maintenance"},
        ]
    }
    session = status_session(OSError("offline"), payload, payload)
    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=session,
    ):
        await coord.async_refresh()
        assert not incidents.available
        assert incidents.native_value is None
        assert not any(e.available for e in binary_entities)
        freezer.tick(timedelta(minutes=5, seconds=1))
        async_fire_time_changed(hass, datetime.now(UTC))
        await hass.async_block_till_done()
        assert session.get.call_count == 2
        assert incidents.available
        assert incidents.native_value == 0
        new = [e for e in binary_entities if e._subsystem == "new-service"]
        assert len(new) == 1
        assert new[0].is_on
        await coord.async_refresh()
        assert len([e for e in binary_entities if e._subsystem == "new-service"]) == 1
    await coord.async_shutdown()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {"subsystems": None},
        {"subsystems": [None]},
        {"subsystems": [{"subsystem": [], "status": "ok"}]},
        {"subsystems": [{"subsystem": "api", "status": None}]},
        {"subsystems": [{"subsystem": "api", "status": ""}]},
        {"incidents": None},
        {"incidents": [None]},
        {"incidents": [{"resolved": "false"}]},
        {"incidents": [{"resolved": False, "subsystem": []}]},
        {"incidents": [{"resolved": False, "level": 1}]},
        *[
            {"incidents": [{"resolved": False, "timestamp": timestamp}]}
            for timestamp in ("bad", True, float("nan"), float("inf"), 10**30)
        ],
    ],
)
async def test_invalid_status_preserves_last_valid_data_and_recovery(hass, payload):
    coord = SmappeeStatusCoordinator(hass)
    good = {"subsystems": [{"subsystem": "api", "status": "ok"}]}
    session = status_session(good, payload, good)
    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=session,
    ):
        await coord.async_refresh()
        previous = coord.data
        await coord.async_refresh()
        assert not coord.last_update_success
        assert isinstance(coord.last_exception, UpdateFailed)
        assert coord.data is previous
        await coord.async_refresh()
        assert coord.last_update_success
        assert coord.data.subsystems == {"api": "ok"}
    await coord.async_shutdown()


async def test_epoch_timestamp_is_safe_for_incident_entity(hass):
    coord = SmappeeStatusCoordinator(hass)
    session = status_session({"incidents": [{"resolved": False, "timestamp": 0}]})
    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=session,
    ):
        await coord.async_refresh()
    entity = sensor.SmappeeActiveIncidentsSensor(coord)
    assert entity.native_value == 1
    assert entity.extra_state_attributes["incidents"][0]["timestamp"] == "1970-01-01T00:00:00+00:00"
    await coord.async_shutdown()


async def test_accounts_have_independent_status_entities_and_devices(hass):
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    registered_entities = []
    registered_devices = []
    coordinators = []
    for account in ("first", "second"):
        entry = MockConfigEntry(domain=DOMAIN, unique_id=account, data={})
        entry.add_to_hass(hass)
        coord = SmappeeStatusCoordinator(hass, config_entry=entry)
        coordinators.append(coord)
        status = binary_sensor.SmappeeServiceStatusSensor(coord, "api")
        incidents = sensor.SmappeeActiveIncidentsSensor(coord)
        device = devices.async_get_or_create(config_entry_id=entry.entry_id, **status.device_info)
        registered_devices.append(device.id)
        for platform, entity in (("binary_sensor", status), ("sensor", incidents)):
            item = registry.async_get_or_create(
                platform, DOMAIN, entity.unique_id, config_entry=entry, device_id=device.id
            )
            registered_entities.append(item.entity_id)
    assert len(set(registered_entities)) == 4
    assert len(set(registered_devices)) == 2
    await coordinators[0].async_shutdown()
    session = status_session({"subsystems": [{"subsystem": "api", "status": "ok"}]})
    with patch(
        "custom_components.smappee_ev.coordinators.status.async_get_clientsession",
        return_value=session,
    ):
        await coordinators[1].async_refresh()
        assert coordinators[1].last_update_success
    await coordinators[1].async_shutdown()
