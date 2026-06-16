# custom_components/smappee_ev/coordinator.py
from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import timedelta
from inspect import iscoroutinefunction
import logging
import re
from time import time as _now
from typing import Any, cast

from aiohttp import ClientError
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DASHBOARD_REFRESH_AFTER_WRITE_DELAY,
    DASHBOARD_REFRESH_INTERVAL,
    DEFAULT_MAX_CURRENT,
    DEFAULT_MIN_CURRENT,
)
from .dashboard_client import SmappeeDashboardClient
from .data import (
    ConnectorState,
    IntegrationData,
    SiteData,
    SiteState,
    SmappeeEvConfigEntry,
    StationState,
)
from .device_handle import SmappeeDeviceHandle
from .helpers import anonymize_uuid
from .mqtt_gateway import redact_mqtt_topic

_LOGGER = logging.getLogger(__name__)

SESSION_START_REFRESH_DELAY = 20
SESSION_ACTIVE_REFRESH_INTERVAL = 5 * 60
SESSION_PAUSED_REFRESH_INTERVAL = 15 * 60
SESSION_MIN_REFRESH_INTERVAL = 2 * 60
SESSION_FINAL_REFRESH_DELAYS = (30, 2 * 60, 5 * 60)

_SESSION_ACTIVE_STATES = {"STARTED", "CHARGING", "CHARGING_STARTED", "RUNNING"}
_SESSION_PAUSED_STATES = {"PAUSED", "SUSPENDED"}
_SESSION_STOPPED_STATES = {"STOPPED", "CHARGING_FINISHED", "FINISHED", "COMPLETED", "IDLE"}
_MQTT_PATH_RE = re.compile(r"\$\.([A-Za-z0-9_]+)\[(\d+)\]")


def _to_int(value: Any, default: int = 0) -> int:
    """Convert a value to int safely, fallback to default on error."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _pick(seq: Sequence[int] | list, idxs: Iterable[int]) -> list[int]:
    """Safe index selection with zero-fill. Returns [] if seq or idxs are empty."""
    idxs = list(idxs)
    if not isinstance(seq, list) or not idxs:
        return []
    n = len(seq)
    return [int(seq[i]) if 0 <= i < n else 0 for i in idxs]


def _amps_from_ma(ma: list[int]) -> list[float]:
    """Convert mA list to A with 3 decimals."""
    return [round(x / 1000.0, 3) for x in ma] if ma else []


def _volts_from_dv(dv: list[int]) -> list[int]:
    """Convert deci-volt list to V as integers."""
    return [round(x / 10) for x in dv] if dv else []


def _indexes_from_aspect_paths(channel: dict[str, Any] | None, field: str) -> list[int]:
    """Extract MQTT array indexes from Dashboard aspect paths."""
    if not isinstance(channel, dict):
        return []
    indexes: list[int] = []
    for aspect in channel.get("aspectPaths") or []:
        if not isinstance(aspect, dict):
            continue
        match = _MQTT_PATH_RE.fullmatch(str(aspect.get("path") or ""))
        if match and match.group(1) == field:
            indexes.append(int(match.group(2)))
    return indexes


def _indexes_and_field_from_aspect_paths(
    channel: dict[str, Any] | None, *fields: str
) -> tuple[list[int], str | None]:
    """Extract MQTT array indexes and remember which array field they belong to."""
    if not isinstance(channel, dict):
        return [], None

    selected_field: str | None = None
    indexes: list[int] = []
    allowed = set(fields)
    for aspect in channel.get("aspectPaths") or []:
        if not isinstance(aspect, dict):
            continue
        match = _MQTT_PATH_RE.fullmatch(str(aspect.get("path") or ""))
        if not match:
            continue
        field = match.group(1)
        if field not in allowed:
            continue
        if selected_field is None:
            selected_field = field
        if field == selected_field:
            indexes.append(int(match.group(2)))

    return indexes, selected_field


def _active_power_values(payload: dict, field: str | None = None) -> list:
    """Return active power values from the Dashboard-indicated MQTT array field."""
    if field:
        values = payload.get(field)
        return values if isinstance(values, list) else []
    for fallback in ("activePowerData", "channelData"):
        values = payload.get(fallback)
        if isinstance(values, list):
            return values
    return []


def _empty_power_topic_map() -> dict[str, Any]:
    """Return an empty MQTT power index map for one topic."""
    return {
        "grid": {"power": [], "current": [], "cons": [], "energy": []},
        "pv": {"power": [], "current": [], "cons": [], "energy": []},
        "cars": {},
    }


def _mqtt_channel_topic(channel: dict[str, Any] | None) -> str | None:
    """Return the MQTT topic advertised by a highlevel channel."""
    if not isinstance(channel, dict):
        return None
    if str(channel.get("protocol") or "").upper() != "MQTT":
        return None
    topic = str(channel.get("name") or "").strip()
    return topic or None


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
        config_entry: SmappeeEvConfigEntry | None = None,
        highlevel_configs: dict[int, dict[str, Any]] | None = None,
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
        self._power_index_maps_by_topic: dict[str, dict[str, Any]] | None = None

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
        self, configs: dict[int, dict[str, Any]]
    ) -> dict[str, dict[str, Any]] | None:
        maps_by_topic: dict[str, dict[str, Any]] = {}
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
        self, cfg: dict[str, Any]
    ) -> dict[str, dict[str, Any]] | None:
        maps_by_topic: dict[str, dict[str, Any]] = {}
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
        elif not up and getattr(site, "mqtt_connected", None) is not False:
            site.mqtt_connected = False
            changed = True
        if changed:
            self.async_set_updated_data(data)

    def apply_mqtt_properties(self, topic: str, payload: dict) -> None:
        data = self.data
        if not data:
            return
        data.site.last_mqtt_rx = _now()
        changed = True
        if not getattr(data.site, "mqtt_connected", False):
            data.site.mqtt_connected = True
        if topic.endswith("/power"):
            changed |= self._handle_power(topic, payload)
        if changed:
            self.async_set_updated_data(data)


class SmappeeStationCoordinator(DataUpdateCoordinator[IntegrationData]):
    """Single source of truth: fetch station + all connector state here."""

    @staticmethod
    def _get_any(d: dict, *names: str):
        for k in names:
            if k in d:
                return d[k]
        low = {kk.lower(): vv for kk, vv in d.items()}
        for k in names:
            v = low.get(k.lower())
            if v is not None:
                return v
        return None

    def __init__(
        self,
        hass: HomeAssistant,
        station_client: SmappeeDeviceHandle,
        connector_clients: dict[str, SmappeeDeviceHandle],  # keyed by UUID
        update_interval: int,
        config_entry: SmappeeEvConfigEntry | None = None,
        dashboard_client: SmappeeDashboardClient | None = None,
        highlevel_configs: dict[int, dict[str, Any]] | None = None,
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
        self._power_index_maps_by_topic: dict[str, dict[str, Any]] | None = None
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
                else:
                    self._log_connector_api_transition(uuid, True)
                    rest_state = cast(ConnectorState, res)
                    prev = (prev_data.connectors or {}).get(uuid) if prev_data else None
                    connectors_state[uuid] = self._merge_connector_rest_state(prev, rest_state)

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
            _LOGGER.warning("Connector %s update failed; marking unavailable: %s", anonymize_uuid(uuid), err)

    def _log_connector_session_transition(
        self, uuid: str, available: bool, err: Exception | None = None
    ) -> None:
        """Log connector session endpoint reachability only when it changes."""
        previous = self._connector_session_available.get(uuid)
        if previous is available:
            if not available and err is not None:
                _LOGGER.debug("Connector %s session fetch still failing: %s", anonymize_uuid(uuid), err)
            return

        self._connector_session_available[uuid] = available
        if available:
            if previous is False:
                _LOGGER.info("Connector %s session fetch recovered", anonymize_uuid(uuid))
            return

        if err is not None:
            _LOGGER.warning("Connector %s session fetch failed: %s", anonymize_uuid(uuid), err)

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

    async def _ensure_power_index_map(self) -> None:
        """Load and cache MQTT power index mapping from Dashboard v10."""
        if self._power_index_maps_by_topic is not None:
            return
        if self.dashboard_client is None:
            return

        configs = dict(self._highlevel_configs)
        if not configs:
            cfg = await self.dashboard_client.async_get_highlevel_configuration(
                self.station_client.service_location_id
            )
            if cfg is None:
                return
            configs[int(self.station_client.service_location_id)] = cfg

        mapping = self._build_measurement_index_maps_by_topic_from_highlevel_configs(configs)
        if mapping:
            self._power_index_maps_by_topic = mapping

    def _build_measurement_index_maps_by_topic_from_highlevel_configs(
        self, configs: dict[int, dict[str, Any]]
    ) -> dict[str, dict[str, Any]] | None:
        """Build MQTT index mappings grouped by exact power topic."""
        maps_by_topic: dict[str, dict[str, Any]] = {}
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
                merged["cars"].update(topic_map["cars"])

        return maps_by_topic or None

    def _build_measurement_index_maps_by_topic_from_highlevel(
        self, cfg: dict[str, Any]
    ) -> dict[str, dict[str, Any]] | None:
        """Build MQTT index mappings from Dashboard v10 highlevelconfiguration."""
        maps_by_topic: dict[str, dict[str, Any]] = {}

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
            appliance_raw = meas.get("appliance")
            appliance = appliance_raw if isinstance(appliance_raw, dict) else {}
            category = str(meas.get("category") or appliance.get("type") or "").upper()

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
                continue

            if mtype == "APPLIANCE" and category == "CAR_CHARGER":
                uuid = self._connector_uuid_for_highlevel_measurement(meas)
                if not uuid:
                    continue
                position = self._connector_position_from_measurement(meas)
                if power_topic:
                    topic_map = maps_by_topic.setdefault(power_topic, _empty_power_topic_map())
                    car = topic_map["cars"].setdefault(
                        uuid,
                        {"position": position, "serial": None},
                    )
                    car["power"] = power_idx
                    car["power_field"] = power_field
                if current_topic:
                    topic_map = maps_by_topic.setdefault(current_topic, _empty_power_topic_map())
                    car = topic_map["cars"].setdefault(
                        uuid,
                        {"position": position, "serial": None},
                    )
                    car["current"] = current_idx
                if energy_topic:
                    topic_map = maps_by_topic.setdefault(energy_topic, _empty_power_topic_map())
                    car = topic_map["cars"].setdefault(
                        uuid,
                        {"position": position, "serial": None},
                    )
                    car["energy"] = energy_idx

        return maps_by_topic or None

    def _connector_uuid_for_highlevel_measurement(self, meas: dict[str, Any]) -> str | None:
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
    def _connector_position_from_measurement(meas: dict[str, Any]) -> int | None:
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

    async def _maybe_refresh_dashboard_data(
        self, data: IntegrationData, force: bool = False
    ) -> bool:
        """Refresh slow Dashboard REST config/cache data when due."""
        if self.dashboard_client is None:
            return False
        if self._dashboard_refresh_lock.locked():
            return False

        now = _now()
        if (
            not force
            and self._last_dashboard_refresh
            and now - self._last_dashboard_refresh < DASHBOARD_REFRESH_INTERVAL.total_seconds()
        ):
            return False

        async with self._dashboard_refresh_lock:
            now = _now()
            if (
                not force
                and self._last_dashboard_refresh
                and now - self._last_dashboard_refresh < DASHBOARD_REFRESH_INTERVAL.total_seconds()
            ):
                return False

            service_location_id = self.station_client.service_location_id
            site_service_location_id = (
                getattr(self.station_client, "site_location_id", None) or service_location_id
            )
            station_serial = (
                self.station_client.charging_station_serial or self.station_client.serial
            )
            calls = {
                "charging station details": self.dashboard_client.async_get_charging_station_details(
                    station_serial
                ),
                "capacity protection": self.dashboard_client.async_get_capacity_protection(
                    site_service_location_id
                ),
                "overload protection": self.dashboard_client.async_get_overload_protection(
                    site_service_location_id
                ),
                "high-level configuration": self.dashboard_client.async_get_highlevel_configuration(
                    service_location_id
                ),
                "appliances": self.dashboard_client.async_get_appliances(service_location_id),
            }
            results = await asyncio.gather(*calls.values(), return_exceptions=True)

            changed = False
            errors: list[str] = []
            responses = dict(zip(calls.keys(), results, strict=True))
            if any(not isinstance(result, BaseException) for result in responses.values()):
                self._last_dashboard_refresh = now
            for label, result in responses.items():
                if isinstance(result, BaseException):
                    if isinstance(result, ConfigEntryAuthFailed):
                        raise result
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    errors.append(f"{label}: {result}")

            details = responses.get("charging station details")
            if isinstance(details, dict):
                changed |= self._merge_dashboard_station_details(data, details)

            capacity = responses.get("capacity protection")
            if isinstance(capacity, dict):
                changed |= self._merge_dashboard_capacity(data.station, capacity)

            overload = responses.get("overload protection")
            if isinstance(overload, dict):
                changed |= self._merge_dashboard_overload(data.station, overload)

            highlevel = responses.get("high-level configuration")
            if isinstance(highlevel, dict):
                self._highlevel_configs[int(service_location_id)] = highlevel
                mapping = self._build_measurement_index_maps_by_topic_from_highlevel_configs(
                    self._highlevel_configs
                )
                if mapping is not None:
                    self._power_index_maps_by_topic = mapping

            if errors:
                self._log_dashboard_refresh_errors(errors)

            self._sync_dashboard_client_metadata(data)
            changed |= await self._refresh_dashboard_load_management(data)
            return changed

    async def _refresh_dashboard_load_management(self, data: IntegrationData) -> bool:
        """Refresh per-connector Dashboard load-management state."""
        if self.dashboard_client is None:
            return False
        method = getattr(self.dashboard_client, "async_get_load_management", None)
        if method is None or not iscoroutinefunction(method):
            return False
        calls = {
            uuid: method(self.station_client.service_location_id, conn.dashboard_device_id)
            for uuid, conn in data.connectors.items()
            if conn.dashboard_device_id
        }
        if not calls:
            return False

        results = await asyncio.gather(*calls.values(), return_exceptions=True)
        changed = False
        errors: list[str] = []
        for uuid, result in zip(calls.keys(), results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, ConfigEntryAuthFailed):
                    raise result
                if isinstance(result, asyncio.CancelledError):
                    raise result
                errors.append(f"load management {uuid}: {result}")
                continue
            if isinstance(result, dict):
                changed |= self._merge_dashboard_load_management(data.connectors[uuid], result)

        if errors:
            self._log_dashboard_refresh_errors(errors)
        return changed

    def _merge_dashboard_load_management(
        self, conn: ConnectorState, payload: dict[str, Any]
    ) -> bool:
        changed = False
        strategy = self._as_str(payload.get("optimizationStrategy"))
        if strategy:
            changed |= self._set_if_changed(conn, "optimization_strategy", strategy)
            base = self._derive_base_mode(conn.raw_charging_mode, conn.optimization_strategy)
            changed |= self._set_if_changed(conn, "ui_mode_base", base)
            changed |= self._set_if_changed(conn, "selected_mode", base)
        return changed

    def _sync_dashboard_client_metadata(self, data: IntegrationData) -> None:
        """Copy Dashboard identifiers from state onto command clients."""
        self.station_client.dashboard_client = self.dashboard_client
        self.station_client.dashboard_device_id = data.station.dashboard_led_device_id
        for connector_uuid, conn in data.connectors.items():
            client = self.connector_clients.get(connector_uuid)
            if client is None:
                continue
            client.dashboard_client = self.dashboard_client
            client.dashboard_device_id = conn.dashboard_device_id

    def _log_dashboard_refresh_errors(self, errors: list[str]) -> None:
        now = _now()
        if now - self._last_dashboard_warning < DASHBOARD_REFRESH_INTERVAL.total_seconds():
            _LOGGER.debug("Dashboard refresh still failing: %s", "; ".join(errors))
            return
        self._last_dashboard_warning = now
        _LOGGER.warning(
            "Dashboard refresh failed; keeping existing MQTT/API data: %s", "; ".join(errors)
        )

    def async_schedule_dashboard_refresh(
        self, delay: float = DASHBOARD_REFRESH_AFTER_WRITE_DELAY
    ) -> None:
        """Schedule a forced dashboard refresh after a write."""
        if self._shutting_down:
            return
        task = self._dashboard_refresh_task
        if task is not None and not task.done():
            task.cancel()
        task = self.hass.async_create_task(self._async_delayed_dashboard_refresh(delay))
        self._dashboard_refresh_task = task
        task.add_done_callback(self._log_background_task_exception)

    async def _async_delayed_dashboard_refresh(self, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            data = self.data
            if data and await self._maybe_refresh_dashboard_data(data, force=True):
                self.async_set_updated_data(data)
        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            self._start_background_reauth()
        finally:
            if self._dashboard_refresh_task is asyncio.current_task():
                self._dashboard_refresh_task = None

    def _merge_dashboard_station_details(
        self, data: IntegrationData, details: dict[str, Any]
    ) -> bool:
        station = data.station
        changed = False

        changed |= self._set_if_changed(
            station, "dashboard_available", self._as_bool(details.get("available"))
        )
        changed |= self._set_if_changed(
            station, "station_features", [str(item) for item in details.get("features") or []]
        )
        changed |= self._set_if_changed(
            station, "maximum_capacity_a", self._as_int(details.get("maximumCapacity"))
        )
        changed |= self._set_if_changed(station, "dashboard_charging_station_details", details)

        offline = details.get("offlineCharging")
        if isinstance(offline, dict):
            changed |= self._set_if_changed(
                station, "offline_charging_enabled", self._as_bool(offline.get("enabled"))
            )
            changed |= self._set_if_changed(
                station,
                "offline_failsafe_current_a",
                self._as_int(offline.get("failSafe")),
            )

        for module in details.get("modules") or []:
            if not isinstance(module, dict):
                continue
            changed |= self._merge_dashboard_module(data, module)

        return changed

    def _merge_dashboard_module(self, data: IntegrationData, module: dict[str, Any]) -> bool:
        smart_device = module.get("smartDevice")
        if not isinstance(smart_device, dict):
            return False
        device_type = smart_device.get("type")
        category = device_type.get("category") if isinstance(device_type, dict) else device_type
        if str(category or "").upper() == "LED":
            return self._merge_dashboard_led(data.station, smart_device)
        if str(category or "").upper() != "CARCHARGER":
            return False

        conn = self._dashboard_connector_for_module(data, module, smart_device)
        if conn is None:
            return False

        changed = False
        changed |= self._set_if_changed(
            conn, "dashboard_device_id", self._as_str(smart_device.get("id"))
        )
        changed |= self._set_if_changed(
            conn,
            "dashboard_device_uuid",
            self._as_str(smart_device.get("uuid"))
            or self._device_uuid_from_dashboard_channel(smart_device),
        )
        changed |= self._set_if_changed(
            conn, "dashboard_device_name", self._as_str(smart_device.get("name"))
        )

        props = smart_device.get("configurationProperties") or []
        changed |= self._set_dashboard_int_prop(
            conn, props, "max_current", "etc.smart.device.type.car.charger.config.max.current"
        )
        changed |= self._set_dashboard_int_prop(
            conn, props, "min_current", "etc.smart.device.type.car.charger.config.min.current"
        )
        changed |= self._set_dashboard_int_prop(
            conn, props, "min_surpluspct", "etc.smart.device.type.car.charger.config.min.excesspct"
        )
        changed |= self._set_dashboard_int_prop(
            conn,
            props,
            "support_grid",
            "etc.smart.device.type.car.charger.config.max.gridassistanceamps",
        )

        car_charger = smart_device.get("carCharger")
        if isinstance(car_charger, dict):
            changed |= self._merge_dashboard_car_charger_fallback(conn, car_charger)

        return changed

    def _merge_dashboard_led(self, station: StationState, smart_device: dict[str, Any]) -> bool:
        changed = self._set_if_changed(
            station, "dashboard_led_device_id", self._as_str(smart_device.get("id"))
        )
        props = smart_device.get("configurationProperties") or []
        value = self._dashboard_prop_int(
            props, "etc.smart.device.type.car.charger.led.config.brightness"
        )
        changed |= self._set_if_changed(station, "led_brightness", value)
        return changed

    def _dashboard_connector_for_module(
        self,
        data: IntegrationData,
        module: dict[str, Any],
        smart_device: dict[str, Any],
    ) -> ConnectorState | None:
        candidates = {
            self._as_str(smart_device.get("uuid")),
            self._device_uuid_from_dashboard_channel(smart_device),
        }
        for candidate in candidates:
            if candidate and candidate in data.connectors:
                return data.connectors[candidate]

        position = self._as_int(module.get("position"))
        if position is None:
            return None
        for conn in data.connectors.values():
            if conn.connector_number == position:
                return conn
        return None

    def _merge_dashboard_car_charger_fallback(
        self, conn: ConnectorState, car_charger: dict[str, Any]
    ) -> bool:
        changed = False
        changed |= self._set_if_empty(
            conn, "connection_status", car_charger.get("connectionStatus")
        )
        changed |= self._set_if_empty(conn, "iec_status", car_charger.get("iecStatus"))
        changed |= self._set_if_empty(conn, "raw_charging_mode", car_charger.get("chargingMode"))
        changed |= self._set_if_empty(
            conn, "optimization_strategy", car_charger.get("optimizationStrategy")
        )

        status = car_charger.get("status")
        if isinstance(status, dict):
            changed |= self._set_if_empty(conn, "status_current", status.get("current"))
            changed |= self._set_if_empty(conn, "session_cause", status.get("current"))
            if conn.stopped_by_cloud is None and status.get("stoppedByCloud") is not None:
                changed |= self._set_if_changed(
                    conn, "stopped_by_cloud", bool(status.get("stoppedByCloud"))
                )

        if conn.selected_mode is None or conn.ui_mode_base is None:
            base = self._derive_base_mode(conn.raw_charging_mode, conn.optimization_strategy)
            changed |= self._set_if_empty(conn, "ui_mode_base", base)
            changed |= self._set_if_empty(conn, "selected_mode", base)

        changed |= self._update_evcc(conn)
        return changed

    @staticmethod
    def _as_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        return None

    @staticmethod
    def _as_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _set_if_empty(self, obj: object, attr: str, value: Any) -> bool:
        if value is None:
            return False
        cur = getattr(obj, attr, None)
        if cur not in (None, "", "Initialize"):
            return False
        return self._set_if_changed(obj, attr, str(value))

    def _set_dashboard_int_prop(
        self, obj: object, props: list[Any], attr: str, spec_name: str
    ) -> bool:
        return self._set_if_changed(obj, attr, self._dashboard_prop_int(props, spec_name))

    def _dashboard_prop_int(self, props: list[Any], spec_name: str) -> int | None:
        raw = self._dashboard_prop_value(props, spec_name)
        if isinstance(raw, dict):
            if "Quantity" in raw and isinstance(raw["Quantity"], dict):
                raw = raw["Quantity"].get("value")
            elif "Integer" in raw:
                raw = raw["Integer"]
            elif "value" in raw:
                raw = raw["value"]
        return self._as_int(raw)

    @staticmethod
    def _dashboard_prop_value(props: list[Any], spec_name: str) -> Any:
        for prop in props:
            if not isinstance(prop, dict):
                continue
            spec = prop.get("spec") or {}
            if not isinstance(spec, dict) or spec.get("name") != spec_name:
                continue
            if "value" in prop:
                return prop.get("value")
            values = prop.get("values")
            if isinstance(values, list) and values:
                return values[0]
        return None

    @staticmethod
    def _device_uuid_from_dashboard_channel(smart_device: dict[str, Any]) -> str | None:
        car_charger = smart_device.get("carCharger")
        if not isinstance(car_charger, dict):
            return None
        channel = car_charger.get("chargingStateUpdateChannel")
        if not isinstance(channel, dict):
            return None
        name = channel.get("name")
        if not isinstance(name, str):
            return None
        marker = "/devices/"
        if marker not in name:
            return None
        return name.split(marker, 1)[1].split("/", 1)[0] or None

    def _merge_dashboard_capacity(self, station: StationState, payload: dict[str, Any]) -> bool:
        changed = False
        changed |= self._set_if_changed(
            station, "capacity_protection_active", self._as_bool(payload.get("active"))
        )
        value = payload.get("capacityMaximumPower")
        if value is not None:
            with suppress(TypeError, ValueError):
                changed |= self._set_if_changed(
                    station, "capacity_maximum_power_kw", round(float(value), 1)
                )
        return changed

    def _merge_dashboard_overload(self, station: StationState, payload: dict[str, Any]) -> bool:
        changed = False
        changed |= self._set_if_changed(
            station, "overload_protection_active", self._as_bool(payload.get("active"))
        )
        changed |= self._set_if_changed(
            station, "overload_maximum_load_a", self._as_int(payload.get("maximumLoad"))
        )
        return changed

    # =====================================================================
    # MQTT helpers + merge logic (call via mqtt_gateway callback)
    # =====================================================================

    # Topic parsing helpers
    def _device_uuid_from_topic(self, topic: str) -> str | None:
        """Return device UUID from .../devices/<UUID>/... ; None if not present."""
        marker = "/devices/"
        i = topic.find(marker)
        if i == -1:
            return None
        rest = topic[i + len(marker) :]
        return rest.split("/", 1)[0] if rest else None

    def _property_name_from_topic(self, topic: str) -> str | None:
        """Return property name from .../property/<name> (first segment)."""
        marker = "/property/"
        i = topic.find(marker)
        if i == -1:
            return None
        name = topic[i + len(marker) :]
        return name.split("/", 1)[0] if name else None

    def _station_serial_from_topic(self, topic: str) -> str | None:
        """Return station serial from .../acchargingstation/v1/<serial>/..."""
        marker = "/acchargingstation/v1/"
        i = topic.find(marker)
        if i == -1:
            return None
        rest = topic[i + len(marker) :]
        return rest.split("/", 1)[0] if rest else None

    @staticmethod
    def _as_int(v: Any, default: int | None = None) -> int | None:
        try:
            return int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    # UI mappings
    @staticmethod
    def _derive_base_mode(mode: str | None, strategy: str | None) -> str:
        """Map raw API mode/strategy to the UI mode (STANDARD/SMART/SOLAR)."""
        m = (mode or "").upper()
        s = (strategy or "").upper()

        if m in ("NORMAL", "PAUSED", "STANDARD"):
            return "STANDARD"
        if s == "EXCESS_ONLY":
            return "SOLAR"
        if s == "SCHEDULES_FIRST_THEN_EXCESS":
            return "SMART"
        if m == "SMART":
            return "SMART"
        return "STANDARD"

    @staticmethod
    def _is_paused(
        raw_mode: str | None, charging_state: str | None, evse_cause: str | None
    ) -> bool:
        """Pause or not."""
        if (raw_mode or "").upper() == "PAUSED":
            return True
        if (charging_state or "").upper() == "SUSPENDED" and (evse_cause or "").upper().startswith(
            "SUSPENDED_EVSE"
        ):
            return True
        return False

    @staticmethod
    def _derive_evcc_letter(iec: str | None, _charging_state: str | None = None) -> str | None:
        if not iec:
            return None
        first = iec.strip()[:1].upper()
        return first if first in ("A", "B", "C", "E", "F") else None

    @staticmethod
    def _evcc_code(letter: str | None) -> int | None:
        return {"A": 0, "B": 1, "C": 2, "E": 3, "F": 4}.get((letter or "").upper())

    def apply_mqtt_connection_change(self, up: bool) -> None:
        data = self.data
        if not data:
            return
        st = data.station
        changed = False
        if up:
            st.last_mqtt_rx = _now()
            if not getattr(st, "mqtt_connected", False):
                st.mqtt_connected = True
                changed = True
            changed = True
        else:
            if getattr(st, "mqtt_connected", None) is not False:
                st.mqtt_connected = False
                st.last_mqtt_rx = _now()
                changed = True

        if changed:
            self.async_set_updated_data(data)

    # Main entry: called by mqtt_gateway
    def apply_mqtt_properties(self, topic: str, payload: dict) -> None:
        """Merge incoming MQTT properties/state in the current snapshot."""
        data = self.data
        if not data:
            return

        changed = False

        st = getattr(data, "station", None)
        if st is not None:
            st.last_mqtt_rx = _now()
            changed = True
            if not getattr(st, "mqtt_connected", False):
                st.mqtt_connected = True

        # devices/updated (connector)
        if "/etc/carcharger/acchargingcontroller/" in topic and topic.endswith("/devices/updated"):
            changed |= self._handle_connector_devices_updated(payload)

        # Connector-device messages
        elif "/etc/carcharger/acchargingcontroller/" in topic and "/devices/" in topic:
            changed |= self._handle_connector_mqtt(topic, payload)

        # Aggregated power
        elif topic.endswith("/power"):
            changed |= self._handle_power(topic, payload)

        # Station-level properties
        elif "/etc/chargingstation/acchargingstation/" in topic and topic.endswith("/properties"):
            changed |= self._handle_station_properties(payload)

        # LED brightness
        elif "/etc/led/acledcontroller/" in topic and topic.endswith("/devices/updated"):
            changed |= self._handle_led_updated(payload)

        if changed:
            self.async_set_updated_data(data)

    # ---------- split helpers to reduce complexity ----------
    def _handle_connector_devices_updated(self, payload: dict) -> bool:
        """Process devices/updated for AC charging controller (min/max current, min.excesspct, etc.)."""
        data = self.data
        if not data:
            return False

        dev_uuid = payload.get("deviceUUID")
        if not dev_uuid or dev_uuid not in data.connectors:
            return False

        conn = data.connectors[dev_uuid]
        changed = False

        # min/max current
        if "minimumCurrent" in payload:
            mc_min = self._as_int(payload.get("minimumCurrent"), conn.min_current)
            if mc_min is not None:
                changed |= self._set_if_changed(conn, "min_current", mc_min)

        if "maximumCurrent" in payload:
            mc_max = self._as_int(payload.get("maximumCurrent"), conn.max_current)
            if mc_max is not None:
                changed |= self._set_if_changed(conn, "max_current", mc_max)

        # custom configuration properties: min excesspct, grid support + connector number
        ccp = payload.get("customConfigurationProperties") or {}
        if isinstance(ccp, dict):
            v = ccp.get("etc.smart.device.type.car.charger.config.min.excesspct")
            v_int = self._as_int(v, None)
            if v_int is not None:
                changed |= self._set_if_changed(conn, "min_surpluspct", v_int)

            grid_support = self._as_int(
                ccp.get("etc.smart.device.type.car.charger.config.max.gridassistanceamps"),
                None,
            )
            if grid_support is not None:
                changed |= self._set_if_changed(conn, "support_grid", grid_support)

            n = ccp.get("etc.smart.device.type.car.charger.smappee.charger.number")
            try:
                n_int = int(n) if n is not None else None
            except (TypeError, ValueError):
                n_int = None
            if n_int is not None:
                changed |= self._set_if_changed(conn, "connector_number", n_int)

        # percentageLimit (only in NORMAL / strategy NONE)
        if "percentageLimit" in payload:
            if (conn.optimization_strategy or "").upper() == "NONE":
                pct = self._as_int(payload.get("percentageLimit"), None)
                if pct is not None:
                    if self._set_if_changed(conn, "selected_percentage_limit", pct):
                        changed = True
                    rng = max(int(conn.max_current) - int(conn.min_current), 1)
                    cur = round((pct / 100.0) * rng + float(conn.min_current), 1)
                    changed |= self._set_if_changed(conn, "selected_current_limit", cur)
            # else: in SMART/SOLAR do not update

        return changed

    def _handle_connector_mqtt(self, topic: str, payload: dict) -> bool:
        data = self.data
        if not data:
            return False
        dev_uuid = self._device_uuid_from_topic(topic)
        if not dev_uuid or dev_uuid not in data.connectors:
            return False
        conn: ConnectorState = data.connectors[dev_uuid]

        if topic.endswith("/state"):
            return self._handle_connector_state(conn, payload)

        if "/property/" in topic and self._property_name_from_topic(topic) == "chargingstate":
            changed = self._handle_connector_property_chargingstate(conn, payload, dev_uuid)
            changed |= self._sync_station_availability_from_chargingstate(payload)
            return changed

        return False

    @staticmethod
    def _handle_connector_state(conn: ConnectorState, payload: dict) -> bool:
        changed = False
        cs = payload.get("connectionStatus")
        if cs and str(cs) != getattr(conn, "connection_status", None):
            conn.connection_status = str(cs)
            changed = True
        errs = payload.get("configurationErrors")
        if isinstance(errs, list):
            new_errs = [str(e) for e in errs]
            if new_errs != (conn.configuration_errors or []):
                conn.configuration_errors = new_errs
                changed = True
        return changed

    def _handle_connector_property_chargingstate(
        self, conn: ConnectorState, payload: dict, connector_uuid: str | None = None
    ) -> bool:
        was_active = self._is_session_active(conn)
        changed = False
        changed |= self._merge_cs_primary(conn, payload)
        changed |= self._merge_cs_context(conn, payload)
        changed |= self._merge_cs_modes(conn, payload)
        changed |= self._merge_cs_limits_availability(conn, payload)

        changed |= self._update_evcc(conn)
        self._handle_session_tracking_transition(conn, was_active, connector_uuid)
        return changed

    def async_start_session_tracking(self) -> None:
        """Start the state-driven recent-session refresh manager."""
        if self._session_tracking_started:
            return
        self._session_tracking_started = True
        self._schedule_session_refresh("startup", delay=0, force=True)
        self._sync_session_tracking_from_current_state()

    async def async_shutdown(self) -> None:
        """Cancel session refresh callbacks and background tasks."""
        self._shutting_down = True

        self._cancel_session_refresh()
        self._cancel_active_session_loop()
        self._cancel_final_session_refreshes()

        task = self._dashboard_refresh_task
        if task is not None and not task.done():
            task.cancel()

        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def _cancel_session_refresh(self) -> None:
        """Cancel one-shot delayed session refresh."""
        if self._session_refresh_unsub is None:
            return

        unsub = self._session_refresh_unsub
        self._session_refresh_unsub = None
        with suppress(RuntimeError):
            unsub()

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

    @staticmethod
    def _normalized_session_value(value: object) -> str:
        return str(value or "").strip().upper()

    def _is_session_active(self, conn: ConnectorState) -> bool:
        state = self._normalized_session_value(conn.session_state)
        mode = self._normalized_session_value(conn.raw_charging_mode)
        cause = self._normalized_session_value(conn.session_cause)
        status = self._normalized_session_value(conn.status_current)
        return (
            state in _SESSION_ACTIVE_STATES
            or state in _SESSION_PAUSED_STATES
            or mode == "PAUSED"
            or cause in _SESSION_ACTIVE_STATES
            or cause in _SESSION_PAUSED_STATES
            or status in _SESSION_ACTIVE_STATES
            or status in _SESSION_PAUSED_STATES
            or bool(conn.paused)
        )

    def _is_session_paused(self, conn: ConnectorState) -> bool:
        state = self._normalized_session_value(conn.session_state)
        mode = self._normalized_session_value(conn.raw_charging_mode)
        cause = self._normalized_session_value(conn.session_cause)
        status = self._normalized_session_value(conn.status_current)
        return (
            state in _SESSION_PAUSED_STATES
            or mode == "PAUSED"
            or cause in _SESSION_PAUSED_STATES
            or status in _SESSION_PAUSED_STATES
            or bool(conn.paused)
        )

    def _is_session_finished(self, conn: ConnectorState) -> bool:
        state = self._normalized_session_value(conn.session_state)
        cause = self._normalized_session_value(conn.session_cause)
        status = self._normalized_session_value(conn.status_current)
        return (
            state in _SESSION_STOPPED_STATES
            or cause in _SESSION_STOPPED_STATES
            or status in _SESSION_STOPPED_STATES
        )

    def _active_session_connectors(self) -> list[ConnectorState]:
        data = self.data
        if not data:
            return []
        return [conn for conn in data.connectors.values() if self._is_session_active(conn)]

    def _session_loop_interval(self) -> int:
        active = self._active_session_connectors()
        if active and all(self._is_session_paused(conn) for conn in active):
            return SESSION_PAUSED_REFRESH_INTERVAL
        return SESSION_ACTIVE_REFRESH_INTERVAL

    def _sync_session_tracking_from_current_state(self) -> None:
        if self._active_session_connectors():
            self._ensure_active_session_loop()

    def _handle_session_tracking_transition(
        self, conn: ConnectorState, was_active: bool, connector_uuid: str | None
    ) -> None:
        if not self._session_tracking_started:
            return

        is_active = self._is_session_active(conn)
        if is_active:
            self._cancel_final_session_refreshes()
            self._ensure_active_session_loop()

            if not was_active:
                self._schedule_session_refresh(
                    f"connector {anonymize_uuid(connector_uuid) or '?'} active",
                    delay=SESSION_START_REFRESH_DELAY,
                )
            return

        if was_active or self._is_session_finished(conn):
            if not self._active_session_connectors():
                self._cancel_active_session_loop()
            self._schedule_final_session_refreshes(connector_uuid)

    def _ensure_active_session_loop(self) -> None:
        """Start periodic session refresh while a charging session is active."""
        if self._shutting_down:
            return

        interval = self._session_loop_interval()

        if self._session_active_loop_unsub is not None:
            if self._session_active_loop_interval == interval:
                return
            self._cancel_active_session_loop()

        self._session_active_loop_interval = interval

        async def _refresh_active_session(_now: Any) -> None:
            if self._shutting_down:
                self._cancel_active_session_loop()
                return

            if not self._active_session_connectors():
                self._cancel_active_session_loop()
                return

            await self._async_refresh_recent_sessions("active session interval")

            if not self._active_session_connectors():
                self._cancel_active_session_loop()
                return

            new_interval = self._session_loop_interval()
            if new_interval != self._session_active_loop_interval:
                self._ensure_active_session_loop()

        self._session_active_loop_unsub = async_track_time_interval(
            self.hass,
            _refresh_active_session,
            timedelta(seconds=interval),
        )

    def _cancel_active_session_loop(self) -> None:
        """Cancel periodic active-session refresh."""
        if self._session_active_loop_unsub is not None:
            unsub = self._session_active_loop_unsub
            self._session_active_loop_unsub = None
            with suppress(RuntimeError):
                unsub()

        self._session_active_loop_interval = None

    def _schedule_final_session_refreshes(self, connector_uuid: str | None) -> None:
        """Schedule final delayed refreshes after a charging session ends."""
        if self._shutting_down:
            return

        self._cancel_final_session_refreshes()

        for delay in SESSION_FINAL_REFRESH_DELAYS:
            unsub_holder: dict[str, CALLBACK_TYPE] = {}

            async def _refresh(
                _now: Any,
                delay: int = delay,
                unsub_holder: dict[str, CALLBACK_TYPE] = unsub_holder,
            ) -> None:
                unsub = unsub_holder.get("unsub")
                if unsub is not None:
                    with suppress(ValueError):
                        self._session_final_refresh_unsubs.remove(unsub)
                if self._shutting_down:
                    return

                await self._async_refresh_recent_sessions(
                    f"connector {anonymize_uuid(connector_uuid) or '?'} finalizing after {delay}s",
                    force=True,
                )

            unsub = async_call_later(self.hass, delay, _refresh)
            unsub_holder["unsub"] = unsub
            self._session_final_refresh_unsubs.append(unsub)

    def _cancel_final_session_refreshes(self) -> None:
        """Cancel all final delayed session refresh callbacks."""
        for unsub in list(self._session_final_refresh_unsubs):
            with suppress(RuntimeError, ValueError):
                unsub()
        self._session_final_refresh_unsubs.clear()

    def _schedule_session_refresh(self, reason: str, *, delay: int, force: bool = False) -> None:
        """Schedule one delayed recent-session refresh."""
        if self._shutting_down:
            return

        self._cancel_session_refresh()

        async def _refresh(_now: Any) -> None:
            self._session_refresh_unsub = None

            if self._shutting_down:
                return

            await self._async_refresh_recent_sessions(reason, force=force)

        self._session_refresh_unsub = async_call_later(self.hass, delay, _refresh)

    async def _async_get_recent_sessions(self) -> list[dict[str, Any]]:
        """Fetch recent charging sessions from the connector endpoints."""
        pairs = list(self.connector_clients.items())
        if not pairs:
            _LOGGER.warning("Skipping recent session refresh: no connector clients available")
            return []

        results = await asyncio.gather(
            *(client.async_get_recent_sessions() for _, client in pairs),
            return_exceptions=True,
        )
        sessions: list[dict[str, Any]] = []
        errors: list[tuple[str, Exception]] = []

        for (connector_uuid, _client), result in zip(pairs, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, ConfigEntryAuthFailed):
                    raise result
                if isinstance(result, Exception):
                    errors.append((connector_uuid, result))
                    self._log_connector_session_transition(connector_uuid, False, result)
                    continue
                raise result
            self._log_connector_session_transition(connector_uuid, True)
            sessions.extend(session for session in result if isinstance(session, dict))

        if errors and len(errors) == len(pairs):
            raise errors[0][1]
        return sessions

    async def _async_refresh_recent_sessions(self, reason: str, *, force: bool = False) -> None:
        if self._session_refresh_lock.locked():
            _LOGGER.debug("Skipping recent session refresh; previous refresh still running")
            return

        async with self._session_refresh_lock:
            now = _now()
            if not force and now - self._last_session_api_attempt < SESSION_MIN_REFRESH_INTERVAL:
                _LOGGER.debug("Skipping recent session refresh for %s; throttled", reason)
                return
            self._last_session_api_attempt = now

            try:
                recent_sessions = await self._async_get_recent_sessions()
            except ConfigEntryAuthFailed:
                self._start_background_reauth()
                return
            except (TimeoutError, ClientError, RuntimeError) as err:
                _LOGGER.warning("Recent session refresh failed for %s: %s", reason, err)
                return

            self._last_session_api_update = now
            if self.data:
                self.async_set_updated_data(replace(self.data, recent_sessions=recent_sessions))
            _LOGGER.debug("Recent sessions refreshed for %s", reason)

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

    def _merge_cs_primary(self, conn: ConnectorState, payload: dict) -> bool:
        v = self._get_any(payload, "chargingState", "chargingstate")
        return self._set_if_changed(conn, "session_state", str(v)) if v is not None else False

    def _merge_cs_context(self, conn: ConnectorState, payload: dict) -> bool:
        changed = False

        status_obj = self._get_any(payload, "status")
        if isinstance(status_obj, dict):
            evse_cur = status_obj.get("current")
            changed |= self._set_if_changed(
                conn, "session_cause", str(evse_cur) if evse_cur else None
            )
            changed |= self._set_if_changed(
                conn, "status_current", str(evse_cur) if evse_cur else None
            )
            sbc = status_obj.get("stoppedByCloud")
            changed |= self._set_if_changed(
                conn, "stopped_by_cloud", bool(sbc) if sbc is not None else None
            )

        iec_obj = self._get_any(payload, "iecStatus", "iecstatus")
        iec_cur = iec_obj.get("current") if isinstance(iec_obj, dict) else iec_obj
        changed |= self._set_if_changed(conn, "iec_status", str(iec_cur) if iec_cur else None)

        return changed

    def _merge_cs_limits_availability(self, conn: ConnectorState, payload: dict) -> bool:
        changed = False

        pct_raw = self._get_any(payload, "percentageLimit", "percentagelimit")
        if pct_raw is not None:
            # Only in NORMAL (strategy == NONE)
            if (conn.optimization_strategy or "").upper() == "NONE":
                pct = self._as_int(pct_raw, conn.selected_percentage_limit)
                if pct is not None:
                    if self._set_if_changed(conn, "selected_percentage_limit", pct):
                        changed = True
                    rng = max(int(conn.max_current) - int(conn.min_current), 1)
                    cur = round((pct / 100.0) * rng + float(conn.min_current), 1)
                    changed |= self._set_if_changed(conn, "selected_current_limit", cur)

        avail = self._get_any(payload, "available")
        if avail is not None:
            changed |= self._set_if_changed(conn, "available", bool(avail))

        return changed

    def _sync_station_availability_from_chargingstate(self, payload: dict) -> bool:
        """Mirror MQTT chargingstate.available to the station-level availability switch."""
        data = self.data
        if not data:
            return False

        avail = self._get_any(payload, "available")
        if avail is None:
            return False

        return self._set_if_changed(data.station, "available", bool(avail))

    def _merge_cs_modes(self, conn: ConnectorState, payload: dict) -> bool:
        changed = False
        mode = self._get_any(payload, "chargingMode", "chargingmode")
        if mode is not None:
            changed |= self._set_if_changed(conn, "raw_charging_mode", str(mode))

        strategy = self._get_any(payload, "optimizationStrategy", "optimizationstrategy")
        if strategy is not None:
            changed |= self._set_if_changed(conn, "optimization_strategy", str(strategy))

        if (conn.raw_charging_mode or "").upper() == "PAUSED":
            changed |= self._set_if_changed(conn, "ui_mode_base", "STANDARD")
            changed |= self._set_if_changed(conn, "selected_mode", "STANDARD")
        else:
            base = self._derive_base_mode(conn.raw_charging_mode, conn.optimization_strategy)
            changed |= self._set_if_changed(conn, "ui_mode_base", base)
            changed |= self._set_if_changed(conn, "selected_mode", base)  # backward compat
        paused = self._is_paused(conn.raw_charging_mode, conn.session_state, conn.session_cause)
        changed |= self._set_if_changed(conn, "paused", paused)
        return changed

    def _update_evcc(self, conn: ConnectorState) -> bool:
        new_letter = self._derive_evcc_letter(conn.iec_status)
        if new_letter is None:
            return False
        changed = self._set_if_changed(conn, "evcc_state", new_letter)
        new_code = self._evcc_code(new_letter)
        changed |= self._set_if_changed(conn, "evcc_state_code", new_code)
        return changed

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

        energy_idx_list = cast(list[int], energy_idxs)
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

    def _handle_power(self, topic: str, payload: dict) -> bool:
        data = self.data
        if not data:
            return False
        st = data.station
        changed = False

        idx_map = (self._power_index_maps_by_topic or {}).get(topic)
        if not idx_map:
            return False
        grid = idx_map.get("grid", {})
        pv = idx_map.get("pv", {})
        cars = idx_map.get("cars", {}) or {}
        roles = []
        if any(grid.values()):
            roles.append("grid")
        if any(pv.values()):
            roles.append("pv")
        if cars:
            roles.append("cars")
        _LOGGER.debug(
            "Smappee MQTT power apply: topic=%s roles=%s payload_keys=%s",
            redact_mqtt_topic(topic),
            roles,
            list(payload.keys()),
        )

        # Station groups
        changed |= self._apply_station_group(
            st,
            payload,
            grid.get("power", []),
            grid.get("current", []),
            grid.get("energy", []),
            "grid",
            grid.get("power_field"),
        )
        changed |= self._apply_station_group(
            st,
            payload,
            pv.get("power", []),
            pv.get("current", []),
            pv.get("energy", []),
            "pv",
            pv.get("power_field"),
        )

        # Connectors
        for uuid, m in cars.items():
            conn = data.connectors.get(uuid)
            if not conn:
                continue
            changed |= self._apply_connector_values(
                conn,
                payload,
                m.get("power", []),
                m.get("current", []),
                m.get("energy", []),
                m.get("power_field"),
            )

        # Aggregate house consumption (positive load behind meter). Allow real zero.
        cp = payload.get("consumptionPower")
        if isinstance(cp, int | float):
            changed |= self._set_if_changed(st, "house_consumption_power", int(cp))

        # Aggregate solar production (PV). Allow zero (night) to overwrite previous value.
        sp = payload.get("solarPower")
        if isinstance(sp, int | float):
            changed |= self._set_if_changed(st, "pv_power_total", int(sp))

        return changed

    def _handle_station_properties(self, payload: dict) -> bool:
        changed = False
        station: StationState = self.data.station
        if "available" in payload:
            avail = bool(payload["available"])
            if avail != getattr(station, "available", None):
                station.available = avail
                changed = True
        if "ledBrightness" in payload:
            new_bri = self._as_int(payload.get("ledBrightness"))
            if new_bri is not None and new_bri != getattr(station, "led_brightness", None):
                station.led_brightness = new_bri
                changed = True
        return changed

    def _handle_led_updated(self, payload: dict) -> bool:
        """Parse LED controller 'devices/updated' and update station.led_brightness."""
        vals = payload.get("configurationPropertyValues") or []
        if not isinstance(vals, list):
            return False

        new_bri = None
        for item in vals:
            if (
                isinstance(item, dict)
                and item.get("propertySpecName")
                == "etc.smart.device.type.car.charger.led.config.brightness"
            ):
                new_bri = self._as_int(item.get("value"))
                break

        if new_bri is None:
            return False

        station = self.data.station
        if new_bri != getattr(station, "led_brightness", None):
            station.led_brightness = new_bri
            return True

        return False


# Backwards-compatible public name used by older tests and platform code.
SmappeeCoordinator = SmappeeStationCoordinator
