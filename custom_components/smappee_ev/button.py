from __future__ import annotations

import logging
from typing import cast

from aiohttp import ClientError
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base_entities import SmappeeConnectorEntity, SmappeeStationEntity
from .const import DOMAIN
from .coordinator import SmappeeCoordinator
from .data import SmappeeEvConfigEntry
from .device_handle import SmappeeDeviceHandle

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


def _connector_action_error(method_name: str, err: BaseException) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="connector_service_failed",
        translation_placeholders={"method_name": method_name, "error": str(err)},
    )


def _station_action_error(method_name: str, err: BaseException) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="station_service_failed",
        translation_placeholders={"method_name": method_name, "error": str(err)},
    )


def _dashboard_mode(mode: str | None) -> str | None:
    """Return a Dashboard v10 charging mode, accepting legacy/restored labels."""
    mode_up = str(mode or "").upper()
    if mode_up in {"STANDARD", "NORMAL"}:
        return "STANDARD"
    if mode_up in {"SMART", "SOLAR"}:
        return mode_up
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmappeeEvConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smappee EV buttons (multi-station)."""
    runtime = config_entry.runtime_data

    entities: list[ButtonEntity] = []
    for sid, site in runtime.sites.items():
        for st_uuid, bucket in site.stations.items():
            coord = cast(SmappeeCoordinator | None, bucket.station_coordinator)
            if coord is None:
                continue
            st_client = cast(
                SmappeeDeviceHandle | None,
                bucket.station_client or getattr(coord, "station_client", None),
            )
            conns = cast(
                dict[str, SmappeeDeviceHandle],
                {key: conn.connector_client for key, conn in bucket.connectors.items()},
            )

            if st_client is not None:
                entities.append(
                    SmappeeStationActionButton(
                        coordinator=coord,
                        api_client=st_client,
                        sid=sid,
                        station_uuid=st_uuid,
                        action="restart_charging_station",
                    )
                )

            for cuuid, client in (conns or {}).items():
                entities.extend(
                    [
                        SmappeeActionButton(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                            action="start_charging",
                        ),
                        SmappeeActionButton(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                            action="pause_charging",
                        ),
                        SmappeeActionButton(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                            action="stop_charging",
                        ),
                        SmappeeActionButton(
                            coordinator=coord,
                            api_client=client,
                            sid=sid,
                            station_uuid=st_uuid,
                            connector_uuid=cuuid,
                            action="resume_charging",
                        ),
                    ]
                )

    async_add_entities(entities, False)


class SmappeeStationActionButton(SmappeeStationEntity, ButtonEntity):
    """Generic action button for a station using shared base entity."""

    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        action: str,
        name: str | None = None,
    ) -> None:
        SmappeeStationEntity.__init__(
            self,
            coordinator,
            sid,
            station_uuid,
            unique_suffix=f"button:{action}",
        )
        self._attr_translation_key = action
        if name is not None:
            self._attr_name = name
        self.api_client = api_client
        self._action = action

    async def async_press(self) -> None:
        """Execute the action on press."""
        if self._action == "restart_charging_station":
            try:
                await self.api_client.restart_charging_station()
            except (ClientError, TimeoutError, RuntimeError, ValueError) as err:
                raise _station_action_error("restart_charging_station", err) from err
            self.coordinator.async_schedule_dashboard_refresh()
        else:
            _LOGGER.debug("Unknown station action for button: %s", self._action)


class SmappeeActionButton(SmappeeConnectorEntity, ButtonEntity):
    """Generic action button for a connector using shared base entity."""

    def __init__(
        self,
        *,
        coordinator: SmappeeCoordinator,
        api_client: SmappeeDeviceHandle,
        sid: int,
        station_uuid: str,
        connector_uuid: str,
        action: str,
        name: str | None = None,
    ) -> None:
        # Build unique id via base class
        SmappeeConnectorEntity.__init__(
            self,
            coordinator,
            api_client,
            sid,
            station_uuid,
            connector_uuid,
            unique_suffix=f"button:{action}",
        )
        self._attr_translation_key = action
        if name is not None:
            self._attr_name = name
        self.api_client = api_client
        self._action = action

    async def async_press(self) -> None:
        """Execute the action on press."""
        if self._action == "start_charging":
            try:
                await self.api_client.start_charging()
            except (ClientError, TimeoutError, RuntimeError, ValueError) as err:
                raise _connector_action_error("start_charging", err) from err
            self.coordinator.async_schedule_dashboard_refresh()
        elif self._action == "pause_charging":
            try:
                await self.api_client.pause_charging()
            except (ClientError, TimeoutError, RuntimeError, ValueError) as err:
                raise _connector_action_error("pause_charging", err) from err
            self.coordinator.async_schedule_dashboard_refresh()
        elif self._action == "stop_charging":
            try:
                await self.api_client.stop_charging()
            except (ClientError, TimeoutError, RuntimeError, ValueError) as err:
                raise _connector_action_error("stop_charging", err) from err
            self.coordinator.async_schedule_dashboard_refresh()
        elif self._action == "resume_charging":
            data = self.coordinator.data if self.coordinator else None
            mode = "STANDARD"
            if data and self.connector_uuid in (data.connectors or {}):
                conn = data.connectors[self.connector_uuid]
                mode = (
                    _dashboard_mode(getattr(conn, "selected_mode", None))
                    or _dashboard_mode(getattr(conn, "ui_mode_base", None))
                    or "STANDARD"
                )
            try:
                await self.api_client.set_charging_mode(mode)
            except (ClientError, TimeoutError, RuntimeError, ValueError) as err:
                raise _connector_action_error("set_charging_mode", err) from err
            self.coordinator.async_schedule_dashboard_refresh()
        else:
            _LOGGER.debug("Unknown action for button: %s", self._action)
