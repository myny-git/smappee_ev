# custom_components/smappee_ev/coordinator.py
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import SmappeeApiClient
from .const import BASE_URL
from .data import ConnectorState, IntegrationData, StationState

_LOGGER = logging.getLogger(__name__)


class SmappeeCoordinator(DataUpdateCoordinator[IntegrationData]):
    """Single source of truth: fetch station + all connector state here."""

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
        # reuse HA's client session via any existing client
        self._session: ClientSession = station_client.get_http_session()
        self._timeout = ClientTimeout(connect=5, total=15)

    async def _async_update_data(self) -> IntegrationData:
        try:
            # refresh auth once; commands will refresh again when needed
            await self.station_client.ensure_auth()

            # ---- Station snapshot (LED brightness) ----
            station_state = await self._fetch_station_state(self.station_client)

            # ---- Connectors in parallel ----
            pairs = list(self.connector_clients.items())  # [(uuid, client), ...]
            coros = [self._fetch_connector_state(client) for _, client in pairs]
            results = await asyncio.gather(*coros, return_exceptions=True)

            connectors_state: dict[str, ConnectorState] = {}
            for (uuid, client), res in zip(pairs, results):
                if isinstance(res, Exception):
                    _LOGGER.warning("Connector %s update failed: %s", uuid, res)
                    # Fall back to safe defaults
                    connectors_state[uuid] = ConnectorState(
                        connector_number=getattr(client, "connector_number", 1),
                        session_state="Initialize",
                        selected_current_limit=None,
                        selected_percentage_limit=None,
                        selected_mode=getattr(client, "selected_mode", "NORMAL"),
                        min_current=6,
                        max_current=32,
                        min_surpluspct=100,
                    )
                else:
                    connectors_state[uuid] = res

            return IntegrationData(station=station_state, connectors=connectors_state)

        except Exception as err:
            raise UpdateFailed(f"Error fetching Smappee data: {err}") from err

    # -----------------------------
    # Helpers (pure HTTP + parsing)
    # -----------------------------
    async def _fetch_station_state(self, client: SmappeeApiClient) -> StationState:
        """Read LED brightness by scanning all smartdevices for the station."""
        headers = client.auth_headers()
        url_all = f"{BASE_URL}/servicelocation/{client.service_location_id}/smartdevices"

        led_brightness = 70
        try:
            resp = await self._session.get(url_all, headers=headers, timeout=self._timeout)
            if resp.status == 200:
                devices = await resp.json()
                for dev in devices:
                    for prop in dev.get("configurationProperties", []):
                        spec = prop.get("spec", {}) or {}
                        if spec.get("name") == "etc.smart.device.type.car.charger.led.config.brightness":
                            raw = prop.get("value")
                            val = raw.get("value") if isinstance(raw, dict) else raw
                            try:
                                led_brightness = int(val)
                            except (TypeError, ValueError):
                                pass
                            break
            else:
                txt = await resp.text()
                _LOGGER.debug("Station brightness fetch status=%s body=%s", resp.status, txt)
        except (TimeoutError, ClientError, asyncio.CancelledError) as err:
            _LOGGER.debug("Station brightness fetch exception: %s", err)

        return StationState(led_brightness=led_brightness, available=True)

    async def _fetch_connector_state(self, client: SmappeeApiClient) -> ConnectorState:
        """Read one connector's properties/config from its smartdevice."""
        await client.ensure_auth()
        headers = client.auth_headers()
        url_dev = f"{BASE_URL}/servicelocation/{client.service_location_id}/smartdevices/{client.smart_device_id}"

        # Defaults, will be overwritten by API values when present
        session_state = getattr(client, "session_state", "Initialize")
        selected_percentage = getattr(client, "selected_percentage_limit", None)
        selected_current = getattr(client, "selected_current_limit", None)
        selected_mode = getattr(client, "selected_mode", "NORMAL")
        min_current = getattr(client, "min_current", 6)
        max_current = getattr(client, "max_current", 32)
        min_surpluspct = getattr(client, "min_surpluspct", 100)

        resp = await self._session.get(url_dev, headers=headers, timeout=self._timeout)
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"smartdevice fetch {client.smart_device_id} failed: {txt}")

        data = await resp.json()

        # properties: chargingState, percentageLimit
        for prop in data.get("properties", []):
            spec = prop.get("spec", {}) or {}
            name = spec.get("name")
            val = prop.get("value")
            if name == "chargingState":
                session_state = val or session_state
            elif name == "percentageLimit":
                try:
                    selected_percentage = int(val)
                except (TypeError, ValueError):
                    pass

        # configurationProperties: max/min current, min.excesspct
        for prop in data.get("configurationProperties", []):
            spec = prop.get("spec", {}) or {}
            name = spec.get("name")
            raw = prop.get("value")
            val = raw.get("value") if isinstance(raw, dict) else raw
            if name == "etc.smart.device.type.car.charger.config.max.current":
                try:
                    max_current = int(val)
                except (TypeError, ValueError):
                    pass
            elif name == "etc.smart.device.type.car.charger.config.min.current":
                try:
                    min_current = int(val)
                except (TypeError, ValueError):
                    pass
            elif name == "etc.smart.device.type.car.charger.config.min.excesspct":
                try:
                    min_surpluspct = int(val)
                except (TypeError, ValueError):
                    pass

        # If we know %, but not A, we can reconstruct A later in the Number entity;
        # here we just return the snapshot.
        return ConnectorState(
            connector_number=getattr(client, "connector_number", 1),
            session_state=session_state,
            selected_current_limit=selected_current,
            selected_percentage_limit=selected_percentage,
            selected_mode=selected_mode,
            min_current=min_current,
            max_current=max_current,
            min_surpluspct=min_surpluspct,
        )
