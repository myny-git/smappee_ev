# custom_components/smappee_ev/data.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict


@dataclass
class ConnectorState:
    """Holds state for one connector."""
    connector_number: int
    session_state: str = "Initialize"
    selected_current_limit: Optional[int] = None
    selected_percentage_limit: Optional[int] = None
    selected_mode: str = "NORMAL"
    min_current: int = 6
    max_current: int = 32
    min_surpluspct: int = 100


@dataclass
class StationState:
    """Holds state for the station (applies to all connectors)."""
    led_brightness: int = 70
    available: bool = True


@dataclass
class IntegrationData:
    """Top-level state container for the integration."""
    station: StationState
    connectors: Dict[str, ConnectorState]  # keyed by UUID