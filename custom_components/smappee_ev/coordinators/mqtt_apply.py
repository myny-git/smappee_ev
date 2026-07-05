"""MQTT merge helpers for Smappee station coordinators."""

from __future__ import annotations

from contextlib import suppress
import logging
from time import time as _now
from typing import TYPE_CHECKING

from ..models.state import ConnectorState, IntegrationData, StationState

if TYPE_CHECKING:
    from typing import Any

_LOGGER = logging.getLogger(__name__)


class MqttMixin:
    """Station MQTT topic parsing and payload merge helpers."""

    if TYPE_CHECKING:
        data: IntegrationData

        def async_set_updated_data(self, data: IntegrationData) -> None: ...

        def _handle_power(self, topic: str, payload: dict[str, Any]) -> bool: ...

        def _handle_session_tracking_transition(
            self, conn: ConnectorState, was_active: bool, connector_uuid: str | None
        ) -> None: ...

        def _is_session_active(self, conn: ConnectorState) -> bool: ...

        def _set_if_changed(self, obj: object, attr: str, value: Any) -> bool: ...

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
    def _as_int(v: object, default: int | None = None) -> int | None:
        if not isinstance(v, int | float | str):
            return default
        with suppress(TypeError, ValueError):
            return int(v)
        return default

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
        st.last_mqtt_rx = _now()
        if up:
            if not getattr(st, "mqtt_connected", False):
                st.mqtt_connected = True
                changed = True
                _LOGGER.info("Station MQTT availability recovered")
        elif getattr(st, "mqtt_connected", None) is not False:
            st.mqtt_connected = False
            changed = True
            _LOGGER.info("Station MQTT unavailable")

        if changed:
            self.async_set_updated_data(data)

    def apply_mqtt_properties(self, topic: str, payload: dict) -> None:
        """Merge incoming MQTT properties/state in the current snapshot."""
        data = self.data
        if not data:
            return

        changed = False

        st = getattr(data, "station", None)
        if st is not None:
            st.last_mqtt_rx = _now()
            if not getattr(st, "mqtt_connected", False):
                st.mqtt_connected = True
                changed = True

        if "/etc/carcharger/acchargingcontroller/" in topic and topic.endswith("/devices/updated"):
            changed |= self._handle_connector_devices_updated(payload)
        elif "/etc/carcharger/acchargingcontroller/" in topic and "/devices/" in topic:
            changed |= self._handle_connector_mqtt(topic, payload)
        elif topic.endswith("/power"):
            changed |= self._handle_power(topic, payload)
        elif "/etc/chargingstation/acchargingstation/" in topic and topic.endswith("/properties"):
            changed |= self._handle_station_properties(payload)
        elif "/etc/led/acledcontroller/" in topic and topic.endswith("/devices/updated"):
            changed |= self._handle_led_updated(payload)

        if changed:
            self.async_set_updated_data(data)

    def _handle_connector_devices_updated(self, payload: dict) -> bool:
        """Process devices/updated for AC charging controller."""
        data = self.data
        if not data:
            return False

        dev_uuid = payload.get("deviceUUID")
        if not dev_uuid or dev_uuid not in data.connectors:
            return False

        conn = data.connectors[dev_uuid]
        changed = False

        if "minimumCurrent" in payload:
            mc_min = self._as_int(payload.get("minimumCurrent"), conn.min_current)
            if mc_min is not None:
                changed |= self._set_if_changed(conn, "min_current", mc_min)

        if "maximumCurrent" in payload:
            mc_max = self._as_int(payload.get("maximumCurrent"), conn.max_current)
            if mc_max is not None:
                changed |= self._set_if_changed(conn, "max_current", mc_max)

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
            n_int = None
            if n is not None:
                with suppress(TypeError, ValueError):
                    n_int = int(n)
            if n_int is not None:
                changed |= self._set_if_changed(conn, "connector_number", n_int)

        if "percentageLimit" in payload:
            if (conn.optimization_strategy or "").upper() == "NONE":
                pct = self._as_int(payload.get("percentageLimit"), None)
                if pct is not None:
                    if self._set_if_changed(conn, "selected_percentage_limit", pct):
                        changed = True
                    rng = max(int(conn.max_current) - int(conn.min_current), 1)
                    cur = round((pct / 100.0) * rng + float(conn.min_current), 1)
                    changed |= self._set_if_changed(conn, "selected_current_limit", cur)

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
            changed |= self._set_if_changed(conn, "selected_mode", base)
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
