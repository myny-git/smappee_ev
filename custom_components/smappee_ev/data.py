# custom_components/smappee_ev/data.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConnectorState:
    """Holds state for one connector."""

    connector_number: int
    session_state: str = "Initialize"
    selected_current_limit: int | None = None
    selected_percentage_limit: int | None = None
    selected_mode: str = "NORMAL"
    min_current: int = 6
    max_current: int = 32
    min_surpluspct: int = 100

    connection_status: str | None = None  # CONNECTED / DISCONNECTED
    configuration_errors: list[str] | None = None

    iec_status: str | None = None  # "A1" / "B1" / "C1"
    available: bool = True  # connector-level availability
    session_cause: str | None = None
    stopped_by_cloud: bool | None = None

    # Modus/strategy + returned UI-modus en paused-overlay
    raw_charging_mode: str | None = None  # NORMAL / SMART / PAUSED
    optimization_strategy: str | None = None  # NONE / EXCESS_ONLY / SCHEDULES_FIRST_THEN_EXCESS
    ui_mode_base: str | None = None  # NORMAL / STANDARD / SOLAR
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

    led_brightness: int = 70
    available: bool = True

    mqtt_connected: bool | None = None
    last_mqtt_rx: float | None = None

    grid_power_total: int | None = None
    grid_power_phases: list[int] | None = None
    grid_energy_import_kwh: float | None = None
    grid_energy_export_kwh: float | None = None
    grid_current_phases: float | None = None

    house_consumption_power: int | None = None

    pv_power_total: int | None = None
    pv_power_phases: list[int] | None = None
    pv_energy_import_kwh: float | None = None
    pv_current_phases: float | None = None


@dataclass
class IntegrationData:
    """Top-level state container for the integration."""

    station: StationState
    connectors: dict[str, ConnectorState]  # keyed by UUID
