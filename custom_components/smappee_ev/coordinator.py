"""Coordinator logic for Smappee EV service locations and charging stations."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging
from time import monotonic as _monotonic, time as _now
from typing import Any, override

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.dashboard_client import SmappeeDashboardClient
from .api.device_handle import SmappeeDeviceHandle, SmartDeviceRequests
from .api.errors import SmappeeError
from .const import (
    MQTT_REAL_POWER_FRESHNESS_TIMEOUT,
    SMARTDEVICE_FALLBACK_INTERVAL,
    SMARTDEVICE_REFRESH_INTERVAL,
)
from .coordinators.api_state import ConnectorRestSnapshot, StationApiMixin
from .coordinators.dashboard_merge import DashboardMixin
from .coordinators.freshness import MeasurementFreshness, MqttApplyResult
from .coordinators.mqtt_apply import MqttMixin
from .coordinators.power import (
    PowerMixin,
    _active_power_values,
    _amps_from_ma,
    _empty_power_topic_map,
    _indexes_and_field_from_aspect_paths,
    _indexes_from_aspect_paths,
    _mqtt_channel_topic,
    _pick,
    _sum_kwh,
    _to_int as _power_to_int,
    _volts_from_dv,
)
from .coordinators.session_tracking import SessionTrackingMixin
from .coordinators.storage import StorageMeasurements
from .helpers import anonymize_uuid
from .models.state import (
    ConnectorState,
    DashboardObject,
    HighLevelConfigMap,
    IntegrationData,
    SiteData,
    SiteState,
)
from .state_updates import StateChanges, state_staging_copy

_LOGGER = logging.getLogger(__name__)


def _to_int(value: object, default: int = 0) -> int:
    """Compatibility wrapper for tests and older internal imports."""
    return _power_to_int(value, default)


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
        self.storage_measurements = StorageMeasurements(self._highlevel_configs)
        self.monitoring_only = False
        self.measurement_freshness = MeasurementFreshness()
        self._power_index_maps_by_topic: dict[str, DashboardObject] | None = None
        self._power_map_retry_after = 0.0
        self.mqtt_transport_connected = False
        self.last_real_charger_rx: datetime | None = None
        self.last_real_power_rx: datetime | None = None
        self.last_heartbeat_rx: datetime | None = None

    @override
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

    def _set_if_changed(self, obj: object, attr: str, value: object) -> bool:
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
        accepted: set[str] | None = None,
    ) -> bool:
        accepted = accepted if accepted is not None else set()
        changed = False
        active = _active_power_values(payload, power_field)
        currents_ma = payload.get("currentData") or []
        voltage_dv = payload.get("phaseVoltageData") or []
        imp_wh = payload.get("importActiveEnergyData") or []
        exp_wh = payload.get("exportActiveEnergyData") or []
        p_ph = _pick(active, power_idxs)
        if p_ph:
            accepted.add(f"{power_key_prefix}_power_total")
            changed |= self._set_if_changed(site, f"{power_key_prefix}_power_phases", p_ph)
            changed |= self._set_if_changed(site, f"{power_key_prefix}_power_total", sum(p_ph))
        i_ph = _amps_from_ma(_pick(currents_ma, current_idxs or power_idxs))
        if i_ph:
            accepted.add(f"{power_key_prefix}_current_phases")
            changed |= self._set_if_changed(site, f"{power_key_prefix}_current_phases", i_ph)
        if power_key_prefix == "grid" and voltage_dv:
            v_ph = _volts_from_dv(
                _pick(voltage_dv, range(min(3, len(voltage_dv))))
                if isinstance(voltage_dv, list)
                else []
            )
            if v_ph:
                accepted.add("grid_voltage_phases")
                changed |= self._set_if_changed(site, "grid_voltage_phases", v_ph)
        if energy_idxs:
            if _pick(imp_wh, energy_idxs):
                accepted.add(f"{power_key_prefix}_energy_import_kwh")
            if power_key_prefix == "grid" and _pick(exp_wh, energy_idxs):
                accepted.add("grid_energy_export_kwh")
            if power_key_prefix == "grid":
                changed |= self._set_if_changed(
                    site,
                    "grid_energy_import_kwh",
                    _sum_kwh(imp_wh, energy_idxs),
                )
                changed |= self._set_if_changed(
                    site,
                    "grid_energy_export_kwh",
                    _sum_kwh(exp_wh, energy_idxs),
                )
            else:
                changed |= self._set_if_changed(
                    site,
                    "pv_energy_import_kwh",
                    _sum_kwh(imp_wh, energy_idxs),
                )
        return changed

    def _handle_power(
        self, topic: str, payload: dict, result: MqttApplyResult | None = None
    ) -> bool:
        data = self.data
        if not data:
            return False
        idx_map = (self._power_index_maps_by_topic or {}).get(topic)
        site = data.site
        accepted = result.site_fields if result is not None else set()
        changed = self.storage_measurements.apply(site, topic, payload, accepted)
        grid = idx_map.get("grid", {}) if idx_map else {}
        pv = idx_map.get("pv", {}) if idx_map else {}
        changed |= self._apply_site_group(
            site,
            payload,
            grid.get("power", []),
            grid.get("current", []),
            grid.get("energy", []),
            "grid",
            grid.get("power_field"),
            accepted,
        )
        changed |= self._apply_site_group(
            site,
            payload,
            pv.get("power", []),
            pv.get("current", []),
            pv.get("energy", []),
            "pv",
            pv.get("power_field"),
            accepted,
        )

        payload_location_id = payload.get("serviceLocationId")
        if payload_location_id is not None:
            # _to_int returns 0 (or a default) if parsing fails safely
            if _to_int(payload_location_id, -1) != self.site_location_id:
                return changed

        cp = payload.get("consumptionPower")
        if isinstance(cp, int | float):
            accepted.add("house_consumption_power")
            changed |= self._set_if_changed(site, "house_consumption_power", int(cp))
        sp = payload.get("solarPower")
        if isinstance(sp, int | float):
            accepted.add("pv_power_total")
            changed |= self._set_if_changed(site, "pv_power_total", int(sp))
        always_on = payload.get("alwaysOn")
        if isinstance(always_on, int | float):
            accepted.add("always_on_power")
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

    def apply_mqtt_properties(self, topic: str, payload: dict) -> MqttApplyResult:
        result = MqttApplyResult()
        data = self.data
        if not data:
            return result
        data.site.last_mqtt_rx = _now()
        changed = False
        if not getattr(data.site, "mqtt_connected", False):
            data.site.mqtt_connected = True
            changed = True
        changed |= self._handle_power(topic, payload, result)
        changed |= self.measurement_freshness.record(result)
        if changed:
            self.async_set_updated_data(data)
        return result


class SmappeeStationCoordinator(
    SessionTrackingMixin,
    StationApiMixin,
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
        self.monitoring_only = False
        self._highlevel_configs = highlevel_configs or {}
        self.site_name = site_name
        self.gateway_serial = gateway_serial
        self.gateway_type = gateway_type
        self.station_name = station_name
        self.station_model = station_model
        self.last_connector_rx: dict[str, datetime] = {}
        self.measurement_freshness = MeasurementFreshness()
        self.station_client.dashboard_client = dashboard_client
        for client in self.connector_clients.values():
            client.dashboard_client = dashboard_client
        self._power_index_maps_by_topic: dict[str, DashboardObject] | None = None
        self._power_map_retry_after = 0.0
        self.mqtt_transport_connected = False
        self.last_real_charger_rx: datetime | None = None
        self.last_real_power_rx: datetime | None = None
        self.last_heartbeat_rx: datetime | None = None
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
        self._last_smartdevice_refresh: float | None = None
        self._smartdevice_refresh_error: Exception | None = None
        self._force_smartdevice_refresh = False
        self._last_dashboard_warning = 0.0
        self._dashboard_refresh_lock = asyncio.Lock()
        self._dashboard_refresh_unsub: CALLBACK_TYPE | None = None
        self._dashboard_refresh_task: asyncio.Task | None = None
        self._shutting_down = False

    @override
    async def _async_update_data(self) -> IntegrationData:
        """Check freshness locally; only poll REST when its deadline is reached."""
        if self._is_stopping:
            return self.data
        now = _monotonic()
        if (
            not self._force_smartdevice_refresh
            and self._last_smartdevice_refresh is not None
            and now - self._last_smartdevice_refresh
            < self._smartdevice_poll_interval().total_seconds()
        ):
            # A local timer tick must not turn a failed REST update into a recovery.
            if self._smartdevice_refresh_error is not None:
                raise self._smartdevice_refresh_error
            return self.data

        self._force_smartdevice_refresh = False
        self._last_smartdevice_refresh = now
        self._smartdevice_refresh_error = None
        try:
            return await self._async_fetch_data()
        except asyncio.CancelledError:
            # An interrupted attempt supplied no snapshot to reuse on later ticks.
            self._last_smartdevice_refresh = None
            raise
        except Exception as err:
            self._smartdevice_refresh_error = err
            raise

    def _smartdevice_poll_interval(self) -> timedelta:
        """Use slow REST checks only with fresh charger traffic and reachable REST."""
        data = self.data
        if self._smartdevice_refresh_error is not None or (
            data is not None
            and (
                data.station.api_available is False
                or any(conn.api_available is False for conn in data.connectors.values())
            )
        ):
            return SMARTDEVICE_FALLBACK_INTERVAL
        last_rx = self.last_real_charger_rx
        if (
            self.mqtt_transport_connected
            and last_rx is not None
            and timedelta(0) <= datetime.now(UTC) - last_rx < MQTT_REAL_POWER_FRESHNESS_TIMEOUT
        ):
            return SMARTDEVICE_REFRESH_INTERVAL
        return SMARTDEVICE_FALLBACK_INTERVAL

    async def _async_fetch_data(self) -> IntegrationData:
        """Fetch and merge one fresh station/connector REST snapshot."""
        # Share successes and failures for this refresh only. The next refresh
        # must fetch a new list rather than replaying an older REST snapshot.
        requests: SmartDeviceRequests = {}
        refresh_start = state_staging_copy(self.data) if self.data is not None else None
        try:
            # Fetch remote values into isolated objects. Do not preserve live
            # telemetry from a snapshot retained while network I/O is pending.
            rest_station = await self._fetch_station_state(self.station_client, requests=requests)

            # ---- Connectors in parallel ----
            pairs = list(self.connector_clients.items())  # [(uuid, client), ...]
            coros = [self._fetch_connector_state(client, requests=requests) for _, client in pairs]
            results = await asyncio.gather(*coros, return_exceptions=True)

            await self._ensure_power_index_map()
            baseline = state_staging_copy(
                self.data
                or IntegrationData(
                    station=rest_station,
                    connectors={
                        uuid: ConnectorState(connector_number=client.connector_number or 1)
                        for uuid, client in pairs
                    },
                )
            )
            staged = state_staging_copy(baseline)
            await self._maybe_refresh_dashboard_data(staged)
            dashboard_changes = StateChanges.between(baseline, staged)

            # All awaits are finished. Merge remote fields into the CURRENT
            # snapshot and return without yielding, preserving MQTT and sessions.
            current = self.data
            station_state = self._merge_station_rest_state(
                current.station if current else None, rest_station
            )

            connectors_state: dict[str, ConnectorState] = {}
            for (uuid, client), res in zip(pairs, results, strict=True):
                if isinstance(res, asyncio.CancelledError):
                    raise res
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
                elif isinstance(res, ConnectorState | ConnectorRestSnapshot):
                    self._log_connector_api_transition(uuid, True)
                    prev = (current.connectors or {}).get(uuid) if current else None
                    connectors_state[uuid] = self._merge_connector_rest_state(prev, res)

            data = IntegrationData(
                station=station_state,
                connectors=connectors_state,
                recent_sessions=current.recent_sessions if current else [],
            )
            dashboard_changes.apply(data)
            if refresh_start is not None and current is not None:
                # New commands and MQTT state take precedence over responses
                # that were already in flight when those updates arrived.
                StateChanges.between(refresh_start, current).apply(data)
            self._sync_dashboard_client_metadata(data)
            return data

        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            raise
        except (SmappeeError, ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error fetching Smappee data: {err}") from err

    def async_start_session_tracking(self) -> None:
        """Start the state-driven recent-session refresh manager."""
        if self._session_tracking_started:
            return
        self._session_tracking_started = True
        self._schedule_session_refresh("startup", delay=0, force=True)
        self._sync_session_tracking_from_current_state()

    @override
    async def async_shutdown(self) -> None:
        """Cancel session refresh callbacks and background tasks."""
        self.cancel_delayed_refreshes()
        await super().async_shutdown()
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
        return (
            self.monitoring_only
            or self._shutting_down
            or getattr(self.hass, "is_stopping", False) is True
        )

    def _start_background_reauth(self) -> None:
        """Start reauth for auth failures raised outside coordinator polling."""
        entry = self.config_entry
        if entry is None:
            _LOGGER.warning("Smappee background task failed authentication")
            return
        entry.async_start_reauth_if_available(self.hass)

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
    def _set_if_changed(self, obj: object, attr: str, value: object) -> bool:
        """Set attr if value is not None and different; return True if changed."""
        if value is None:
            return False
        cur = getattr(obj, attr, None)
        if value != cur:
            setattr(obj, attr, value)
            return True
        return False


# Backwards-compatible public name used by older tests and platform code.
SmappeeCoordinator = SmappeeStationCoordinator
