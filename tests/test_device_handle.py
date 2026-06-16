"""Unit tests for `device_handle.SmappeeDeviceHandle` dashboard-only behavior."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.smappee_ev.device_handle import SmappeeDeviceHandle


class RecordingDashboard:
    """Dashboard test double that records public API calls."""

    def __init__(self, *, success: bool = True) -> None:
        self.success = success
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.smart_devices: list[dict[str, Any]] = []
        self.recent_sessions: list[dict[str, Any]] = []

    async def _record(self, name: str, *args: Any) -> bool:
        self.calls.append((name, args))
        return self.success

    async def async_set_charging_mode(
        self, service_location_id: str, device_id: str, mode: str
    ) -> bool:
        return await self._record("async_set_charging_mode", service_location_id, device_id, mode)

    async def async_start_charging(
        self, service_location_id: str, device_id: str, percentage: int
    ) -> bool:
        return await self._record(
            "async_start_charging", service_location_id, device_id, percentage
        )

    async def async_pause_charging(self, service_location_id: str, device_id: str) -> bool:
        return await self._record("async_pause_charging", service_location_id, device_id)

    async def async_stop_charging(self, service_location_id: str, device_id: str) -> bool:
        return await self._record("async_stop_charging", service_location_id, device_id)

    async def async_set_led_brightness(
        self, service_location_id: str, device_id: str, brightness: int
    ) -> bool:
        return await self._record(
            "async_set_led_brightness", service_location_id, device_id, brightness
        )

    async def async_set_min_surpluspct(
        self, service_location_id: str, device_id: str, min_surpluspct: int
    ) -> bool:
        return await self._record(
            "async_set_min_surpluspct", service_location_id, device_id, min_surpluspct
        )

    async def async_set_connector_max_current(
        self, service_location_id: str, device_id: str, max_current_a: int
    ) -> bool:
        return await self._record(
            "async_set_connector_max_current", service_location_id, device_id, max_current_a
        )

    async def async_set_percentage_limit(
        self, service_location_id: str, device_id: str, percentage: int
    ) -> bool:
        return await self._record(
            "async_set_percentage_limit", service_location_id, device_id, percentage
        )

    async def async_set_charger_availability(self, serial: str, available: bool) -> bool:
        return await self._record("async_set_charger_availability", serial, available)

    async def async_restart_charging_station(self, serial: str) -> bool:
        return await self._record("async_restart_charging_station", serial)

    async def async_set_offline_charging(
        self, serial: str, enabled: bool, failsafe_amps: int
    ) -> bool:
        return await self._record("async_set_offline_charging", serial, enabled, failsafe_amps)

    async def async_get_smart_devices(self, service_location_id: str) -> list[dict[str, Any]]:
        self.calls.append(("async_get_smart_devices", (service_location_id,)))
        return self.smart_devices

    async def async_get_recent_sessions(self, serial: str) -> list[dict[str, Any]]:
        self.calls.append(("async_get_recent_sessions", (serial,)))
        return self.recent_sessions


def make_client(
    *,
    serial: str = "SERIAL",
    station_serial: str | None = None,
    uuid: str = "DEVUUID",
    dev_id: str = "1",
    loc: str = "100",
    connector: int | None = 1,
    is_station: bool = False,
    dashboard: RecordingDashboard | None = None,
    dashboard_device_id: str | None = "DASHBOARD_DEVICE",
) -> SmappeeDeviceHandle:
    client = SmappeeDeviceHandle(
        serial=serial,
        smart_device_uuid=uuid,
        smart_device_id=dev_id,
        service_location_id=loc,
        connector_number=connector,
        is_station=is_station,
        charging_station_serial=station_serial,
    )
    client.dashboard_client = dashboard
    client.dashboard_device_id = dashboard_device_id if dashboard else None
    return client


@pytest.mark.asyncio
async def test_recent_sessions_use_dashboard_station_serial():
    dashboard = RecordingDashboard()
    dashboard.recent_sessions = [{"energy": 7.2}]
    client = make_client(
        serial="CONNECTSERIAL",
        station_serial="STATIONSERIAL",
        dashboard=dashboard,
    )

    sessions = await client.async_get_recent_sessions()

    assert sessions == [{"energy": 7.2}]
    assert dashboard.calls == [("async_get_recent_sessions", ("STATIONSERIAL",))]


@pytest.mark.asyncio
async def test_recent_sessions_return_empty_without_dashboard():
    client = make_client(station_serial="STATIONSERIAL")

    assert await client.async_get_recent_sessions() == []


@pytest.mark.asyncio
async def test_smartdevices_use_dashboard_and_single_lookup():
    dashboard = RecordingDashboard()
    dashboard.smart_devices = [
        {"id": "OTHER", "uuid": "OTHER_UUID"},
        {"id": "DEVICE_ID", "uuid": "DEVICE_UUID"},
    ]
    client = make_client(loc="500", dashboard=dashboard)

    devices = await client.async_get_smartdevices()
    device = await client.async_get_smartdevice("DEVICE_UUID")

    assert devices == dashboard.smart_devices
    assert device == {"id": "DEVICE_ID", "uuid": "DEVICE_UUID"}
    assert dashboard.calls == [
        ("async_get_smart_devices", ("500",)),
        ("async_get_smart_devices", ("500",)),
    ]


@pytest.mark.asyncio
async def test_smartdevices_return_none_without_dashboard():
    client = make_client()

    assert await client.async_get_smartdevices() is None
    assert await client.async_get_smartdevice("DEVICE_ID") is None


@pytest.mark.asyncio
async def test_set_charging_mode_dashboard_only():
    dashboard = RecordingDashboard()
    client = make_client(loc="236259", dashboard=dashboard)

    assert await client.set_charging_mode("SMART") is True
    assert await client.set_charging_mode("STANDARD") is True
    assert await client.set_charging_mode("SOLAR") is True
    assert await client.set_charging_mode("NORMAL") is False

    assert dashboard.calls == [
        ("async_set_charging_mode", ("236259", "DASHBOARD_DEVICE", "SMART")),
        ("async_set_charging_mode", ("236259", "DASHBOARD_DEVICE", "STANDARD")),
        ("async_set_charging_mode", ("236259", "DASHBOARD_DEVICE", "SOLAR")),
    ]


@pytest.mark.asyncio
async def test_set_charging_mode_requires_dashboard():
    client = make_client()

    with pytest.raises(RuntimeError, match="Dashboard API is not configured"):
        await client.set_charging_mode("SMART")


@pytest.mark.asyncio
async def test_dashboard_action_requires_device_id():
    client = make_client(dashboard=RecordingDashboard(), dashboard_device_id=None)

    with pytest.raises(RuntimeError, match="Dashboard device id not available"):
        await client.set_charging_mode("SMART")


@pytest.mark.asyncio
async def test_dashboard_action_false_result_raises():
    client = make_client(dashboard=RecordingDashboard(success=False))

    with pytest.raises(RuntimeError, match="returned no success"):
        await client.set_charging_mode("SMART")


@pytest.mark.asyncio
async def test_start_charging_clamps_and_sends_percentage():
    dashboard = RecordingDashboard()
    client = make_client(dashboard=dashboard)

    target, pct = await client.start_charging(40, min_current=6, max_current=32)

    assert target == 32
    assert pct == 100
    assert dashboard.calls == [("async_start_charging", ("100", "DASHBOARD_DEVICE", 100))]


@pytest.mark.asyncio
async def test_start_charging_reversed_and_degenerate_ranges():
    dashboard = RecordingDashboard()
    client = make_client(dashboard=dashboard)

    target, pct = await client.start_charging(20, min_current=32, max_current=6)
    fixed_target, fixed_pct = await client.start_charging(15, min_current=16, max_current=16)

    assert (target, pct) == (20, 54)
    assert (fixed_target, fixed_pct) == (16, 100)
    assert dashboard.calls[-2:] == [
        ("async_start_charging", ("100", "DASHBOARD_DEVICE", 54)),
        ("async_start_charging", ("100", "DASHBOARD_DEVICE", 100)),
    ]


@pytest.mark.asyncio
async def test_set_percentage_limit_and_current_use_dashboard():
    dashboard = RecordingDashboard()
    client = make_client(dashboard=dashboard)

    current, pct = await client.set_percentage_limit(50, min_current=6, max_current=32)
    current_from_amps, pct_from_amps = await client.set_current(16.5, min_current=6, max_current=32)

    assert (current, pct) == (19, 50)
    assert (current_from_amps, pct_from_amps) == (16.4, 40)
    assert dashboard.calls == [
        ("async_set_percentage_limit", ("100", "DASHBOARD_DEVICE", 50)),
        ("async_set_percentage_limit", ("100", "DASHBOARD_DEVICE", 40)),
    ]


@pytest.mark.asyncio
async def test_set_percentage_limit_bounds_and_degenerate_range():
    dashboard = RecordingDashboard()
    client = make_client(dashboard=dashboard)

    assert await client.set_percentage_limit(-10, min_current=6, max_current=32) == (6, 0)
    assert await client.set_percentage_limit(120, min_current=6, max_current=32) == (32, 100)
    assert await client.set_percentage_limit(80, min_current=16, max_current=10) == (16, 80)


@pytest.mark.asyncio
async def test_pause_stop_brightness_and_surplus_use_dashboard():
    dashboard = RecordingDashboard()
    client = make_client(dashboard=dashboard)

    await client.pause_charging()
    await client.stop_charging()
    await client.set_brightness(42)
    await client.set_min_surpluspct(17)
    await client.set_connector_max_current(16)

    assert dashboard.calls == [
        ("async_pause_charging", ("100", "DASHBOARD_DEVICE")),
        ("async_stop_charging", ("100", "DASHBOARD_DEVICE")),
        ("async_set_led_brightness", ("100", "DASHBOARD_DEVICE", 42)),
        ("async_set_min_surpluspct", ("100", "DASHBOARD_DEVICE", 17)),
        ("async_set_connector_max_current", ("100", "DASHBOARD_DEVICE", 16)),
    ]


@pytest.mark.asyncio
async def test_availability_uses_dashboard_v11_station_serial():
    dashboard = RecordingDashboard()
    client = make_client(
        serial="CONNECTSERIAL",
        station_serial="STATIONSERIAL",
        dashboard=dashboard,
    )

    await client.set_available()
    await client.set_unavailable()

    assert dashboard.calls == [
        ("async_set_charger_availability", ("STATIONSERIAL", True)),
        ("async_set_charger_availability", ("STATIONSERIAL", False)),
    ]


@pytest.mark.asyncio
async def test_availability_requires_dashboard():
    client = make_client()

    with pytest.raises(RuntimeError, match="charger availability"):
        await client.set_available()


@pytest.mark.asyncio
async def test_restart_charging_station_uses_dashboard_v11_station_serial():
    dashboard = RecordingDashboard()
    client = make_client(
        serial="CONNECTSERIAL",
        station_serial="STATIONSERIAL",
        dashboard=dashboard,
    )

    await client.restart_charging_station()

    assert dashboard.calls == [
        ("async_restart_charging_station", ("STATIONSERIAL",)),
    ]


@pytest.mark.asyncio
async def test_restart_charging_station_requires_dashboard():
    client = make_client()

    with pytest.raises(RuntimeError, match="charging station restart"):
        await client.restart_charging_station()


@pytest.mark.asyncio
async def test_offline_charging_uses_dashboard_v11_station_serial():
    dashboard = RecordingDashboard()
    client = make_client(
        serial="CONNECTSERIAL",
        station_serial="STATIONSERIAL",
        dashboard=dashboard,
    )

    await client.set_offline_charging_config(True, 6)

    assert dashboard.calls == [
        ("async_set_offline_charging", ("STATIONSERIAL", True, 6)),
    ]


@pytest.mark.asyncio
async def test_offline_charging_requires_dashboard():
    client = make_client()

    with pytest.raises(RuntimeError, match="offline charging"):
        await client.set_offline_charging_config(True, 6)
