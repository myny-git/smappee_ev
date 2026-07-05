"""Comprehensive tests for number.py: SmappeeCombinedCurrentSlider and SmappeeMinSurplusPctNumber."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
import pytest

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, StationState
from custom_components.smappee_ev.number import (
    SmappeeCapacityMaximumPowerNumber,
    SmappeeCombinedCurrentSlider,
    SmappeeConnectorMaxCurrentNumber,
    SmappeeMinSurplusPctNumber,
    SmappeeOfflineFailsafeCurrentNumber,
    SmappeeOverloadMaximumLoadNumber,
    async_setup_entry,
)
from tests.factories import make_connector_runtime, make_site_runtime, make_station_runtime


@pytest.fixture
def connector_state():
    return ConnectorState(
        connector_number=1,
        session_state="Charging",
        selected_current_limit=16,
        selected_percentage_limit=50,
        selected_mode="STANDARD",
        min_current=6,
        max_current=32,
        min_surpluspct=20,
    )


@pytest.fixture
def station_state():
    return StationState(led_brightness=70, available=True)


@pytest.fixture
def integration_data(connector_state, station_state):
    return IntegrationData(
        station=station_state,
        connectors={"uuid": connector_state},
    )


@pytest.fixture
def coordinator(integration_data):
    coord = MagicMock(spec=SmappeeCoordinator)
    coord.data = integration_data
    coord.async_set_updated_data = MagicMock()
    coord.async_schedule_dashboard_refresh = MagicMock()
    return coord


@pytest.fixture
def api_client():
    client = MagicMock(spec=SmappeeDeviceHandle)
    client.set_current = AsyncMock(return_value=(16, 50))
    client.start_charging = AsyncMock(return_value=(6, 100))
    client.set_brightness = AsyncMock()
    client.set_min_surpluspct = AsyncMock()
    client.set_connector_max_current = AsyncMock()
    return client


@pytest.fixture
def dashboard_client():
    client = MagicMock()
    client.async_set_capacity_protection = AsyncMock()
    client.async_set_overload_protection = AsyncMock()
    return client


# --- SmappeeCombinedCurrentSlider ---
def test_current_slider_native_value(coordinator, api_client):
    slider = SmappeeCombinedCurrentSlider(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )
    assert slider.native_value == 16
    attrs = slider.extra_state_attributes
    assert attrs["percentage"] == 38
    assert attrs["percentage_formatted"] == "38%"
    assert attrs["fixed_range"] is False


@pytest.mark.asyncio
async def test_current_slider_set_native_value(coordinator, api_client):
    slider = SmappeeCombinedCurrentSlider(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )
    # Test normal range - mock returns (16.0, 50) simulating 50% of 6-32A range
    state = slider._state()
    state.max_current = 32
    state.min_current = 6
    state.selected_current_limit = None
    state.selected_percentage_limit = None
    api_client.set_current = AsyncMock(return_value=(16.0, 50))
    await slider.async_set_native_value(20)
    assert state.selected_current_limit == 16.0
    assert state.selected_percentage_limit == 50
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
    # Test fixed range - mock returns (6.0, 100) for 100% of fixed 6-6A range
    coordinator.async_set_updated_data.reset_mock()
    state.max_current = 6
    state.min_current = 6
    api_client.set_current = AsyncMock(return_value=(6.0, 100))
    await slider.async_set_native_value(6)
    assert state.selected_current_limit == 6.0
    assert state.selected_percentage_limit == 100
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)


def test_current_slider_derives_value_from_percentage(coordinator, api_client):
    state = coordinator.data.connectors["uuid"]
    state.selected_current_limit = None
    state.selected_percentage_limit = 25
    slider = SmappeeCombinedCurrentSlider(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    assert slider.native_value == 12.5
    assert slider.extra_state_attributes == {
        "percentage": 25,
        "percentage_formatted": "25%",
        "fixed_range": False,
    }


def test_current_slider_clamps_explicit_value_to_configured_max(coordinator, api_client):
    state = coordinator.data.connectors["uuid"]
    state.min_current = 6
    state.max_current = 16
    state.selected_current_limit = 20
    slider = SmappeeCombinedCurrentSlider(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    assert slider.native_value == 16


@pytest.mark.asyncio
async def test_connector_max_current_number_updates_config_without_charging_limit_action(
    coordinator, api_client
):
    state = coordinator.data.connectors["uuid"]
    state.min_current = 6
    state.max_current = 32
    state.selected_current_limit = 20
    number = SmappeeConnectorMaxCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    await number.async_set_native_value(16)

    api_client.set_connector_max_current.assert_awaited_once_with(16)
    api_client.set_current.assert_not_awaited()
    assert state.max_current == 16
    assert state.selected_current_limit == 16
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
    coordinator.async_schedule_dashboard_refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_connector_max_current_number_clamps_to_live_range(coordinator, api_client):
    state = coordinator.data.connectors["uuid"]
    state.min_current = 8
    state.max_current = 20
    state.selected_current_limit = 18
    number = SmappeeConnectorMaxCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    await number.async_set_native_value(4)

    api_client.set_connector_max_current.assert_awaited_once_with(8)
    assert state.max_current == 8
    assert state.selected_current_limit == 8


def test_connector_max_current_number_updates_live_minimum_from_coordinator(
    coordinator, api_client
):
    state = coordinator.data.connectors["uuid"]
    state.min_current = 6
    number = SmappeeConnectorMaxCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )
    assert number.native_min_value == 6

    state.min_current = 10

    with patch.object(SmappeeConnectorMaxCurrentNumber.__mro__[1], "_handle_coordinator_update"):
        number._handle_coordinator_update()

    assert number.native_min_value == 10


@pytest.mark.asyncio
async def test_connector_max_current_number_missing_state_raises(coordinator, api_client):
    coordinator.data = None
    number = SmappeeConnectorMaxCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(16)

    api_client.set_connector_max_current.assert_not_awaited()


@pytest.mark.asyncio
async def test_connector_max_current_number_api_error_preserves_state(coordinator, api_client):
    state = coordinator.data.connectors["uuid"]
    state.max_current = 32
    state.selected_current_limit = 20
    api_client.set_connector_max_current.side_effect = RuntimeError("dashboard down")
    number = SmappeeConnectorMaxCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    with pytest.raises(RuntimeError):
        await number.async_set_native_value(16)

    assert state.max_current == 32
    assert state.selected_current_limit == 20
    coordinator.async_set_updated_data.assert_not_called()
    coordinator.async_schedule_dashboard_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_capacity_maximum_power_rounds_to_one_decimal_and_defaults_active_true(
    coordinator, dashboard_client
):
    coordinator.dashboard_client = dashboard_client
    coordinator.data.station.capacity_protection_active = None
    number = SmappeeCapacityMaximumPowerNumber(
        coordinator=coordinator,
        sid=1,
        station_uuid="station",
    )

    await number.async_set_native_value(5.06)

    dashboard_client.async_set_capacity_protection.assert_awaited_once_with(1, True, 5.1)
    assert coordinator.data.station.capacity_maximum_power_kw == 5.1
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
    coordinator.async_schedule_dashboard_refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_capacity_maximum_power_missing_station_state_raises(coordinator, dashboard_client):
    coordinator.dashboard_client = dashboard_client
    coordinator.data = None
    number = SmappeeCapacityMaximumPowerNumber(
        coordinator=coordinator,
        sid=1,
        station_uuid="station",
    )

    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(5)

    dashboard_client.async_set_capacity_protection.assert_not_awaited()


@pytest.mark.asyncio
async def test_capacity_maximum_power_api_error_preserves_previous_value(
    coordinator, dashboard_client
):
    coordinator.dashboard_client = dashboard_client
    station = coordinator.data.station
    station.capacity_protection_active = False
    station.capacity_maximum_power_kw = 4.2
    dashboard_client.async_set_capacity_protection.side_effect = RuntimeError("offline")
    number = SmappeeCapacityMaximumPowerNumber(
        coordinator=coordinator,
        sid=1,
        station_uuid="station",
    )

    with pytest.raises(RuntimeError):
        await number.async_set_native_value(6)

    dashboard_client.async_set_capacity_protection.assert_awaited_once_with(1, False, 6.0)
    assert station.capacity_maximum_power_kw == 4.2
    coordinator.async_set_updated_data.assert_not_called()
    coordinator.async_schedule_dashboard_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_overload_maximum_load_rounds_to_integer_and_defaults_active_true(
    coordinator, dashboard_client
):
    coordinator.dashboard_client = dashboard_client
    coordinator.data.station.overload_protection_active = None
    number = SmappeeOverloadMaximumLoadNumber(
        coordinator=coordinator,
        sid=1,
        station_uuid="station",
    )

    await number.async_set_native_value(24.6)

    dashboard_client.async_set_overload_protection.assert_awaited_once_with(1, True, 25)
    assert coordinator.data.station.overload_maximum_load_a == 25
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
    coordinator.async_schedule_dashboard_refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_overload_maximum_load_missing_station_state_raises(coordinator, dashboard_client):
    coordinator.dashboard_client = dashboard_client
    coordinator.data = None
    number = SmappeeOverloadMaximumLoadNumber(
        coordinator=coordinator,
        sid=1,
        station_uuid="station",
    )

    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(16)

    dashboard_client.async_set_overload_protection.assert_not_awaited()


def test_current_slider_fixed_range_and_missing_state(coordinator, api_client):
    state = coordinator.data.connectors["uuid"]
    state.selected_current_limit = None
    state.selected_percentage_limit = None
    state.min_current = 10
    state.max_current = 10
    slider = SmappeeCombinedCurrentSlider(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    assert slider.native_value == 10
    assert slider.extra_state_attributes == {
        "percentage": None,
        "percentage_formatted": "\u2014",
        "fixed_range": True,
    }

    coordinator.data = None
    assert slider.native_value is None
    assert slider.extra_state_attributes == {}


@pytest.mark.asyncio
async def test_current_slider_set_native_value_without_state(coordinator, api_client):
    coordinator.data = None
    slider = SmappeeCombinedCurrentSlider(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    with pytest.raises(HomeAssistantError):
        await slider.async_set_native_value(20)

    api_client.set_current.assert_not_awaited()


def test_current_slider_updates_range_from_coordinator(coordinator, api_client):
    slider = SmappeeCombinedCurrentSlider(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )
    state = coordinator.data.connectors["uuid"]
    state.min_current = 8
    state.max_current = 4

    with patch.object(SmappeeCombinedCurrentSlider.__mro__[1], "_handle_coordinator_update"):
        slider._handle_coordinator_update()

    assert slider.native_min_value == 8
    assert slider.native_max_value == 8


def test_dashboard_station_number_ranges(coordinator, dashboard_client):
    coordinator.dashboard_client = dashboard_client

    capacity = SmappeeCapacityMaximumPowerNumber(
        coordinator=coordinator,
        sid=1,
        station_uuid="station",
    )
    overload = SmappeeOverloadMaximumLoadNumber(
        coordinator=coordinator,
        sid=1,
        station_uuid="station",
    )

    assert capacity.native_min_value == 0
    assert capacity.native_max_value == 10
    assert capacity.native_step == 0.1
    assert overload.native_min_value == 0
    assert overload.native_max_value == 32
    assert overload.native_step == 1


@pytest.mark.asyncio
async def test_offline_failsafe_set_native_value(coordinator, api_client, dashboard_client):
    coordinator.dashboard_client = dashboard_client
    coordinator.last_update_success = True
    station = coordinator.data.station
    station.offline_charging_enabled = True
    station.offline_failsafe_current_a = 6
    api_client.set_offline_charging_config = AsyncMock()

    number = SmappeeOfflineFailsafeCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
    )

    assert number.available is True
    assert number.native_value == 6

    await number.async_set_native_value(10)

    api_client.set_offline_charging_config.assert_awaited_once_with(True, 10)
    assert station.offline_failsafe_current_a == 10
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)


@pytest.mark.asyncio
async def test_offline_failsafe_defaults_enabled_and_clamps_negative_current(
    coordinator, api_client, dashboard_client
):
    coordinator.dashboard_client = dashboard_client
    station = coordinator.data.station
    station.offline_charging_enabled = None
    station.offline_failsafe_current_a = 12
    api_client.set_offline_charging_config = AsyncMock()
    number = SmappeeOfflineFailsafeCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
    )

    await number.async_set_native_value(-3)

    api_client.set_offline_charging_config.assert_awaited_once_with(True, 0)
    assert station.offline_charging_enabled is True
    assert station.offline_failsafe_current_a == 0
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
    coordinator.async_schedule_dashboard_refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_offline_failsafe_missing_station_state_raises(coordinator, api_client):
    coordinator.data = None
    number = SmappeeOfflineFailsafeCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
    )

    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(10)


def test_offline_failsafe_available_only_when_supported(coordinator, api_client, dashboard_client):
    coordinator.last_update_success = True
    coordinator.dashboard_client = dashboard_client
    station = coordinator.data.station
    number = SmappeeOfflineFailsafeCurrentNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
    )

    station.offline_charging_enabled = False
    assert number.available is False

    station.offline_charging_enabled = True
    assert number.available is True

    coordinator.dashboard_client = None
    assert number.available is False


# --- SmappeeMinSurplusPctNumber ---
def test_min_surpluspct_native_value(coordinator, api_client):
    min_pct = SmappeeMinSurplusPctNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )
    assert min_pct.native_value == 20
    assert min_pct.entity_category is EntityCategory.CONFIG


@pytest.mark.asyncio
async def test_min_surpluspct_set_native_value(coordinator, api_client):
    min_pct = SmappeeMinSurplusPctNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )
    state = min_pct._state()
    await min_pct.async_set_native_value(17)
    assert state.min_surpluspct == 17
    api_client.set_min_surpluspct.assert_awaited_with(17)
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
    coordinator.async_schedule_dashboard_refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_min_surpluspct_api_error_preserves_state_and_raises_homeassistant_error(
    coordinator, api_client
):
    state = coordinator.data.connectors["uuid"]
    state.min_surpluspct = 20
    api_client.set_min_surpluspct.side_effect = RuntimeError("dashboard down")
    min_pct = SmappeeMinSurplusPctNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    with pytest.raises(HomeAssistantError):
        await min_pct.async_set_native_value(17)

    assert state.min_surpluspct == 20
    coordinator.async_set_updated_data.assert_not_called()
    coordinator.async_schedule_dashboard_refresh.assert_not_called()


def test_min_surpluspct_native_value_returns_none_without_state(coordinator, api_client):
    min_pct = SmappeeMinSurplusPctNumber(
        coordinator=coordinator,
        api_client=api_client,
        sid=1,
        station_uuid="station",
        connector_uuid="uuid",
    )

    coordinator.data.connectors["uuid"].min_surpluspct = None
    assert min_pct.native_value is None

    coordinator.data = None
    assert min_pct.native_value is None


@pytest.mark.asyncio
async def test_async_setup_entry_adds_connector_numbers(hass, coordinator, api_client):
    station_client = MagicMock(spec=SmappeeDeviceHandle)
    station_client.serial = "STATION123"
    station_client.connector_number = None
    runtime = MagicMock(spec=RuntimeData)
    runtime.sites = {
        1: make_site_runtime(
            site_location_id=1,
            stations={
                "station": make_station_runtime(
                    site_location_id=1,
                    control_location_id=1,
                    station_uuid="station",
                    coordinator=coordinator,
                    station_client=station_client,
                    connectors={
                        "uuid": make_connector_runtime(
                            connector_key="uuid",
                            connector_uuid="uuid",
                            connector_client=api_client,
                        )
                    },
                )
            },
        )
    }
    entry = MagicMock(spec=ConfigEntry)
    entry.runtime_data = runtime
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args.args[0]
    assert len(entities) == 3
    assert all(isinstance(entity, NumberEntity) for entity in entities)
    assert async_add_entities.call_args.args[1] is False


@pytest.mark.asyncio
async def test_async_setup_entry_adds_site_dashboard_numbers_once(hass, dashboard_client):
    site_coordinator = MagicMock(spec=SmappeeCoordinator)
    site_coordinator.dashboard_client = dashboard_client
    site_coordinator.data = IntegrationData(station=StationState(), connectors={})
    site_coordinator.last_update_success = True
    site_coordinator.site_name = "Main site"
    site_coordinator.gateway_serial = "PARKGW"
    site_coordinator.gateway_type = "Infinity"
    site_coordinator.station_client = MagicMock(spec=SmappeeDeviceHandle)
    site_coordinator.station_client.charging_station_serial = "STATION123"
    site_coordinator.station_client.service_location_id = 317443

    other_coordinator = MagicMock(spec=SmappeeCoordinator)
    other_coordinator.dashboard_client = dashboard_client
    other_coordinator.data = IntegrationData(station=StationState(), connectors={})
    other_coordinator.last_update_success = True
    other_coordinator.station_client = MagicMock(spec=SmappeeDeviceHandle)
    other_coordinator.station_client.charging_station_serial = "STATION456"
    other_coordinator.station_client.service_location_id = 317444

    runtime = MagicMock(spec=RuntimeData)
    runtime.sites = {
        317418: make_site_runtime(
            site_location_id=317418,
            stations={
                "station-a": make_station_runtime(
                    site_location_id=317418,
                    control_location_id=317443,
                    station_uuid="station-a",
                    coordinator=site_coordinator,
                    connectors={},
                ),
                "station-b": make_station_runtime(
                    site_location_id=317418,
                    control_location_id=317444,
                    station_uuid="station-b",
                    coordinator=other_coordinator,
                    connectors={},
                ),
            },
        )
    }
    entry = MagicMock(spec=ConfigEntry)
    entry.runtime_data = runtime
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args.args[0]
    capacity = [e for e in entities if isinstance(e, SmappeeCapacityMaximumPowerNumber)]
    overload = [e for e in entities if isinstance(e, SmappeeOverloadMaximumLoadNumber)]
    assert len(capacity) == 1
    assert len(overload) == 1
    assert capacity[0].device_info["identifiers"] == {(DOMAIN, "site:317418")}
    assert overload[0].device_info["identifiers"] == {(DOMAIN, "site:317418")}


@pytest.mark.asyncio
async def test_async_setup_entry_handles_empty_sites(hass):
    runtime = MagicMock(spec=RuntimeData)
    runtime.sites = {}
    entry = MagicMock(spec=ConfigEntry)
    entry.runtime_data = runtime
    async_add_entities = MagicMock()

    await async_setup_entry(hass, entry, async_add_entities)

    async_add_entities.assert_called_once_with([], False)
