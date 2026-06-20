"""Shared test factories for Smappee EV integration tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntry, ConfigEntryState

from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.coordinator import SmappeeCoordinator
from custom_components.smappee_ev.data import (
    ConnectorState,
    IntegrationData,
    RuntimeData,
    SmappeeConnectorRuntime,
    SmappeeLedRuntime,
    SmappeeSiteRuntime,
    SmappeeStationRuntime,
    StationState,
)
from custom_components.smappee_ev.device_handle import SmappeeDeviceHandle

_DEFAULT_API = object()


def make_connector_client(
    *,
    service_location_id: int = 12345,
    connector_number: int = 1,
    smart_device_uuid: str = "connector_uuid_1",
    serial: str | None = None,
    min_current: int = 6,
    max_current: int = 32,
) -> MagicMock:
    """Create a SmappeeDeviceHandle-like connector client with async service methods."""
    client = MagicMock(spec=SmappeeDeviceHandle)
    client.service_location_id = service_location_id
    client.connector_number = connector_number
    client.charging_station_serial = serial or f"SERIAL_{service_location_id}"
    client.serial = serial or f"SERIAL_{service_location_id}"
    client.smart_device_uuid = smart_device_uuid
    client.min_current = min_current
    client.max_current = max_current
    client.start_charging = AsyncMock()
    client.pause_charging = AsyncMock()
    client.stop_charging = AsyncMock()
    client.set_charging_mode = AsyncMock()
    client.set_current = AsyncMock()
    return client


def make_station_client(
    *,
    service_location_id: int = 317443,
    serial: str = "STATION123",
) -> MagicMock:
    """Create the station handle attributes entity metadata depends on."""
    station_client = MagicMock()
    station_client.service_location_id = service_location_id
    station_client.charging_station_serial = serial
    station_client.serial = serial
    station_client.serial_id = serial
    return station_client


def make_station_coordinator(
    *,
    station_client: object,
    station_state: StationState | None = None,
    connectors: dict[str, object] | None = None,
) -> MagicMock:
    """Create a coordinator with realistic metadata used by base entities."""
    coordinator = MagicMock(spec=SmappeeCoordinator)
    coordinator.station_client = station_client
    coordinator.site_name = "Home"
    coordinator.gateway_serial = "GATEWAY123"
    coordinator.gateway_type = "Infinity"
    coordinator.station_name = "Garage Charger"
    coordinator.station_model = "EV Wall Business"
    coordinator.last_update_success = True
    coordinator.data = IntegrationData(
        station=station_state or StationState(mqtt_connected=True),
        connectors=connectors or {},
    )
    return coordinator


def make_connector_runtime(
    *,
    connector_key: str = "connector_uuid_1",
    connector_uuid: str | None = None,
    connector_position: int | None = 1,
    connector_client: object | None = None,
) -> SmappeeConnectorRuntime:
    """Create a typed connector runtime container."""
    connector_uuid = connector_uuid or connector_key
    connector_client = connector_client or make_connector_client(
        connector_number=connector_position or 1,
        smart_device_uuid=connector_uuid,
    )
    return SmappeeConnectorRuntime(
        connector_key=connector_key,
        connector_uuid=connector_uuid,
        connector_position=connector_position,
        connector_client=connector_client,
    )


def make_led_runtime(
    *,
    led_key: str = "led-device-1",
    led_device_id: str | None = None,
    led_device_uuid: str | None = None,
    led_device_name: str | None = "LED Ring",
) -> SmappeeLedRuntime:
    """Create a typed LED runtime container."""
    return SmappeeLedRuntime(
        led_key=led_key,
        led_device_id=led_device_id or led_key,
        led_device_uuid=led_device_uuid,
        led_device_name=led_device_name,
    )


def make_station_runtime(
    *,
    site_location_id: int = 317418,
    control_location_id: int = 317443,
    station_uuid: str = "station-uuid",
    serial: str = "STATION123",
    station_client: object | None = None,
    coordinator: object = _DEFAULT_API,
    connectors: dict[str, SmappeeConnectorRuntime] | None = None,
    led_devices: dict[str, SmappeeLedRuntime] | None = None,
    site_coordinator: object | None = None,
    highlevel_configs: dict[int, dict[str, Any]] | None = None,
    site_name: str | None = "Typed Site",
    gateway_serial: str | None = "GATEWAY123",
    gateway_type: str | None = "Infinity",
    control_name: str | None = "Typed Station",
    control_function_type: str | None = "CHARGINGSTATION",
    station_name: str | None = "Typed Station",
    station_model: str | None = "EV Wall",
) -> SmappeeStationRuntime:
    """Create a typed station runtime container."""
    station_client = station_client or make_station_client(
        service_location_id=control_location_id,
        serial=serial,
    )
    if coordinator is _DEFAULT_API:
        coordinator = make_station_coordinator(
            station_client=station_client,
            connectors={},
        )
    return SmappeeStationRuntime(
        site_location_id=site_location_id,
        control_location_id=control_location_id,
        site_name=site_name,
        gateway_serial=gateway_serial,
        gateway_type=gateway_type,
        control_name=control_name,
        control_uuid=station_uuid,
        control_function_type=control_function_type,
        station_name=station_name,
        charging_station_serial=serial,
        charging_station_model=station_model,
        station_client=station_client,
        station_coordinator=coordinator,
        site_coordinator=site_coordinator,
        highlevel_configs=highlevel_configs or {},
        led_devices=led_devices or {},
        connectors=connectors or {},
    )


def make_site_runtime(
    *,
    site_location_id: int = 317418,
    site_name: str = "Typed Site",
    site_function_type: str | None = "SERVICELOCATION",
    site_uuid: str | None = "site-uuid",
    gateway_serial: str | None = "GATEWAY123",
    gateway_type: str | None = "Infinity",
    control_location_ids: list[int] | None = None,
    measurement_location_ids: list[int] | None = None,
    mqtt_clients: object | None = None,
    site_coordinator: object | None = None,
    highlevel_configs: dict[int, dict[str, Any]] | None = None,
    stations: dict[str, SmappeeStationRuntime] | None = None,
) -> SmappeeSiteRuntime:
    """Create a typed site runtime container."""
    return SmappeeSiteRuntime(
        site_location_id=site_location_id,
        site_name=site_name,
        site_function_type=site_function_type,
        site_uuid=site_uuid,
        gateway_serial=gateway_serial,
        gateway_type=gateway_type,
        control_location_ids=control_location_ids or [site_location_id],
        measurement_location_ids=measurement_location_ids or [site_location_id],
        highlevel_configs=highlevel_configs or {},
        mqtt_clients=mqtt_clients,
        site_coordinator=site_coordinator,
        stations=stations or {},
    )


def make_runtime_data(
    *,
    api: object = _DEFAULT_API,
    sites: dict[int, SmappeeSiteRuntime] | None = None,
    mqtt: dict[int, object] | None = None,
    dashboard: object | None = None,
    background_tasks: set | None = None,
) -> RuntimeData:
    """Create RuntimeData with the integration's current schema."""
    runtime = RuntimeData(
        api=MagicMock() if api is _DEFAULT_API else api,
        sites=sites or {},
        mqtt=mqtt or {},
    )
    runtime.dashboard = dashboard
    if background_tasks is not None:
        runtime.background_tasks = background_tasks
    return runtime


def make_runtime_for_connector(
    site_id: int,
    connector_client: object,
    *,
    station_uuid: str | None = None,
) -> RuntimeData:
    """Create a one-site runtime containing a single connector client."""
    station_uuid = station_uuid or f"station_{site_id}"
    connector_uuid = connector_client.smart_device_uuid
    coord = MagicMock()
    coord.data = IntegrationData(
        station=StationState(),
        connectors={
            connector_uuid: ConnectorState(
                connector_number=connector_client.connector_number,
                min_current=connector_client.min_current,
                max_current=connector_client.max_current,
            )
        },
    )
    return make_runtime_data(
        sites={
            site_id: make_site_runtime(
                site_location_id=site_id,
                stations={
                    station_uuid: make_station_runtime(
                        site_location_id=site_id,
                        control_location_id=site_id,
                        station_uuid=station_uuid,
                        station_client=make_station_client(service_location_id=site_id),
                        coordinator=coord,
                        connectors={
                            connector_uuid: make_connector_runtime(
                                connector_key=connector_uuid,
                                connector_uuid=connector_uuid,
                                connector_position=connector_client.connector_number,
                                connector_client=connector_client,
                            )
                        },
                    )
                },
            )
        },
    )


def make_loaded_entry_for_connector(
    entry_id: str,
    site_id: int,
    connector_client: object,
) -> MagicMock:
    """Create a loaded config entry with one connector runtime."""
    return make_loaded_config_entry(
        entry_id,
        make_runtime_for_connector(site_id, connector_client),
    )


def make_config_entry(
    *,
    runtime_data: RuntimeData | None = None,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    entry_id: str = "test_entry_id",
    title: str = "Smappee EV",
    domain: str = DOMAIN,
    state: object | None = ConfigEntryState.LOADED,
) -> MagicMock:
    """Create a ConfigEntry-like mock with commonly used attributes."""
    entry = MagicMock(spec=ConfigEntry)
    entry.runtime_data = runtime_data
    entry.data = data or {}
    entry.options = options or {}
    entry.entry_id = entry_id
    entry.title = title
    entry.domain = domain
    entry.state = state
    return entry


def make_loaded_config_entry(
    entry_id: str = "test_entry_id",
    runtime_data: RuntimeData | None = None,
    **kwargs: Any,
) -> MagicMock:
    """Create a loaded config entry mock."""
    return make_config_entry(
        entry_id=entry_id,
        runtime_data=runtime_data,
        state=ConfigEntryState.LOADED,
        **kwargs,
    )


def configure_loaded_entries(hass: object, entries: list[object]) -> None:
    """Wire hass.config_entries mocks to return a list of loaded entries."""
    hass.config_entries.async_entries.return_value = entries

    def get_entry_by_id(entry_id: str):
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    hass.config_entries.async_get_entry = get_entry_by_id
