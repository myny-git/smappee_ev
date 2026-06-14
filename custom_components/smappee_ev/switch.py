from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import UpdateFailed

from .base_entities import SmappeeConnectorEntity, SmappeeStationRestEntity
from .coordinator import SmappeeCoordinator
from .data import IntegrationData, SmappeeEvConfigEntry, StationState
from .device_handle import SmappeeDeviceHandle
from .helpers import build_connector_label

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


# station_serial no longer needed; provided by base entity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV switches (multi-station)."""
    runtime = config_entry.runtime_data
    sites = runtime.sites

    entities: list[SwitchEntity] = []
    for sid, site in (sites or {}).items():
        stations = (site or {}).get("stations", {})
        for st_uuid, bucket in (stations or {}).items():
            coord: SmappeeCoordinator = bucket["coordinator"]
            st_client: SmappeeDeviceHandle = bucket["station_client"]
            conns: dict[str, SmappeeDeviceHandle] = bucket.get("connector_clients", {})

            if conns:
                # Station-level switch
                entities.append(
                    SmappeeAvailabilitySwitch(
                        coordinator=coord,
                        api_client=st_client,
                        sid=sid,
                        station_uuid=st_uuid,
                    )
                )

                # Connector-level switches
                for cuuid, client in (conns or {}).items():
                    entities.append(
                        SmappeeChargingSwitch(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                        )
                    )

    async_add_entities(entities, False)


# ====================================================================================
# Charging ON/OFF (acts like a start/pause toggle on the connector)
# ====================================================================================


class SmappeeChargingSwitch(SmappeeConnectorEntity, SwitchEntity, RestoreEntity):
    """Switch to control start/pause charging on a specific connector."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
    ) -> None:
        num_lbl = build_connector_label(api_client, connector_uuid).split(" ", 1)[1]
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            api_client,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix="switch:charging",
            name=f"Connector {num_lbl} EVCC charging",
        )
        self.api_client = api_client
        self._is_on = False

    # ---------- HA hooks ----------

    @property
    def is_on(self) -> bool:
        """Show last EVCC intent only (not physical session state)."""
        return self._is_on

    @property
    def icon(self) -> str:
        return "mdi:ev-station" if self.is_on else "mdi:ev-station-disabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Restore last EVCC intent across restarts
        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == "on"

    # ---------- Actions ----------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set charging mode to STANDARD via the configured API path."""
        try:
            _LOGGER.debug(
                "Charging switch ON â†’ STANDARD (sid=%s, uuid=%s)",
                self._sid,
                self.connector_uuid,
            )
            await self.api_client.set_charging_mode("STANDARD")
            st = self._conn_state
            if st:
                st.selected_mode = "STANDARD"
                data = self.coordinator.data
                if data:
                    self.coordinator.async_set_updated_data(data)
            self.coordinator.async_schedule_dashboard_refresh()
            self._is_on = True
            self.async_write_ha_state()
        except asyncio.CancelledError:
            raise
        except (ClientError, TimeoutError, HomeAssistantError, UpdateFailed) as err:
            _LOGGER.warning("Failed to start charging on %s: %s", self.connector_uuid, err)
            self.async_write_ha_state()
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause charging via the configured API path."""
        try:
            _LOGGER.debug(
                "Charging switch OFF â†’ pause (sid=%s, uuid=%s)",
                self._sid,
                self.connector_uuid,
            )
            await self.api_client.pause_charging()
            self.coordinator.async_schedule_dashboard_refresh()
            self._is_on = False
            self.async_write_ha_state()
        except asyncio.CancelledError:
            raise
        except (ClientError, TimeoutError, HomeAssistantError, UpdateFailed) as err:
            _LOGGER.warning("Failed to pause charging on %s: %s", self.connector_uuid, err)
            self.async_write_ha_state()
            raise


# ====================================================================================
# Availability ON/OFF (expose/take connector available for charging)
# ====================================================================================


class SmappeeAvailabilitySwitch(SmappeeStationRestEntity, SwitchEntity):
    """Switch to toggle station availability (acchargingstation action)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,  # <-- station client
        sid: int,
        station_uuid: str,
    ) -> None:
        SmappeeStationRestEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix="switch:station_available",
            name="Station available",
        )
        self.api_client = api_client

    def _station_state(self) -> StationState | None:
        data: IntegrationData | None = self.coordinator.data
        return data.station if data else None

    @property
    def is_on(self) -> bool:
        st = self._station_state()
        return bool(getattr(st, "available", True)) if st else True

    @property
    def icon(self) -> str:
        return "mdi:ev-station" if self.is_on else "mdi:ev-station-disabled"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_available(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_available(False)

    async def _set_available(self, value: bool) -> None:
        data: IntegrationData | None = self.coordinator.data
        st = self._station_state()
        prev = getattr(st, "available", None) if st else None

        if data and st is not None and prev != value:
            st.available = value
            self.coordinator.async_set_updated_data(data)

        try:
            if value:
                await self.api_client.set_available()
            else:
                await self.api_client.set_unavailable()
            self.coordinator.async_schedule_dashboard_refresh()
        except Exception as err:
            _LOGGER.warning("Set station availability failed (sid=%s): %s", self._sid, err)
            # revert optimistic update
            if data and st is not None and prev is not None:
                st.available = prev
                self.coordinator.async_set_updated_data(data)
            raise
