# custom_components/smappee_ev/coordinator.py
from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from contextlib import suppress
from datetime import timedelta
import logging
from time import time as _now
from typing import Any, cast

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import SmappeeApiClient
from .const import BASE_URL, MQTT_TRACK_INTERVAL_SEC
from .data import ConnectorState, IntegrationData, StationState

_LOGGER = logging.getLogger(__name__)


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


class SmappeeCoordinator(DataUpdateCoordinator[IntegrationData]):
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
        self._power_index_map: dict[str, Any] | None = None

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
            for (uuid, client), res in zip(pairs, results, strict=True):
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
                    connectors_state[uuid] = cast(ConnectorState, res)

            await self._ensure_power_index_map()

            return IntegrationData(station=station_state, connectors=connectors_state)

        except (ClientError, TimeoutError, asyncio.CancelledError) as err:
            raise UpdateFailed(f"Error fetching Smappee data: {err}") from err

    async def _ensure_power_index_map(self) -> None:
        """Load and cache metering index mapping once."""
        if self._power_index_map is not None:
            return

        cfg = await self.station_client.async_get_metering_configuration()
        self._power_index_map = self._build_power_index_map(cfg or {})

    def _build_power_index_map(self, cfg: dict) -> dict:
        """
        Build mapping from /meteringconfiguration.

        Returns:
        {
            "grid": {"power":[...], "cons":[...]},
            "pv":   {"power":[...], "cons":[...]},
            "cars": { "<uuid>": {"power":[...], "cons":[...], "position": int|None, "serial": str|None } }
        }
        """
        mapping: dict[str, Any] = {
            "grid": {"power": [], "cons": []},
            "pv": {"power": [], "cons": []},
            "cars": {},
        }

        # measurements → GRID / PRODUCTION(PV)
        for meas in cfg.get("measurements") or []:
            mtype = (meas.get("type") or "").upper()
            chans = meas.get("channels") or []
            pidx = [int(ch["powerTopicIndex"]) for ch in chans if "powerTopicIndex" in ch]
            cidx = [int(ch["consumptionIndex"]) for ch in chans if "consumptionIndex" in ch]
            if mtype == "GRID":
                mapping["grid"]["power"], mapping["grid"]["cons"] = pidx, cidx
            elif mtype == "PRODUCTION":
                mapping["pv"]["power"], mapping["pv"]["cons"] = pidx, cidx

        # chargingStations[].chargers[] → per-connector
        for cs in cfg.get("chargingStations") or []:
            for chg in cs.get("chargers") or []:
                uuid = chg.get("uuid")
                if not uuid:
                    continue
                chans = chg.get("channels") or []
                pidx = [int(ch["powerTopicIndex"]) for ch in chans if "powerTopicIndex" in ch]
                cidx = [int(ch["consumptionIndex"]) for ch in chans if "consumptionIndex" in ch]
                mapping["cars"][uuid] = {
                    "power": pidx,
                    "cons": cidx,
                    "position": chg.get("position"),
                    "serial": chg.get("serialNumber"),
                }
        return mapping

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
                        if (
                            spec.get("name")
                            == "etc.smart.device.type.car.charger.led.config.brightness"
                        ):
                            raw = prop.get("value")
                            val = raw.get("value") if isinstance(raw, dict) else raw
                            with suppress(TypeError, ValueError):
                                led_brightness = _to_int(val, default=led_brightness)
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
                with suppress(TypeError, ValueError):
                    selected_percentage = int(val)

        # configurationProperties: max/min current, min.excesspct
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
                with suppress(TypeError, ValueError):
                    min_surpluspct = _to_int(val, default=min_surpluspct)

        client.min_current = min_current
        client.max_current = max_current
        client.selected_percentage_limit = selected_percentage
        client.selected_current_limit = selected_current

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
        """Map to NORMAL/SMART/SOLAR."""
        s = (strategy or "").upper()
        if s == "EXCESS_ONLY":
            return "SOLAR"
        if s == "SCHEDULES_FIRST_THEN_EXCESS":
            return "SMART"
        return "NORMAL"

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
        now = _now()

        if up:
            if not getattr(st, "mqtt_connected", False):
                st.mqtt_connected = True
                changed = True
            st.last_mqtt_rx = now
            changed = True
        else:
            grace = max(2 * MQTT_TRACK_INTERVAL_SEC, 10)
            last = float(getattr(st, "last_mqtt_rx", 0) or 0)
            if (now - last) > grace and getattr(st, "mqtt_connected", True):
                st.mqtt_connected = False
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
        heartbeat_touch = False

        st = getattr(data, "station", None)
        if st is not None:
            st.last_mqtt_rx = _now()
            if not getattr(st, "mqtt_connected", False):
                st.mqtt_connected = True
                changed = True

        # devices/updated (connector)
        if "/etc/carcharger/acchargingcontroller/" in topic and topic.endswith("/devices/updated"):
            changed |= self._handle_connector_devices_updated(payload)

        # Connector-device messages
        elif "/etc/carcharger/acchargingcontroller/" in topic and "/devices/" in topic:
            changed |= self._handle_connector_mqtt(topic, payload)

        # Aggregated power
        elif topic.endswith("/power"):
            changed |= self._handle_power(payload)

        # Station-level properties
        elif "/etc/chargingstation/acchargingstation/" in topic and topic.endswith("/properties"):
            changed |= self._handle_station_properties(payload)

        # LED brightness
        elif "/etc/led/acledcontroller/" in topic and topic.endswith("/devices/updated"):
            changed |= self._handle_led_updated(payload)

        if changed or heartbeat_touch:
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

        # custom configuration properties: min excesspct + connector number
        ccp = payload.get("customConfigurationProperties") or {}
        if isinstance(ccp, dict):
            v = ccp.get("etc.smart.device.type.car.charger.config.min.excesspct")
            v_int = self._as_int(v, None)
            if v_int is not None:
                changed |= self._set_if_changed(conn, "min_surpluspct", v_int)

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
                    cur = int(round((pct / 100) * rng + int(conn.min_current)))
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
            return self._handle_connector_property_chargingstate(conn, payload)

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

    def _handle_connector_property_chargingstate(self, conn: ConnectorState, payload: dict) -> bool:
        changed = False
        changed |= self._merge_cs_primary(conn, payload)
        changed |= self._merge_cs_context(conn, payload)
        changed |= self._merge_cs_modes(conn, payload)
        changed |= self._merge_cs_limits_availability(conn, payload)

        changed |= self._update_evcc(conn)
        return changed

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
                    cur = int(round((pct / 100) * rng + int(conn.min_current)))
                    changed |= self._set_if_changed(conn, "selected_current_limit", cur)

        avail = self._get_any(payload, "available")
        if avail is not None:
            changed |= self._set_if_changed(conn, "available", bool(avail))

        return changed

    def _merge_cs_modes(self, conn: ConnectorState, payload: dict) -> bool:
        changed = False
        mode = self._get_any(payload, "chargingMode", "chargingmode")
        if mode is not None:
            changed |= self._set_if_changed(conn, "raw_charging_mode", str(mode))

        strategy = self._get_any(payload, "optimizationStrategy", "optimizationstrategy")
        if strategy is not None:
            changed |= self._set_if_changed(conn, "optimization_strategy", str(strategy))

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
        cons_idxs: list[int],
        power_key_prefix: str,  # "grid" or "pv"
    ) -> bool:
        changed = False
        active = payload.get("activePowerData") or []
        currents_ma = payload.get("currentData") or []
        imp_wh = payload.get("importActiveEnergyData") or []
        exp_wh = payload.get("exportActiveEnergyData") or []

        p_ph = _pick(active, power_idxs)
        if p_ph:
            changed |= self._set_if_changed(st, f"{power_key_prefix}_power_phases", p_ph)
            changed |= self._set_if_changed(st, f"{power_key_prefix}_power_total", sum(p_ph))
            i_ph = _amps_from_ma(_pick(currents_ma, power_idxs))
            if i_ph:
                changed |= self._set_if_changed(st, f"{power_key_prefix}_current_phases", i_ph)

        if cons_idxs:
            if power_key_prefix == "grid":
                changed |= self._set_if_changed(
                    st, "grid_energy_import_kwh", round(sum(_pick(imp_wh, cons_idxs)) / 1000.0, 3)
                )
                changed |= self._set_if_changed(
                    st, "grid_energy_export_kwh", round(sum(_pick(exp_wh, cons_idxs)) / 1000.0, 3)
                )
            else:  # pv
                changed |= self._set_if_changed(
                    st, "pv_energy_import_kwh", round(sum(_pick(imp_wh, cons_idxs)) / 1000.0, 3)
                )
        return changed

    def _apply_connector_values(
        self,
        conn,  # ConnectorState
        payload: dict,
        power_idxs: list[int],
        cons_idxs: list[int],
    ) -> bool:
        changed = False
        active = payload.get("activePowerData") or []
        currents_ma = payload.get("currentData") or []
        imp_wh = payload.get("importActiveEnergyData") or []

        p_ph = _pick(active, power_idxs)
        i_ma = _pick(currents_ma, power_idxs)
        imp_kwh = round(sum(_pick(imp_wh, cons_idxs)) / 1000.0, 3) if cons_idxs else None

        changed |= self._set_if_changed(conn, "power_phases", p_ph)
        changed |= self._set_if_changed(conn, "power_total", sum(p_ph) if p_ph else None)
        if i_ma:
            changed |= self._set_if_changed(conn, "current_phases", _amps_from_ma(i_ma))
        changed |= self._set_if_changed(conn, "energy_import_kwh", imp_kwh)
        return changed

    def _handle_power(self, payload: dict) -> bool:
        data = self.data
        if not data:
            return False
        st = data.station
        changed = False

        idx_map = self._power_index_map or {}
        grid = idx_map.get("grid", {})
        pv = idx_map.get("pv", {})
        cars = idx_map.get("cars", {}) or {}

        # Station groups
        changed |= self._apply_station_group(
            st, payload, grid.get("power", []), grid.get("cons", []), "grid"
        )
        changed |= self._apply_station_group(
            st, payload, pv.get("power", []), pv.get("cons", []), "pv"
        )

        # Connectors
        for uuid, m in cars.items():
            conn = data.connectors.get(uuid)
            if not conn:
                continue
            changed |= self._apply_connector_values(
                conn, payload, m.get("power", []), m.get("cons", [])
            )

        # Prefer explicit totals from payload if present (optional)
        cp = payload.get("consumptionPower")
        if isinstance(cp, int | float) and cp != 0:
            changed |= self._set_if_changed(st, "grid_power_total", int(cp))
        sp = payload.get("solarPower")
        if isinstance(sp, int | float) and sp != 0:
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
