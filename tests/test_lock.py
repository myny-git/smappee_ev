"""Tests for the lock platform."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
import pytest

from custom_components.smappee_ev import lock
from custom_components.smappee_ev.api.errors import SmappeeAuthenticationError
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState
from tests.factories import make_connector_runtime, make_site_runtime, make_station_runtime


@pytest.fixture
def mock_runtime_data():
    """Create mock runtime data."""
    runtime = MagicMock(spec=RuntimeData)
    runtime.sites = {
        12345: make_site_runtime(
            site_location_id=12345,
            stations={
                "station_uuid": make_station_runtime(
                    site_location_id=12345,
                    control_location_id=12345,
                    station_uuid="station_uuid",
                    coordinator=MagicMock(spec=SmappeeCoordinator),
                    station_client=MagicMock(),
                    connectors={
                        "connector_uuid1": make_connector_runtime(
                            connector_key="connector_uuid1",
                            connector_uuid="connector_uuid1",
                            connector_client=MagicMock(),
                        ),
                    },
                )
            },
        )
    }
    return runtime


@pytest.fixture
def mock_config_entry(mock_runtime_data):
    """Create mock config entry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.runtime_data = mock_runtime_data
    return entry


@pytest.fixture
def mock_integration_data():
    """Create mock integration data with station and connector states."""
    station = StationState(cable_locked=False)
    connector = ConnectorState(connector_number=1)
    return IntegrationData(
        station=station,
        connectors={"connector_uuid1": connector},
    )


class TestLockPlatform:
    """Test cases for lock platform."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self, hass: HomeAssistant, mock_config_entry):
        """Test lock platform setup."""
        async_add_entities = MagicMock()

        await lock.async_setup_entry(hass, mock_config_entry, async_add_entities)

        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert all(isinstance(entity, LockEntity) for entity in entities)
        assert isinstance(entities[0], lock.SmappeeCableLock)

    @pytest.mark.asyncio
    async def test_async_setup_entry_no_sites(self, hass: HomeAssistant):
        """Test lock platform setup with no sites."""
        runtime = MagicMock(spec=RuntimeData)
        runtime.sites = {}

        entry = MagicMock(spec=ConfigEntry)
        entry.runtime_data = runtime

        async_add_entities = MagicMock()

        await lock.async_setup_entry(hass, entry, async_add_entities)

        async_add_entities.assert_called_once_with([], False)


class TestSmappeeCableLock:
    """Test the SmappeeCableLock class."""

    def test_initialization(self):
        coordinator = MagicMock(spec=SmappeeCoordinator)
        api_client = MagicMock()

        cable_lock = lock.SmappeeCableLock(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        assert cable_lock._station_uuid == "station_uuid"
        assert cable_lock._sid == 12345
        assert cable_lock.api_client == api_client
        assert cable_lock.translation_key == "cable_lock"

    def test_available_only_when_station_reports_cable_lock_state(self, mock_integration_data):
        """Stations that never report cableLocked (e.g. fixed-cable models) stay unavailable."""
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        coordinator.monitoring_only = False
        coordinator.last_update_success = True
        api_client = MagicMock()

        cable_lock = lock.SmappeeCableLock(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        mock_integration_data.station.cable_locked = None
        mock_integration_data.station.api_available = True
        assert cable_lock.available is False

        mock_integration_data.station.cable_locked = True
        assert cable_lock.available is True

    def test_is_locked_property(self, mock_integration_data):
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()

        cable_lock = lock.SmappeeCableLock(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        assert cable_lock.is_locked is False

        mock_integration_data.station.cable_locked = True
        assert cable_lock.is_locked is True

        coordinator.data = None
        assert cable_lock.is_locked is None

    @pytest.mark.asyncio
    async def test_async_lock(self, mock_integration_data):
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()
        api_client.set_cable_locked = AsyncMock()

        cable_lock = lock.SmappeeCableLock(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        mock_integration_data.station.cable_locked = False
        await cable_lock.async_lock()

        api_client.set_cable_locked.assert_called_once()
        assert mock_integration_data.station.cable_locked is True
        coordinator.async_set_updated_data.assert_called_once_with(mock_integration_data)
        coordinator.async_schedule_dashboard_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_unlock(self, mock_integration_data):
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()
        api_client.set_cable_unlocked = AsyncMock()

        cable_lock = lock.SmappeeCableLock(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        mock_integration_data.station.cable_locked = True
        await cable_lock.async_unlock()

        api_client.set_cable_unlocked.assert_called_once()
        assert mock_integration_data.station.cable_locked is False
        coordinator.async_set_updated_data.assert_called_once_with(mock_integration_data)

    @pytest.mark.asyncio
    async def test_set_locked_error_preserves_state(self, mock_integration_data):
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()
        api_client.set_cable_locked = AsyncMock(side_effect=RuntimeError("Connection error"))

        cable_lock = lock.SmappeeCableLock(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        mock_integration_data.station.cable_locked = False

        with pytest.raises(HomeAssistantError):
            await cable_lock._set_locked(True)

        assert mock_integration_data.station.cable_locked is False
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_locked_auth_error_preserves_state_and_propagates(
        self, mock_integration_data
    ):
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = mock_integration_data
        api_client = MagicMock()
        api_client.set_cable_locked = AsyncMock(
            side_effect=SmappeeAuthenticationError("reauth required")
        )
        cable_lock = lock.SmappeeCableLock(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )
        mock_integration_data.station.cable_locked = False

        with pytest.raises(ConfigEntryAuthFailed, match="reauth required"):
            await cable_lock.async_lock()

        assert mock_integration_data.station.cable_locked is False
        coordinator.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_locked_raises_when_station_state_missing(self):
        coordinator = MagicMock(spec=SmappeeCoordinator)
        coordinator.data = None
        api_client = MagicMock()

        cable_lock = lock.SmappeeCableLock(
            coordinator=coordinator,
            api_client=api_client,
            sid=12345,
            station_uuid="station_uuid",
        )

        with pytest.raises(HomeAssistantError) as err:
            await cable_lock.async_lock()

        assert err.value.translation_key == "station_unavailable"


@pytest.mark.parametrize("target", [True, False])
@pytest.mark.parametrize("snapshot", ["same", "refreshed", "confirmed", "removed"])
@pytest.mark.parametrize("outcome", ["success", "error", "auth", "cancel"])
async def test_pending_lock_preserves_current_snapshot(
    mock_integration_data, target, snapshot, outcome
):
    """A write must not claim success early or roll back concurrent updates."""
    initial = not target
    mock_integration_data.station.cable_locked = initial
    mock_integration_data.connectors["connector_uuid1"].power_total = 100
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.data = mock_integration_data
    coordinator.async_set_updated_data.side_effect = lambda data: setattr(coordinator, "data", data)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def write():
        started.set()
        await finish.wait()
        if outcome == "error":
            raise RuntimeError("write failed")
        if outcome == "auth":
            raise SmappeeAuthenticationError("reauth required")

    api = MagicMock()
    api.set_cable_locked = AsyncMock(side_effect=write)
    api.set_cable_unlocked = AsyncMock(side_effect=write)
    entity = lock.SmappeeCableLock(
        coordinator=coordinator, api_client=api, sid=12345, station_uuid="station_uuid"
    )
    task = asyncio.create_task(entity.async_lock() if target else entity.async_unlock())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert entity.is_locked is initial
        coordinator.async_set_updated_data.assert_not_called()

        current = mock_integration_data
        expected_state = initial
        if snapshot in {"refreshed", "confirmed"}:
            expected_state = target if snapshot == "confirmed" else initial
            current = replace(
                current,
                station=replace(current.station, cable_locked=expected_state, led_brightness=75),
                connectors={
                    "connector_uuid1": replace(
                        current.connectors["connector_uuid1"], power_total=3200
                    )
                },
                recent_sessions=[{"energy": 12.5}],
            )
        elif snapshot == "same":
            current.connectors["connector_uuid1"].power_total = 3200
        else:
            current = None
        coordinator.data = current

        if outcome == "cancel":
            task.cancel()
        else:
            finish.set()
        if outcome == "success":
            await asyncio.gather(task)
            expected_state = target
        else:
            error = {
                "error": HomeAssistantError,
                "auth": ConfigEntryAuthFailed,
                "cancel": asyncio.CancelledError,
            }[outcome]
            with pytest.raises(error):
                await asyncio.gather(task)

        assert coordinator.data is current
        if current is not None:
            assert current.station.cable_locked is expected_state
            assert current.connectors["connector_uuid1"].power_total == 3200
            if snapshot in {"refreshed", "confirmed"}:
                assert current.station.led_brightness == 75
                assert current.recent_sessions == [{"energy": 12.5}]
        if outcome == "success" and snapshot in {"same", "refreshed"}:
            coordinator.async_set_updated_data.assert_called_once_with(current)
        else:
            coordinator.async_set_updated_data.assert_not_called()
        if outcome in {"success", "cancel"}:
            coordinator.async_schedule_dashboard_refresh.assert_called_once()
        else:
            coordinator.async_schedule_dashboard_refresh.assert_not_called()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
