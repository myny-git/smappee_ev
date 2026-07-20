"""Config entry runtime containers for the Smappee EV integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from ..api.mqtt_gateway import SmappeeMqtt
from .mqtt_diagnostics import MqttRoutingDiagnostics
from .state import HighLevelConfigMap

if TYPE_CHECKING:
    from ..api.device_handle import SmappeeDeviceHandle
    from ..coordinator import SmappeeSiteCoordinator, SmappeeStationCoordinator

type MqttRuntimeValue = SmappeeMqtt | list[SmappeeMqtt] | None


@dataclass
class SmappeeConnectorRuntime:
    """Runtime objects for one connector."""

    connector_key: str
    connector_uuid: str | None
    connector_position: int | None
    connector_client: SmappeeDeviceHandle


@dataclass
class SmappeeLedRuntime:
    """Runtime objects for one LED controller."""

    led_key: str
    led_device_id: str | None
    led_device_uuid: str | None = None
    led_device_name: str | None = None


@dataclass
class SmappeeStationRuntime:
    """Runtime objects for one charging station."""

    site_location_id: int
    control_location_id: int
    site_name: str | None
    gateway_serial: str | None
    gateway_type: str | None
    control_name: str | None
    control_uuid: str | None
    control_function_type: str | None
    station_name: str | None
    charging_station_serial: str
    charging_station_model: str | None
    station_client: SmappeeDeviceHandle
    station_coordinator: SmappeeStationCoordinator | None
    mqtt: MqttRuntimeValue = None
    site_coordinator: SmappeeSiteCoordinator | None = None
    highlevel_configs: HighLevelConfigMap = field(default_factory=dict)
    led_devices: dict[str, SmappeeLedRuntime] = field(default_factory=dict)
    connectors: dict[str, SmappeeConnectorRuntime] = field(default_factory=dict)


@dataclass
class SmappeeSiteRuntime:
    """Runtime objects for one site/service location."""

    site_location_id: int
    site_name: str | None
    site_function_type: str | None
    site_uuid: str | None
    gateway_serial: str | None
    gateway_type: str | None
    control_location_ids: list[int] = field(default_factory=list)
    measurement_location_ids: list[int] = field(default_factory=list)
    highlevel_configs: HighLevelConfigMap = field(default_factory=dict)
    mqtt_clients: MqttRuntimeValue = None
    site_coordinator: SmappeeSiteCoordinator | None = None
    stations: dict[str, SmappeeStationRuntime] = field(default_factory=dict)


@dataclass
class RuntimeData:
    """Runtime storage placed on ConfigEntry.runtime_data."""

    api: object
    sites: dict[int, SmappeeSiteRuntime]
    mqtt: dict[int, MqttRuntimeValue]
    dashboard: object | None = None
    background_tasks: set[asyncio.Task] = field(default_factory=set)
    mqtt_diagnostics: dict[int, list[MqttRoutingDiagnostics]] = field(default_factory=dict)
    shutdown_task: asyncio.Task[None] | None = None


type SmappeeEvConfigEntry = ConfigEntry[RuntimeData]
