# custom_components/smappee_ev/data.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT


@dataclass
class ConnectorState:
    """Holds state for one connector."""

    connector_number: int
    session_state: str = "Initialize"
    selected_current_limit: float | None = None
    selected_percentage_limit: int | None = None
    selected_mode: str | None = None
    min_current: int = DEFAULT_MIN_CURRENT
    max_current: int = DEFAULT_MAX_CURRENT
    min_surpluspct: int | None = None
    support_grid: int | None = None
    dashboard_device_id: str | None = None
    dashboard_device_uuid: str | None = None
    dashboard_device_name: str | None = None

    connection_status: str | None = None  # CONNECTED / DISCONNECTED
    configuration_errors: list[str] | None = None

    iec_status: str | None = None  # "A1" / "B1" / "C1"
    available: bool = True  # Smappee domain status: connector available for charging
    api_available: bool = True  # HA reachability: last connector REST fetch succeeded
    session_cause: str | None = None
    stopped_by_cloud: bool | None = None

    # Modus/strategy + returned UI-modus en paused-overlay
    raw_charging_mode: str | None = None  # NORMAL / SMART / PAUSED
    optimization_strategy: str | None = None  # NONE / EXCESS_ONLY / SCHEDULES_FIRST_THEN_EXCESS
    ui_mode_base: str | None = None  # STANDARD / SMART / SOLAR
    paused: bool = False  # overlay

    status_current: str | None = None

    # EVCC letter/code (return from IEC of chargingState)
    evcc_state: str | None = None  # "A" / "B" / "C"
    evcc_state_code: int | None = None  # 0(A) / 1(B) / 2(C)

    power_phases: list[int] | None = None  # [W_L1, W_L2, W_L3] (missing phases 0)
    current_phases: list[float] | None = None  # [A_L1, A_L2, A_L3]
    energy_import_kwh: float | None = None  # cumulative (kWh)
    power_total: int | None = None


@dataclass
class StationState:
    """Holds state for the station (applies to all connectors)."""

    led_brightness: int | None = None
    dashboard_led_device_id: str | None = None
    available: bool = True
    api_available: bool = True
    dashboard_available: bool | None = None
    station_features: list[str] = field(default_factory=list)
    maximum_capacity_a: int | None = None
    offline_charging_enabled: bool | None = None
    offline_failsafe_current_a: int | None = None
    capacity_protection_active: bool | None = None
    capacity_maximum_power_kw: float | None = None
    overload_protection_active: bool | None = None
    overload_maximum_load_a: int | None = None
    dashboard_charging_station_details: dict[str, Any] | None = None

    mqtt_connected: bool | None = None
    last_mqtt_rx: float | None = None

    grid_power_total: int | None = None
    grid_power_phases: list[int] | None = None
    grid_energy_import_kwh: float | None = None
    grid_energy_export_kwh: float | None = None
    # list of per-phase currents (A)
    grid_current_phases: list[float] | None = None
    # list of per-phase voltages (V, integer)
    grid_voltage_phases: list[int] | None = None

    house_consumption_power: int | None = None

    pv_power_total: int | None = None
    pv_power_phases: list[int] | None = None
    pv_energy_import_kwh: float | None = None
    pv_current_phases: list[float] | None = None


@dataclass
class IntegrationData:
    """Top-level state container for the integration."""

    station: StationState
    connectors: dict[str, ConnectorState]  # keyed by UUID
    recent_sessions: list[dict] = field(default_factory=list)


@dataclass
class RuntimeData:
    """Runtime storage placed on ConfigEntry.runtime_data.

    Keeps the public objects platforms need without depending on hass.data.
    """

    api: object  # Dashboard client (kept generic to avoid circular import in type checking)
    sites: dict[int, dict]
    mqtt: dict[int, object]  # service_location_id -> SmappeeMqtt
    dashboard: object | None = None


type SmappeeEvConfigEntry = ConfigEntry[RuntimeData]
