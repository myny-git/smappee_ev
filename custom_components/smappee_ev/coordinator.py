from __future__ import annotations

import asyncio
import logging

from datetime import timedelta
from typing import Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .data import IntegrationData, StationState, ConnectorState
from .api_client import SmappeeApiClient

_LOGGER = logging.getLogger(__name__)


class SmappeeCoordinator(DataUpdateCoordinator[IntegrationData]):
    """Coordinator that fetches Smappee EV state via api_client.delayed_update()."""

    def __init__(
        self,
        hass: HomeAssistant,
        station_client: SmappeeApiClient,
        connector_clients: dict[str, SmappeeApiClient],  # keyed by UUID
        update_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Smappee EV Coordinator",
            update_interval=timedelta(seconds=update_interval),
        )
        self.station_client = station_client
        self.connector_clients = connector_clients

    async def _async_update_data(self) -> IntegrationData:
        """Fetch the latest data from the API."""
        try:
            # --- Station update ---
            await self.station_client.delayed_update()
            station_state = StationState(
                led_brightness=self.station_client.led_brightness,
                available=getattr(self.station_client, "available", True),
            )

            # --- Connector updates in parallel ---
            pairs = list(self.connector_clients.items())  # [(uuid, client), ...]
            coros = [client.delayed_update() for _, client in pairs]  # No create_task needed
            results = await asyncio.gather(*coros, return_exceptions=True)

            connectors_state: Dict[str, ConnectorState] = {}

            for (uuid, client), res in zip(pairs, results):
                if isinstance(res, Exception):
                    _LOGGER.warning("Connector %s update failed: %s", uuid, res)
                    # fall back to client's last-known values

                connectors_state[uuid] = ConnectorState(
                    connector_number=getattr(client, "connector_number", 1),
                    session_state=getattr(client, "session_state", "Initialize"),
                    selected_current_limit=getattr(client, "selected_current_limit", None),
                    selected_percentage_limit=getattr(client, "selected_percentage_limit", None),
                    selected_mode=getattr(client, "selected_mode", "NORMAL"),
                    min_current=getattr(client, "min_current", 6),
                    max_current=getattr(client, "max_current", 32),
                    min_surpluspct=getattr(client, "min_surpluspct", 100),
                )

            return IntegrationData(
                station=station_state,
                connectors=connectors_state,
            )

        except Exception as err:
            raise UpdateFailed(f"Error fetching Smappee data: {err}") from err
