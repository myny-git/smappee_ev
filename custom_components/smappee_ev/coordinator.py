# custom_components/smappee_ev/coordinator.py
from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from contextlib import suppress
from datetime import timedelta
import logging
from time import time as _now
from typing import Any, cast

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import SmappeeApiClient
from .const import DEFAULT_MAX_CURRENT, DEFAULT_MIN_CURRENT
from .data import ConnectorState, IntegrationData, SmappeeEvConfigEntry, StationState
from .oauth import SmappeeAuthError

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


def _volts_from_dv(dv: list[int]) -> list[int]:
    """Convert deci-volt list to V as integers."""
    return [round(x / 10) for x in dv] if dv else []


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
        config_entry: SmappeeEvConfigEntry | None = None,
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
        self._power_index_map: dict[str, Any] | None = None
        self._last_session_api_update = 0.0

    async def _async_update_data(self) -> IntegrationData:
        try:
            # refresh auth once; commands will refresh again when needed
            await self.station_client.ensure_auth()

            # ---- Station snapshot (LED brightness) ----
            station_state = await self._fetch_station_state(self.station_client)

            # ---- Recent sessions ----
            recent_sessions = self.data.recent_sessions if self.data else []

            # ---- Connectors in parallel ----
            pairs = list(self.connector_clients.items())  # [(uuid, client), ...]
            coros = [self._fetch_connector_state(client) for _, client in pairs]
            results = await asyncio.gather(*coros, return_exceptions=True)

            connectors_state: dict[str, ConnectorState] = {}
            for (uuid, client), res in zip(pairs, results, strict=True):
                if isinstance(res, ConfigEntryAuthFailed):
                    raise res
                if isinstance(res, SmappeeAuthError):
                    raise ConfigEntryAuthFailed(f"Smappee authentication failed: {res}") from res
                if isinstance(res, Exception):
                    _LOGGER.warning("Connector %s update failed: %s", uuid, res)
                    # Fall back to safe defaults
                    connectors_state[uuid] = ConnectorState(
                        connector_number=getattr(client, "connector_number", 1),
                        session_state="Initialize",
                        selected_current_limit=None,
                        selected_percentage_limit=None,
                        selected_mode=None,
                        min_current=DEFAULT_MIN_CURRENT,
                        max_current=DEFAULT_MAX_CURRENT,
                        min_surpluspct=None,
                    )
                else:
                    connectors_state[uuid] = cast(ConnectorState, res)

            await self._ensure_power_index_map()

            return IntegrationData(station=station_state, connectors=connectors_state, recent_sessions=recent_sessions)

        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            raise
        except SmappeeAuthError as err:
            raise ConfigEntryAuthFailed(f"Smappee authentication failed: {err}") from err
        except (ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error fetching Smappee data: {err}") from err

    async def _ensure_power_index_map(self) -> None:
        """Load and cache metering index mapping once."""
        if self._power_index_map is not None:
            return

        cfg = await self.station_client.async_get_metering_configuration()
        if cfg is None:
            return

        self._power_index_map = self._build_power_index_map(cfg)

    def _build_power_index_map(self, cfg: dict) -> dict:
        """
        Build mapping from /meteringconfiguration.

        Returns:
        {
            "grid": {"power":[...], "cons":[...], "energy":[...]},
            "pv":   {"power":[...], "cons":[...], "energy":[...]},
            "cars": { "<uuid>": {"power":[...], "cons":[...], "energy":[...], "position": int|None, "serial": str|None } }
        }

        ``power`` indices are used for activePowerData/currentData (sparse,
        indexed by physical CT input number).

        ``energy`` indices are used for importActiveEnergyData/
        exportActiveEnergyData which use a dense sequential layout
        (rank among all configured consumptionIndex values).
        """
        mapping: dict[str, Any] = {
            "grid": {"power": [], "cons": [], "energy": []},
            "pv": {"power": [], "cons": [], "energy": []},
            "cars": {},
        }

        # First pass: collect every consumptionIndex so we can rank them.
        all_cons: list[int] = []
        all_cons.extend(
            int(ch["consumptionIndex"])
            for meas in cfg.get("measurements") or []
            for ch in meas.get("channels") or []
            if "consumptionIndex" in ch
        )
        all_cons.extend(
            int(ch["consumptionIndex"])
            for cs in cfg.get("chargingStations") or []
            for chg in cs.get("chargers") or []
            for ch in chg.get("channels") or []
            if "consumptionIndex" in ch
        )

        # Build rank lookup: consumptionIndex → 0-based position in sorted order.
        sorted_cons = sorted(set(all_cons))
        cons_to_rank = {ci: rank for rank, ci in enumerate(sorted_cons)}

        # measurements → GRID / PRODUCTION(PV)
        for meas in cfg.get("measurements") or []:
            mtype = (meas.get("type") or "").upper()
            chans = meas.get("channels") or []
            pidx = [int(ch["powerTopicIndex"]) for ch in chans if "powerTopicIndex" in ch]
            cidx = [int(ch["consumptionIndex"]) for ch in chans if "consumptionIndex" in ch]
            eidx = [cons_to_rank[ci] for ci in cidx if ci in cons_to_rank]
            if mtype == "GRID":
                mapping["grid"]["power"], mapping["grid"]["cons"] = pidx, cidx
                mapping["grid"]["energy"] = eidx
            elif mtype == "PRODUCTION":
                mapping["pv"]["power"], mapping["pv"]["cons"] = pidx, cidx
                mapping["pv"]["energy"] = eidx

        # chargingStations[].chargers[] → per-connector
        for cs in cfg.get("chargingStations") or []:
            for chg in cs.get("chargers") or []:
                uuid = chg.get("uuid")
                if not uuid:
                    continue
                chans = chg.get("channels") or []
                pidx = [int(ch["powerTopicIndex"]) for ch in chans if "powerTopicIndex" in ch]
                cidx = [int(ch["consumptionIndex"]) for ch in chans if "consumptionIndex" in ch]
                eidx = [cons_to_rank[ci] for ci in cidx if ci in cons_to_rank]
                mapping["cars"][uuid] = {
                    "power": pidx,
                    "cons": cidx,
                    "energy": eidx,
                    "position": chg.get("position"),
                    "serial": chg.get("serialNumber"),
                }
        return mapping

    # -----------------------------
    # Helpers (pure HTTP + parsing)
    # -----------------------------
    async def _fetch_station_state(self, client: SmappeeApiClient) -> StationState:
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
        except asyncio.CancelledError:
            raise
        except SmappeeAuthError as err:
            raise ConfigEntryAuthFailed(
                f"Smappee authentication failed while fetching station state: {err}"
            ) from err
        except (TimeoutError, ClientError) as err:
            _LOGGER.debug("Station brightness fetch exception: %s", err)
        except RuntimeError as err:
            _LOGGER.debug("Station brightness fetch failed: %s", err)

        return StationState(led_brightness=led_brightness, available=True)

    async def _fetch_connector_state(self, client: SmappeeApiClient) -> ConnectorState:
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
            changed |= self._handle_power(payload)

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
            changed = self._handle_connector_property_chargingstate(conn, payload)
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

    def _handle_connector_property_chargingstate(self, conn: ConnectorState, payload: dict) -> bool:
        old_state = getattr(conn, "session_state", None)

        changed = False
        changed |= self._merge_cs_primary(conn, payload)
        changed |= self._merge_cs_context(conn, payload)
        changed |= self._merge_cs_modes(conn, payload)
        changed |= self._merge_cs_limits_availability(conn, payload)
        changed |= self._update_evcc(conn)

        status_dict = payload.get("status") or {}
        mqtt_state = status_dict.get("current") if isinstance(status_dict, dict) else None

        if not mqtt_state:
            mqtt_state = payload.get("chargingState")

        if mqtt_state and old_state != mqtt_state:
            _LOGGER.debug(
                "Charging state changed from %s to %s (via MQTT). Triggering background session refresh.",
                old_state,
                mqtt_state,
            )
            self.hass.async_create_task(self.async_background_refresh_recent_sessions())

        return changed

    async def async_background_refresh_recent_sessions(self, *_) -> None:
        """Fetch recent sessions from the cloud API in the background."""
        _LOGGER.critical("Refreshing recent sessions from Smappee API...")
        try:
            recent_sessions = await self.station_client.get_recent_sessions()
            if self.data:
                self.data.recent_sessions = recent_sessions
                # Update de entiteiten in Home Assistant
                self.async_set_updated_data(self.data)
                _LOGGER.debug("Recent sessions successfully refreshed.")
        except Exception as sess_err:
            _LOGGER.warning("Could not refresh recent sessions in background: %s", sess_err)

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
        energy_idxs: list[int],
        power_key_prefix: str,  # "grid" or "pv"
    ) -> bool:
        changed = False
        active = payload.get("activePowerData") or []
        currents_ma = payload.get("currentData") or []
        voltage_dv = payload.get("phaseVoltageData") or []
        imp_wh = payload.get("importActiveEnergyData") or []
        exp_wh = payload.get("exportActiveEnergyData") or []

        p_ph = _pick(active, power_idxs)
        if p_ph:
            changed |= self._set_if_changed(st, f"{power_key_prefix}_power_phases", p_ph)
            changed |= self._set_if_changed(st, f"{power_key_prefix}_power_total", sum(p_ph))
            i_ph = _amps_from_ma(_pick(currents_ma, power_idxs))
            if i_ph:
                changed |= self._set_if_changed(st, f"{power_key_prefix}_current_phases", i_ph)

        if power_key_prefix == "grid":
            # Voltage is always in the first 3 entries of phaseVoltageData
            # (L1, L2, L3 of the grid connection), independent of powerTopicIndex.
            v_ph = _volts_from_dv(_pick(voltage_dv, [0, 1, 2]))
            if v_ph:
                changed |= self._set_if_changed(st, "grid_voltage_phases", v_ph)

        if energy_idxs:
            if power_key_prefix == "grid":
                changed |= self._set_if_changed(
                    st, "grid_energy_import_kwh", round(sum(_pick(imp_wh, energy_idxs)) / 1000.0, 3)
                )
                changed |= self._set_if_changed(
                    st, "grid_energy_export_kwh", round(sum(_pick(exp_wh, energy_idxs)) / 1000.0, 3)
                )
            else:  # pv
                changed |= self._set_if_changed(
                    st, "pv_energy_import_kwh", round(sum(_pick(imp_wh, energy_idxs)) / 1000.0, 3)
                )
        return changed

    def _apply_connector_values(
        self,
        conn,  # ConnectorState
        payload: dict,
        power_idxs: list[int],
        energy_idxs: list[int],
    ) -> bool:
        changed = False
        active = payload.get("activePowerData") or []
        currents_ma = payload.get("currentData") or []
        imp_wh = payload.get("importActiveEnergyData") or []

        p_ph = _pick(active, power_idxs)
        i_ma = _pick(currents_ma, power_idxs)
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
            st, payload, grid.get("power", []), grid.get("energy", []), "grid"
        )
        changed |= self._apply_station_group(
            st, payload, pv.get("power", []), pv.get("energy", []), "pv"
        )

        # Connectors
        for uuid, m in cars.items():
            conn = data.connectors.get(uuid)
            if not conn:
                continue
            changed |= self._apply_connector_values(
                conn, payload, m.get("power", []), m.get("energy", [])
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
