"""Coordinator logic for Smappee EV service locations and charging stations."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import timedelta
import logging
import re
from time import time as _now
from typing import Any

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.dashboard_client import SmappeeDashboardClient
from .api.device_handle import SmappeeDeviceHandle
from .const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT
from .coordinators.dashboard import DashboardMixin
from .coordinators.mqtt import MqttMixin
from .coordinators.power import (
    PowerMixin,
    _active_power_values,
    _amps_from_ma,
    _empty_power_topic_map,
    _indexes_and_field_from_aspect_paths,
    _indexes_from_aspect_paths,
    _mqtt_channel_topic,
    _pick,
    _to_int,
    _volts_from_dv,
)
from .coordinators.session_tracking import SessionTrackingMixin
from .helpers import anonymize_uuid
from .models.state import (
    ConnectorState,
    DashboardObject,
    HighLevelConfigMap,
    IntegrationData,
    SiteData,
    SiteState,
    StationState,
)

_LOGGER = logging.getLogger(__name__)


class SmappeeSiteCoordinator(DataUpdateCoordinator[SiteData]):
    """Single source of truth for one site/service-location MQTT state."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        site_location_id: int,
        site_name: str,
        site_uuid: str | None,
        gateway_serial: str | None,
        gateway_type: str | None,
        update_interval: int,
        config_entry: ConfigEntry[Any] | None = None,
        highlevel_configs: HighLevelConfigMap | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Smappee EV Site Coordinator {site_location_id}",
            update_interval=timedelta(seconds=update_interval),
            config_entry=config_entry,
        )
        self.site_location_id = int(site_location_id)
        self.site_name = site_name
        self.site_uuid = site_uuid
        self.gateway_serial = gateway_serial
        self.gateway_type = gateway_type
        self._highlevel_configs = highlevel_configs or {}
        self._power_index_maps_by_topic: dict[str, DashboardObject] | None = None

    async def _async_update_data(self) -> SiteData:
        await self._ensure_power_index_map()
        return self.data or SiteData(site=SiteState())

    async def _ensure_power_index_map(self) -> None:
        if self._power_index_maps_by_topic is not None:
            return
        mapping = self._build_measurement_index_maps_by_topic_from_highlevel_configs(
            self._highlevel_configs
        )
        if mapping:
            self._power_index_maps_by_topic = mapping

    def _build_measurement_index_maps_by_topic_from_highlevel_configs(
        self, configs: HighLevelConfigMap
    ) -> dict[str, DashboardObject] | None:
        maps_by_topic: dict[str, DashboardObject] = {}
        for cfg in configs.values():
            mapping = self._build_measurement_index_maps_by_topic_from_highlevel(cfg)
            if not mapping:
                continue
            for topic, topic_map in mapping.items():
                merged = maps_by_topic.setdefault(topic, _empty_power_topic_map())
                for role in ("grid", "pv"):
                    for key in ("power", "power_field", "current", "energy"):
                        value = topic_map[role].get(key)
                        if value and not merged[role].get(key):
                            merged[role][key] = value
        return maps_by_topic or None

    def _build_measurement_index_maps_by_topic_from_highlevel(
        self, cfg: DashboardObject
    ) -> dict[str, DashboardObject] | None:
        maps_by_topic: dict[str, DashboardObject] = {}
        for meas in cfg.get("measurements") or []:
            if not isinstance(meas, dict):
                continue
            channels = meas.get("updateChannels") or {}
            if not isinstance(channels, dict):
                continue
            power_idx, power_field = _indexes_and_field_from_aspect_paths(
                channels.get("activePower"), "activePowerData", "channelData"
            )
            power_topic = _mqtt_channel_topic(channels.get("activePower"))
            current_idx = _indexes_from_aspect_paths(channels.get("current"), "currentData")
            current_topic = _mqtt_channel_topic(channels.get("current"))
            energy_idx = _indexes_from_aspect_paths(
                channels.get("meterReadings"), "importActiveEnergyData"
            )
            energy_topic = _mqtt_channel_topic(channels.get("meterReadings"))
            mtype = str(meas.get("type") or "").upper()
            if mtype == "GRID":
                if power_topic:
                    topic_map = maps_by_topic.setdefault(power_topic, _empty_power_topic_map())
                    topic_map["grid"]["power"] = power_idx
                    topic_map["grid"]["power_field"] = power_field
                if current_topic:
                    topic_map = maps_by_topic.setdefault(current_topic, _empty_power_topic_map())
                    topic_map["grid"]["current"] = current_idx
                if energy_topic:
                    topic_map = maps_by_topic.setdefault(energy_topic, _empty_power_topic_map())
                    topic_map["grid"]["energy"] = energy_idx
                continue
            if mtype == "PRODUCTION":
                if power_topic:
                    topic_map = maps_by_topic.setdefault(power_topic, _empty_power_topic_map())
                    topic_map["pv"]["power"] = power_idx
                    topic_map["pv"]["power_field"] = power_field
                if current_topic:
                    topic_map = maps_by_topic.setdefault(current_topic, _empty_power_topic_map())
                    topic_map["pv"]["current"] = current_idx
                if energy_topic:
                    topic_map = maps_by_topic.setdefault(energy_topic, _empty_power_topic_map())
                    topic_map["pv"]["energy"] = energy_idx
        return maps_by_topic or None

    def _set_if_changed(self, obj: object, attr: str, value) -> bool:
        if value is None:
            return False
        cur = getattr(obj, attr, None)
        if value != cur:
            setattr(obj, attr, value)
            return True
        return False

    def _apply_site_group(
        self,
        site: SiteState,
        payload: dict,
        power_idxs: list[int],
        current_idxs: list[int],
        energy_idxs: list[int],
        power_key_prefix: str,
        power_field: str | None = None,
    ) -> bool:
        changed = False
        active = _active_power_values(payload, power_field)
        currents_ma = payload.get("currentData") or []
        voltage_dv = payload.get("phaseVoltageData") or []
        imp_wh = payload.get("importActiveEnergyData") or []
        exp_wh = payload.get("exportActiveEnergyData") or []
        p_ph = _pick(active, power_idxs)
        if p_ph:
            changed |= self._set_if_changed(site, f"{power_key_prefix}_power_phases", p_ph)
            changed |= self._set_if_changed(site, f"{power_key_prefix}_power_total", sum(p_ph))
            i_ph = _amps_from_ma(_pick(currents_ma, current_idxs or power_idxs))
            if i_ph:
                changed |= self._set_if_changed(site, f"{power_key_prefix}_current_phases", i_ph)
        if power_key_prefix == "grid":
            v_ph = _volts_from_dv(_pick(voltage_dv, [0, 1, 2]))
            if v_ph:
                changed |= self._set_if_changed(site, "grid_voltage_phases", v_ph)
        if energy_idxs:
            if power_key_prefix == "grid":
                changed |= self._set_if_changed(
                    site,
                    "grid_energy_import_kwh",
                    round(sum(_pick(imp_wh, energy_idxs)) / 1000.0, 3),
                )
                changed |= self._set_if_changed(
                    site,
                    "grid_energy_export_kwh",
                    round(sum(_pick(exp_wh, energy_idxs)) / 1000.0, 3),
                )
            else:
                changed |= self._set_if_changed(
                    site,
                    "pv_energy_import_kwh",
                    round(sum(_pick(imp_wh, energy_idxs)) / 1000.0, 3),
                )
        return changed

    def _handle_power(self, topic: str, payload: dict) -> bool:
        data = self.data
        if not data:
            return False
        idx_map = (self._power_index_maps_by_topic or {}).get(topic)
        if not idx_map:
            return False
        site = data.site
        changed = False
        grid = idx_map.get("grid", {})
        pv = idx_map.get("pv", {})
        changed |= self._apply_site_group(
            site,
            payload,
            grid.get("power", []),
            grid.get("current", []),
            grid.get("energy", []),
            "grid",
            grid.get("power_field"),
        )
        changed |= self._apply_site_group(
            site,
            payload,
            pv.get("power", []),
            pv.get("current", []),
            pv.get("energy", []),
            "pv",
            pv.get("power_field"),
        )
        cp = payload.get("consumptionPower")
        if isinstance(cp, int | float):
            changed |= self._set_if_changed(site, "house_consumption_power", int(cp))
        sp = payload.get("solarPower")
        if isinstance(sp, int | float):
            changed |= self._set_if_changed(site, "pv_power_total", int(sp))
        always_on = payload.get("alwaysOnPower")
        if isinstance(always_on, int | float):
            changed |= self._set_if_changed(site, "always_on_power", int(always_on))
        return changed

    def apply_mqtt_connection_change(self, up: bool) -> None:
        data = self.data
        if not data:
            return
        site = data.site
        changed = False
        site.last_mqtt_rx = _now()
        if up and not getattr(site, "mqtt_connected", False):
            site.mqtt_connected = True
            changed = True
            _LOGGER.info("Site %s MQTT availability recovered", anonymize_uuid(self.site_uuid))
        elif not up and getattr(site, "mqtt_connected", None) is not False:
            site.mqtt_connected = False
            changed = True
            _LOGGER.info("Site %s MQTT unavailable", anonymize_uuid(self.site_uuid))
        if changed:
            self.async_set_updated_data(data)

    def apply_mqtt_properties(self, topic: str, payload: dict) -> None:
        data = self.data
        if not data:
            return
        data.site.last_mqtt_rx = _now()
        changed = False
        if not getattr(data.site, "mqtt_connected", False):
            data.site.mqtt_connected = True
            changed = True
        if topic.endswith("/power"):
            changed |= self._handle_power(topic, payload)
        if changed:
            self.async_set_updated_data(data)


class SmappeeStationCoordinator(
    SessionTrackingMixin,
    MqttMixin,
    PowerMixin,
    DashboardMixin,
    DataUpdateCoordinator[IntegrationData],
):
    """Single source of truth: fetch station + all connector state here."""

    def __init__(
        self,
        hass: HomeAssistant,
        station_client: SmappeeDeviceHandle,
        connector_clients: dict[str, SmappeeDeviceHandle],  # keyed by UUID
        update_interval: int,
        config_entry: ConfigEntry[Any] | None = None,
        dashboard_client: SmappeeDashboardClient | None = None,
        highlevel_configs: HighLevelConfigMap | None = None,
        site_name: str | None = None,
        gateway_serial: str | None = None,
        gateway_type: str | None = None,
        station_name: str | None = None,
        station_model: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Smappee EV Coordinator",
            update_interval=timedelta(seconds=update_interval),
            config_entry=config_entry,
        )
        self.station_client = station_client
        self.connector_clients = connector_clients
        self.dashboard_client = dashboard_client
        self._highlevel_configs = highlevel_configs or {}
        self.site_name = site_name
        self.gateway_serial = gateway_serial
        self.gateway_type = gateway_type
        self.station_name = station_name
        self.station_model = station_model
        self.station_client.dashboard_client = dashboard_client
        for client in self.connector_clients.values():
            client.dashboard_client = dashboard_client
        self._power_index_maps_by_topic: dict[str, DashboardObject] | None = None
        self._station_api_available: bool | None = None
        self._connector_api_available: dict[str, bool] = {}
        self._connector_session_available: dict[str, bool] = {}
        self._last_session_api_attempt = 0.0
        self._last_session_api_update = 0.0
        self._session_refresh_unsub: CALLBACK_TYPE | None = None
        self._session_active_loop_unsub: CALLBACK_TYPE | None = None
        self._session_active_loop_interval: int | None = None
        self._session_final_refresh_unsubs: list[CALLBACK_TYPE] = []
        self._session_refresh_lock = asyncio.Lock()
        self._session_tracking_started = False
        self._last_dashboard_refresh = 0.0
        self._last_dashboard_warning = 0.0
        self._dashboard_refresh_lock = asyncio.Lock()
        self._dashboard_refresh_unsub: CALLBACK_TYPE | None = None
        self._dashboard_refresh_task: asyncio.Task | None = None
        self._shutting_down = False

    async def _async_update_data(self) -> IntegrationData:
        try:
            # ---- Station snapshot (LED brightness) ----
            prev_data = self.data
            station_state = self._merge_station_rest_state(
                prev_data.station if prev_data else None,
                await self._fetch_station_state(self.station_client),
            )

            # ---- Connectors in parallel ----
            pairs = list(self.connector_clients.items())  # [(uuid, client), ...]
            coros = [self._fetch_connector_state(client) for _, client in pairs]
            results = await asyncio.gather(*coros, return_exceptions=True)

            connectors_state: dict[str, ConnectorState] = {}
            for (uuid, client), res in zip(pairs, results, strict=True):
                if isinstance(res, ConfigEntryAuthFailed):
                    raise res
                if isinstance(res, Exception):
                    self._log_connector_api_transition(uuid, False, res)
                    # Preserve last-known values, but mark this connector unreachable
                    # for Home Assistant availability.
                    prev = (self.data.connectors or {}).get(uuid) if self.data else None
                    if prev is not None:
                        connectors_state[uuid] = replace(prev, api_available=False)
                    else:
                        connectors_state[uuid] = ConnectorState(
                            connector_number=getattr(client, "connector_number", 1),
                            api_available=False,
                        )
                elif isinstance(res, ConnectorState):
                    self._log_connector_api_transition(uuid, True)
                    prev = (prev_data.connectors or {}).get(uuid) if prev_data else None
                    connectors_state[uuid] = self._merge_connector_rest_state(prev, res)

            await self._ensure_power_index_map()

            data = IntegrationData(
                station=station_state,
                connectors=connectors_state,
                recent_sessions=prev_data.recent_sessions if prev_data else [],
            )
            await self._maybe_refresh_dashboard_data(data)
            return data

        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            raise
        except (ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error fetching Smappee data: {err}") from err

    def _log_connector_api_transition(
        self, uuid: str, available: bool, err: Exception | None = None
    ) -> None:
        """Log connector REST reachability only when it changes."""
        previous = self._connector_api_available.get(uuid)
        if previous is available:
            if not available and err is not None:
                _LOGGER.debug("Connector %s update still failing: %s", anonymize_uuid(uuid), err)
            return

        self._connector_api_available[uuid] = available
        if available:
            if previous is False:
                _LOGGER.info("Connector %s update recovered", anonymize_uuid(uuid))
            return

        if err is not None:
            _LOGGER.warning(
                "Connector %s update failed; marking unavailable: %s", anonymize_uuid(uuid), err
            )

    def _log_station_api_transition(self, available: bool, err: Exception | None = None) -> None:
        """Log station REST reachability only when it changes."""
        previous = self._station_api_available
        if previous is available:
            if not available and err is not None:
                _LOGGER.debug("Station update still failing: %s", err)
            return

        self._station_api_available = available
        if available:
            if previous is False:
                _LOGGER.info("Station update recovered")
            return

        if err is not None:
            _LOGGER.warning("Station update failed; marking unavailable: %s", err)

    @staticmethod
    def _merge_station_rest_state(prev: StationState | None, rest: StationState) -> StationState:
        """Merge REST station fields into the previous MQTT-rich station state."""
        if prev is None:
            return rest
        return replace(
            prev,
            led_brightness=rest.led_brightness
            if rest.led_brightness is not None
            else prev.led_brightness,
            dashboard_led_device_id=prev.dashboard_led_device_id,
            api_available=rest.api_available,
        )

    @staticmethod
    def _merge_connector_rest_state(
        prev: ConnectorState | None, rest: ConnectorState
    ) -> ConnectorState:
        """Merge REST connector fields into the previous MQTT-rich connector state."""
        if prev is None:
            return rest
        return replace(
            prev,
            connector_number=rest.connector_number,
            session_state=rest.session_state,
            selected_current_limit=rest.selected_current_limit
            if rest.selected_current_limit is not None
            else prev.selected_current_limit,
            selected_percentage_limit=rest.selected_percentage_limit,
            selected_mode=rest.selected_mode
            if rest.selected_mode is not None
            else prev.selected_mode,
            min_current=rest.min_current,
            max_current=rest.max_current,
            min_surpluspct=rest.min_surpluspct,
            support_grid=rest.support_grid,
            api_available=rest.api_available,
        )

    def _connector_uuid_for_highlevel_measurement(self, meas: DashboardObject) -> str | None:
        """Match a Dashboard highlevel APPLIANCE measurement to a connector UUID."""
        direct_values = [
            meas.get("uuid"),
            meas.get("smartDeviceUuid"),
            meas.get("deviceUuid"),
        ]
        appliance = meas.get("appliance")
        if isinstance(appliance, dict):
            direct_values.extend(
                [
                    appliance.get("uuid"),
                    appliance.get("smartDeviceUuid"),
                    appliance.get("deviceUuid"),
                ]
            )
        for value in direct_values:
            text = str(value).strip() if value is not None else ""
            if text and text in self.connector_clients:
                return text

        position = self._connector_position_from_measurement(meas)
        if position is not None:
            for uuid, client in self.connector_clients.items():
                if getattr(client, "connector_number", None) == position:
                    return uuid

        if len(self.connector_clients) == 1:
            return next(iter(self.connector_clients))
        return None

    @staticmethod
    def _connector_position_from_measurement(meas: DashboardObject) -> int | None:
        for key in ("position", "connectorNumber"):
            value = meas.get(key)
            if value is not None:
                with suppress(TypeError, ValueError):
                    return int(value)
        name = str(meas.get("name") or "")
        match = re.search(r"(?:^|\s-\s|\s)(\d+)\s*$", name)
        if match:
            with suppress(TypeError, ValueError):
                return int(match.group(1))
        return None

    # -----------------------------
    # Helpers (pure HTTP + parsing)
    # -----------------------------
    async def _fetch_station_state(self, client: SmappeeDeviceHandle) -> StationState:
        """Read LED brightness by scanning all smartdevices for the station."""
        led_brightness: int | None = None
        try:
            devices = await client.async_get_smartdevices()
            for dev in devices or []:
                for prop in dev.get("configurationProperties", []):
                    spec = prop.get("spec", {}) or {}
                    if (
                        spec.get("name")
                        == "etc.smart.device.type.car.charger.led.config.brightness"
                    ):
                        raw = prop.get("value")
                        val = raw.get("value") if isinstance(raw, dict) else raw
                        if val is not None:
                            with suppress(TypeError, ValueError):
                                led_brightness = int(val)
                        break
            self._log_station_api_transition(True)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, ClientError, RuntimeError) as err:
            self._log_station_api_transition(False, err)
            return StationState(led_brightness=led_brightness, available=True, api_available=False)

        return StationState(led_brightness=led_brightness, available=True, api_available=True)

    async def _fetch_connector_state(self, client: SmappeeDeviceHandle) -> ConnectorState:
        """Read one connector's properties/config from its smartdevice."""
        # Defaults, will be overwritten by API values when present
        session_state = "Initialize"
        selected_percentage: int | None = None
        selected_current: int | None = None
        selected_mode: str | None = None
        min_current = DEFAULT_MIN_CURRENT
        max_current = DEFAULT_MAX_CURRENT
        min_surpluspct: int | None = None
        support_grid: int | None = None

        data = await client.async_get_smartdevice(client.smart_device_id)
        if data is None:
            raise RuntimeError(f"smartdevice fetch {client.smart_device_id} returned no data")

        # properties: chargingState, percentageLimit
        for prop in data.get("properties", []):
            spec = prop.get("spec", {}) or {}
            name = spec.get("name")
            val = prop.get("value")
            if name == "chargingState":
                session_state = val or session_state
            elif name == "percentageLimit":
                with suppress(TypeError, ValueError):
                    selected_percentage = int(val)

        # configurationProperties: max/min current, min.excesspct, grid support
        for prop in data.get("configurationProperties", []):
            spec = prop.get("spec", {}) or {}
            name = spec.get("name")
            raw = prop.get("value")
            val = raw.get("value") if isinstance(raw, dict) else raw
            if name == "etc.smart.device.type.car.charger.config.max.current":
                with suppress(TypeError, ValueError):
                    max_current = _to_int(val, default=max_current)
            elif name == "etc.smart.device.type.car.charger.config.min.current":
                with suppress(TypeError, ValueError):
                    min_current = _to_int(val, default=min_current)
            elif name == "etc.smart.device.type.car.charger.config.min.excesspct":
                if val is not None:
                    with suppress(TypeError, ValueError):
                        min_surpluspct = int(val)
            elif name == "etc.smart.device.type.car.charger.config.max.gridassistanceamps":
                with suppress(TypeError, ValueError):
                    support_grid = _to_int(val)

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
            support_grid=support_grid,
            api_available=True,
        )

    def async_start_session_tracking(self) -> None:
        """Start the state-driven recent-session refresh manager."""
        if self._session_tracking_started:
            return
        self._session_tracking_started = True
        self._schedule_session_refresh("startup", delay=0, force=True)
        self._sync_session_tracking_from_current_state()

    async def async_shutdown(self) -> None:
        """Cancel session refresh callbacks and background tasks."""
        self.cancel_delayed_refreshes()
        task = self._dashboard_refresh_task

        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def cancel_delayed_refreshes(self) -> None:
        """Synchronously cancel delayed refresh callbacks/tasks during shutdown."""
        self._shutting_down = True

        self._cancel_session_refresh()
        self._cancel_active_session_loop()
        self._cancel_final_session_refreshes()
        self._cancel_dashboard_refresh_timer()

        task = self._dashboard_refresh_task
        if task is not None and not task.done():
            task.cancel()

    @property
    def _is_stopping(self) -> bool:
        """Return True when the coordinator should avoid new background I/O."""
        return self._shutting_down or getattr(self.hass, "is_stopping", False) is True

    def _start_background_reauth(self) -> None:
        """Start reauth for auth failures raised outside coordinator polling."""
        entry = self.config_entry
        if entry is None:
            _LOGGER.warning("Smappee background task failed authentication")
            return
        start_reauth = getattr(entry, "async_start_reauth", None)
        if callable(start_reauth):
            start_reauth(self.hass)
            return
        _LOGGER.warning("Smappee background task failed authentication")

    def _log_background_task_exception(self, task: asyncio.Task) -> None:
        """Consume and log unexpected background task exceptions."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None or isinstance(exc, ConfigEntryAuthFailed):
            return
        _LOGGER.warning("Smappee background task failed", exc_info=exc)

    # ---------- split sub-helpers ----------
    def _set_if_changed(self, obj: object, attr: str, value) -> bool:
        """Set attr if value is not None and different; return True if changed."""
        if value is None:
            return False
        cur = getattr(obj, attr, None)
        if value != cur:
            setattr(obj, attr, value)
            return True
        return False

    def _apply_station_group(
        self,
        st,  # StationState
        payload: dict,
        power_idxs: list[int],
        current_idxs: list[int],
        energy_idxs: list[int] | str,
        power_key_prefix: str | None = None,  # "grid" or "pv"
        power_field: str | None = None,
    ) -> bool:
        if power_key_prefix is None:
            power_key_prefix = str(energy_idxs)
            energy_idxs = current_idxs
            current_idxs = []
        changed = False
        active = _active_power_values(payload, power_field)
        currents_ma = payload.get("currentData") or []
        voltage_dv = payload.get("phaseVoltageData") or []
        imp_wh = payload.get("importActiveEnergyData") or []
        exp_wh = payload.get("exportActiveEnergyData") or []

        p_ph = _pick(active, power_idxs)
        if p_ph:
            changed |= self._set_if_changed(st, f"{power_key_prefix}_power_phases", p_ph)
            changed |= self._set_if_changed(st, f"{power_key_prefix}_power_total", sum(p_ph))
            i_ph = _amps_from_ma(_pick(currents_ma, current_idxs or power_idxs))
            if i_ph:
                changed |= self._set_if_changed(st, f"{power_key_prefix}_current_phases", i_ph)

        if power_key_prefix == "grid":
            # Voltage is always in the first 3 entries of phaseVoltageData
            # (L1, L2, L3 of the grid connection), independent of powerTopicIndex.
            v_ph = _volts_from_dv(_pick(voltage_dv, [0, 1, 2]))
            if v_ph:
                changed |= self._set_if_changed(st, "grid_voltage_phases", v_ph)

        energy_idx_list = energy_idxs if isinstance(energy_idxs, list) else []
        if energy_idx_list:
            if power_key_prefix == "grid":
                changed |= self._set_if_changed(
                    st,
                    "grid_energy_import_kwh",
                    round(sum(_pick(imp_wh, energy_idx_list)) / 1000.0, 3),
                )
                changed |= self._set_if_changed(
                    st,
                    "grid_energy_export_kwh",
                    round(sum(_pick(exp_wh, energy_idx_list)) / 1000.0, 3),
                )
            else:  # pv
                changed |= self._set_if_changed(
                    st,
                    "pv_energy_import_kwh",
                    round(sum(_pick(imp_wh, energy_idx_list)) / 1000.0, 3),
                )
        return changed

    def _apply_connector_values(
        self,
        conn,  # ConnectorState
        payload: dict,
        power_idxs: list[int],
        current_idxs: list[int],
        energy_idxs: list[int] | None = None,
        power_field: str | None = None,
    ) -> bool:
        if energy_idxs is None:
            energy_idxs = current_idxs
            current_idxs = []
        changed = False
        active = _active_power_values(payload, power_field)
        currents_ma = payload.get("currentData") or []
        imp_wh = payload.get("importActiveEnergyData") or []

        p_ph = _pick(active, power_idxs)
        i_ma = _pick(currents_ma, current_idxs or power_idxs)
        if energy_idxs:
            energy_values = _pick(imp_wh, energy_idxs)
            if energy_values and len(set(energy_values)) == 1:
                # All values are identical -> total energy replicated across indices
                val = energy_values[0]
            else:
                # Different values -> per-phase energy, sum them
                val = sum(energy_values)
            imp_kwh = round(val / 1000.0, 3)
        else:
            imp_kwh = None

        changed |= self._set_if_changed(conn, "power_phases", p_ph)
        changed |= self._set_if_changed(conn, "power_total", sum(p_ph) if p_ph else None)
        if i_ma:
            changed |= self._set_if_changed(conn, "current_phases", _amps_from_ma(i_ma))
        changed |= self._set_if_changed(conn, "energy_import_kwh", imp_kwh)
        return changed

# Backwards-compatible public name used by older tests and platform code.
SmappeeCoordinator = SmappeeStationCoordinator
