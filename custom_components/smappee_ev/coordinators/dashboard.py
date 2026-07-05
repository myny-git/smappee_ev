"""Dashboard refresh and merge helpers for Smappee station coordinators."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
from inspect import iscoroutinefunction
import logging
from time import time as _now
from typing import Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later

from ..const import DASHBOARD_REFRESH_AFTER_WRITE_DELAY, DASHBOARD_REFRESH_INTERVAL
from ..helpers import anonymize_uuid
from ..models.state import (
    ConnectorState,
    DashboardObject,
    IntegrationData,
    MqttPayload,
    StationState,
)

_LOGGER = logging.getLogger(__name__)


class DashboardMixin:
    """Dashboard refresh, scheduling, and payload merge helpers."""

    async def _maybe_refresh_dashboard_data(
        self, data: IntegrationData, force: bool = False
    ) -> bool:
        """Refresh slow Dashboard REST config/cache data when due."""
        if self._is_stopping:
            return False
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
            if self._is_stopping:
                return False

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
                errors.append(f"load management {anonymize_uuid(uuid)}: {result}")
                continue
            if isinstance(result, dict):
                changed |= self._merge_dashboard_load_management(data.connectors[uuid], result)

        if errors:
            self._log_dashboard_refresh_errors(errors)
        return changed

    def _merge_dashboard_load_management(self, conn: ConnectorState, payload: MqttPayload) -> bool:
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
        if self._is_stopping:
            return
        self._cancel_dashboard_refresh_timer()

        if delay <= 0:
            self._start_dashboard_refresh_task()
            return

        async def _refresh(_now: datetime) -> None:
            self._dashboard_refresh_unsub = None
            if self._is_stopping:
                return
            self._start_dashboard_refresh_task()

        self._dashboard_refresh_unsub = async_call_later(self.hass, delay, _refresh)

    def _start_dashboard_refresh_task(self) -> None:
        """Start the dashboard refresh task if shutdown has not begun."""
        if self._is_stopping:
            return
        task = self._dashboard_refresh_task
        if task is not None and not task.done():
            task.cancel()
        task = self.hass.async_create_task(self._async_dashboard_refresh_now())
        self._dashboard_refresh_task = task
        task.add_done_callback(self._log_background_task_exception)

    async def _async_dashboard_refresh_now(self) -> None:
        try:
            if self._is_stopping:
                return
            data = self.data
            if data and await self._maybe_refresh_dashboard_data(data, force=True):
                self.async_set_updated_data(data)
        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed:
            if not self._is_stopping:
                self._start_background_reauth()
        finally:
            if self._dashboard_refresh_task is asyncio.current_task():
                self._dashboard_refresh_task = None

    async def _async_delayed_dashboard_refresh(self, delay: float) -> None:
        """Compatibility wrapper for older tests and callers."""
        if delay > 0:
            self.async_schedule_dashboard_refresh(delay=delay)
            return
        await self._async_dashboard_refresh_now()

    def _cancel_dashboard_refresh_timer(self) -> None:
        """Cancel a scheduled dashboard refresh timer."""
        if self._dashboard_refresh_unsub is None:
            return

        unsub = self._dashboard_refresh_unsub
        self._dashboard_refresh_unsub = None
        with suppress(RuntimeError):
            unsub()

    def _merge_dashboard_station_details(
        self, data: IntegrationData, details: DashboardObject
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

    def _merge_dashboard_module(self, data: IntegrationData, module: DashboardObject) -> bool:
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

    def _merge_dashboard_led(self, station: StationState, smart_device: DashboardObject) -> bool:
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
        module: DashboardObject,
        smart_device: DashboardObject,
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
        self, conn: ConnectorState, car_charger: DashboardObject
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
    def _device_uuid_from_dashboard_channel(smart_device: DashboardObject) -> str | None:
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

    def _merge_dashboard_capacity(self, station: StationState, payload: DashboardObject) -> bool:
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

    def _merge_dashboard_overload(self, station: StationState, payload: DashboardObject) -> bool:
        changed = False
        changed |= self._set_if_changed(
            station, "overload_protection_active", self._as_bool(payload.get("active"))
        )
        changed |= self._set_if_changed(
            station, "overload_maximum_load_a", self._as_int(payload.get("maximumLoad"))
        )
        return changed
