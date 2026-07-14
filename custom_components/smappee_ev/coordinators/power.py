"""MQTT power mapping helpers for Smappee station coordinators."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import suppress
import logging
import re
from time import monotonic
from typing import Any

from aiohttp import ClientError
from homeassistant.exceptions import ConfigEntryAuthFailed

from ..api.errors import SmappeeError
from ..api.mqtt_gateway import redact_mqtt_topic
from ..models.state import DashboardObject, HighLevelConfigMap, MqttPayload
from .base import CoordinatorMixin

_LOGGER = logging.getLogger(__name__)

_MQTT_PATH_RE = re.compile(r"\$\.([A-Za-z0-9_]+)\[(\d+)\]")
_POWER_MAP_RETRY_BACKOFF = 60.0


def _to_int(value: object, default: int = 0) -> int:
    """Convert a value to int safely, fallback to default on error."""
    if not isinstance(value, int | float | str):
        return default
    with suppress(TypeError, ValueError):
        return int(value)
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


def _indexes_from_aspect_paths(channel: DashboardObject | None, field: str) -> list[int]:
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
    channel: DashboardObject | None, *fields: str
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


def _active_power_values(payload: MqttPayload, field: str | None = None) -> list[Any]:
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


def _mqtt_channel_topic(channel: DashboardObject | None) -> str | None:
    """Return the MQTT topic advertised by a highlevel channel."""
    if not isinstance(channel, dict):
        return None
    if str(channel.get("protocol") or "").upper() != "MQTT":
        return None
    topic = str(channel.get("name") or "").strip()
    return topic or None


class PowerMixin(CoordinatorMixin):
    """Station MQTT power mapping and application helpers."""

    _power_index_maps_by_topic: dict[str, DashboardObject] | None
    _power_map_retry_after: float

    async def _ensure_power_index_map(self) -> None:
        """Load and cache MQTT power index mapping from Dashboard v10."""
        if self._power_index_maps_by_topic is not None:
            return
        if self.dashboard_client is None:
            return
        if monotonic() < self._power_map_retry_after:
            return

        configs = dict(self._highlevel_configs)
        if not configs:
            try:
                cfg = await self.dashboard_client.async_get_highlevel_configuration(
                    self.station_client.service_location_id
                )
            except ConfigEntryAuthFailed:
                raise
            except SmappeeError as err:
                _LOGGER.warning("MQTT power mapping temporarily unavailable: %s", err)
                self._power_map_retry_after = monotonic() + _POWER_MAP_RETRY_BACKOFF
                return
            except (ClientError, TimeoutError, RuntimeError) as err:
                _LOGGER.warning("MQTT power mapping temporarily unavailable: %s", err)
                self._power_map_retry_after = monotonic() + _POWER_MAP_RETRY_BACKOFF
                return
            if cfg is None:
                self._power_map_retry_after = monotonic() + _POWER_MAP_RETRY_BACKOFF
                return
            configs[int(self.station_client.service_location_id)] = cfg

        mapping = self._build_measurement_index_maps_by_topic_from_highlevel_configs(configs)
        self._power_index_maps_by_topic = mapping or {}
        self._power_map_retry_after = 0.0

    def _build_measurement_index_maps_by_topic_from_highlevel_configs(
        self, configs: HighLevelConfigMap
    ) -> dict[str, DashboardObject] | None:
        """Build MQTT index mappings grouped by exact power topic."""
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
                merged["cars"].update(topic_map["cars"])

        return maps_by_topic or None

    def _build_measurement_index_maps_by_topic_from_highlevel(
        self, cfg: DashboardObject
    ) -> dict[str, DashboardObject] | None:
        """Build MQTT index mappings from Dashboard v10 highlevelconfiguration."""
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

    def _handle_power(self, topic: str, payload: dict) -> bool:
        data = self.data
        if not data:
            return False
        st = data.station
        changed = False

        idx_map = (self._power_index_maps_by_topic or {}).get(topic)
        grid = idx_map.get("grid", {}) if idx_map else {}
        pv = idx_map.get("pv", {}) if idx_map else {}
        cars = (idx_map.get("cars", {}) or {}) if idx_map else {}
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

        for uuid, mapping in cars.items():
            conn = data.connectors.get(uuid)
            if not conn:
                continue
            changed |= self._apply_connector_values(
                conn,
                payload,
                mapping.get("power", []),
                mapping.get("current", []),
                mapping.get("energy", []),
                mapping.get("power_field"),
            )

        cp = payload.get("consumptionPower")
        if isinstance(cp, int | float):
            changed |= self._set_if_changed(st, "house_consumption_power", int(cp))

        sp = payload.get("solarPower")
        if isinstance(sp, int | float):
            changed |= self._set_if_changed(st, "pv_power_total", int(sp))

        always_on = payload.get("alwaysOnPower")
        if isinstance(always_on, int | float):
            changed |= self._set_if_changed(st, "always_on_power", int(always_on))

        return changed

    def _apply_station_group(
        self,
        st,
        payload: dict,
        power_idxs: list[int],
        current_idxs: list[int],
        energy_idxs: list[int] | str,
        power_key_prefix: str | None = None,
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
            else:
                changed |= self._set_if_changed(
                    st,
                    "pv_energy_import_kwh",
                    round(sum(_pick(imp_wh, energy_idx_list)) / 1000.0, 3),
                )
        return changed

    def _apply_connector_values(
        self,
        conn,
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
                val = energy_values[0]
            else:
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
