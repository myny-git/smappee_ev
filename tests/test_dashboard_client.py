from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev.dashboard_client import SmappeeDashboardClient


@pytest.mark.asyncio
async def test_set_capacity_protection_uses_one_decimal_kw_payload():
    """Capacity protection writes use one decimal kW."""
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock()

    await client.async_set_capacity_protection(236259, True, 5.06)

    client._request.assert_awaited_once_with(
        "PUT",
        "v10/servicelocation/236259/homecontrol/smart/capacityprotection",
        json={
            "locationId": 236259,
            "active": True,
            "capacityMaximumPower": 5.1,
            "capacitySuggestedPower": 0,
        },
        expected=(200, 204),
    )


@pytest.mark.asyncio
async def test_set_overload_protection_uses_confirmed_patch_payload():
    """Overload writes use the Dashboard PATCH payload confirmed from manual testing."""
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock()

    await client.async_set_overload_protection(236259, True, 25)

    client._request.assert_awaited_once_with(
        "PATCH",
        "v10/servicelocation/236259/homecontrol/smart/overloadprotection",
        json={"active": True, "maximumLoad": 25},
        expected=(200, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_charging_mode_action_payload():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_charging_mode(
        236259, "CARCHARGER-acchargingcontroller-123", "smart"
    )

    assert ok is True
    client._request.assert_awaited_once_with(
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/CARCHARGER-acchargingcontroller-123/actions/setChargingMode",
        json=[
            {
                "spec": {
                    "name": "mode",
                    "species": "String",
                    "required": True,
                    "possibleValues": {
                        "values": [
                            {"String": "STANDARD"},
                            {"String": "SMART"},
                            {"String": "SOLAR"},
                        ],
                        "exhaustive": True,
                    },
                },
                "values": [{"String": "SMART"}],
            }
        ],
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_solar_mode_does_not_write_min_surplus():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_charging_mode(
        236259, "CARCHARGER-acchargingcontroller-123", "solar"
    )

    assert ok is True
    client._request.assert_awaited_once()
    assert client._request.await_args.args == (
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/CARCHARGER-acchargingcontroller-123/actions/setChargingMode",
    )


@pytest.mark.asyncio
async def test_dashboard_start_charging_action_payload():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_start_charging(236259, "CARCHARGER-acchargingcontroller-123", 40)

    assert ok is True
    client._request.assert_awaited_once_with(
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/CARCHARGER-acchargingcontroller-123/actions/startCharging",
        json=[
            {
                "spec": {
                    "name": "percentageLimit",
                    "species": "Integer",
                    "unit": "%",
                    "required": True,
                },
                "values": [{"Integer": 40}],
            }
        ],
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_led_brightness_configuration_patch_payload():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_led_brightness(236259, "LED-controller-123", 42)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v10/servicelocation/236259/homecontrol/smart/devices/LED-controller-123",
        json={
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.led.config.brightness"},
                    "values": [{"Integer": 42}],
                }
            ]
        },
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_min_surplus_configuration_patch_payload():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_min_surpluspct(236259, "device-1", 84)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1",
        json={
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.min.excesspct"},
                    "values": [{"Integer": 84}],
                }
            ]
        },
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_connector_max_current_configuration_patch_payload():
    """Connector max current writes use the Dashboard smart device config PATCH."""
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_connector_max_current(236259, "device-1", 16)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1",
        json={
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.max.current"},
                    "values": [{"Quantity": {"value": 16, "unit": "A"}}],
                }
            ]
        },
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_pause_and_stop_action_payloads():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    assert await client.async_pause_charging(236259, "device-1")
    assert await client.async_stop_charging(236259, "device-1")

    client._request.assert_any_await(
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1/actions/pauseCharging",
        json=[],
        expected=(200, 201, 204),
    )
    client._request.assert_any_await(
        "POST",
        "v10/servicelocation/236259/homecontrol/smart/devices/device-1/actions/stopCharging",
        json=[],
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_smart_devices_uses_v10_homecontrol_endpoint():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=[{"id": "device-1"}])

    devices = await client.async_get_smart_devices(236259)

    assert devices == [{"id": "device-1"}]
    client._request.assert_awaited_once_with(
        "GET",
        "v10/servicelocation/236259/homecontrol/smart/devices",
        params={"excludedCategories": ""},
        return_json=True,
    )


@pytest.mark.asyncio
async def test_dashboard_recent_sessions_uses_v10_range_mode():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=[{"id": "session-1"}])

    sessions = await client.async_get_recent_sessions("STATIONSERIAL")

    assert sessions == [{"id": "session-1"}]
    method, path = client._request.await_args.args
    kwargs = client._request.await_args.kwargs
    assert method == "GET"
    assert path == "v10/chargingstations/STATIONSERIAL/sessions"
    assert kwargs["params"]["rangeMode"] == "stop_or_start"
    assert "," in kwargs["params"]["range"]
    assert kwargs["return_json"] is True


@pytest.mark.asyncio
async def test_dashboard_charger_availability_uses_v11_patch():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_charger_availability("STATIONSERIAL", False)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v11/chargingstations/STATIONSERIAL",
        json={"available": False},
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_restart_charging_station_uses_v11_post():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_restart_charging_station("6220017988")

    assert ok is True
    client._request.assert_awaited_once_with(
        "POST",
        "v11/chargingstations/6220017988/restart",
        json={},
        expected=(200, 201, 204),
    )


@pytest.mark.asyncio
async def test_dashboard_offline_charging_uses_v11_patch():
    client = SmappeeDashboardClient(
        username=None,
        password=None,
        refresh_token=None,
        session=MagicMock(),
        token_update_callback=MagicMock(),
    )
    client._request = AsyncMock(return_value=True)

    ok = await client.async_set_offline_charging("STATIONSERIAL", True, 6)

    assert ok is True
    client._request.assert_awaited_once_with(
        "PATCH",
        "v11/chargingstations/STATIONSERIAL",
        json={"offlineCharging": {"enabled": True, "failSafe": 6}},
        expected=(200, 201, 204),
    )
