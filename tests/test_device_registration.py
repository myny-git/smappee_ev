"""Device registry: registering runtime devices and removing stale ones."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smappee_ev import async_remove_config_entry_device
from custom_components.smappee_ev.const import DOMAIN
from custom_components.smappee_ev.helpers import (
    make_connector_device_info,
    make_device_info,
    make_station_device_info,
)
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.runtime_devices import (
    _register_runtime_devices,
    _remove_legacy_led_controller_devices,
)
from tests.factories import (
    make_connector_runtime,
    make_led_runtime,
    make_site_runtime,
    make_station_runtime,
)


def _stale_device_runtime(entry, *, connector_uuid="conn-uuid"):
    connector_client = MagicMock()
    connector_client.connector_number = 2
    runtime = RuntimeData(
        api=MagicMock(),
        mqtt={},
        sites={
            100: make_site_runtime(
                site_location_id=100,
                stations={
                    "station-uuid": make_station_runtime(
                        site_location_id=100,
                        control_location_id=200,
                        station_uuid="station-uuid",
                        serial="STATION-1",
                        station_client=MagicMock(serial_id="STATION-1"),
                        connectors={
                            connector_uuid: make_connector_runtime(
                                connector_key=connector_uuid,
                                connector_uuid=connector_uuid,
                                connector_position=2,
                                connector_client=connector_client,
                            )
                        },
                    )
                },
            )
        },
    )
    runtime.status_coordinator = MagicMock(
        unique_id_prefix=f"{entry.entry_id}:smappee_service_status"
    )
    entry.runtime_data = runtime
    return runtime


async def test_register_runtime_devices_creates_site_station_and_connector_devices(hass):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    station_client = MagicMock()
    station_client.serial_id = "LEGACY-SERIAL"
    connector_client = MagicMock()
    connector_client.connector_number = 2
    entry.runtime_data = RuntimeData(
        api=MagicMock(),
        mqtt={},
        sites={
            100: make_site_runtime(
                site_location_id=100,
                site_name="Home",
                gateway_serial="GATEWAY-1",
                gateway_type="Genius",
                stations={
                    "station-uuid": make_station_runtime(
                        site_location_id=100,
                        control_location_id=200,
                        station_uuid="station-uuid",
                        serial="STATION-1",
                        station_client=station_client,
                        station_name="Garage Charger",
                        station_model="EV Wall",
                        led_devices={"led-id": make_led_runtime(led_key="led-id")},
                        connectors={
                            "conn-uuid": make_connector_runtime(
                                connector_key="conn-uuid",
                                connector_uuid="conn-uuid",
                                connector_position=2,
                                connector_client=connector_client,
                            )
                        },
                    )
                },
            )
        },
    )
    registry = dr.async_get(hass)
    legacy_led = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "led:100:200:STATION-1:led-id")},
    )
    _register_runtime_devices(hass, entry)
    assert registry.async_get(legacy_led.id) is None

    site = registry.async_get_device_by_identifier((DOMAIN, "site:100"), entry.entry_id)
    station = registry.async_get_device_by_identifier(
        (DOMAIN, "station:100:200:STATION-1"), entry.entry_id
    )
    connector = registry.async_get_device_by_identifier(
        (DOMAIN, "connector:100:200:STATION-1:conn-uuid"), entry.entry_id
    )
    assert site is not None
    assert station is not None
    assert connector is not None
    assert (DOMAIN, "100:LEGACY-SERIAL:station-uuid") in station.identifiers
    assert station.via_device_id == site.id
    assert connector.via_device_id == station.id

    # Entity metadata and a subsequent setup must preserve links and device IDs.
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **make_station_device_info(100, 200, "STATION-1"),
    )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **make_connector_device_info(100, 200, "STATION-1", "conn-uuid"),
    )
    assert registry.async_get(station.id).via_device_id == site.id
    assert registry.async_get(connector.id).via_device_id == station.id
    _register_runtime_devices(hass, entry)
    assert {
        device.id for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
    } == {site.id, station.id, connector.id}
    assert registry.async_get(station.id).via_device_id == site.id
    assert registry.async_get(connector.id).via_device_id == station.id


@pytest.mark.parametrize(
    ("site_name", "gateway_serial", "station_name"),
    [("Home", "GATEWAY-1", "Garage Charger"), (None, None, None)],
)
async def test_registration_and_entities_write_the_same_device_metadata(
    hass, site_name, gateway_serial, station_name
):
    """Setup registration and entity device_info must not rename devices in turn."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    connector_client = MagicMock()
    connector_client.connector_number = 1
    entry.runtime_data = RuntimeData(
        api=MagicMock(),
        mqtt={},
        sites={
            100: make_site_runtime(
                site_location_id=100,
                site_name=site_name,
                gateway_serial=gateway_serial,
                gateway_type="Genius",
                stations={
                    "station-uuid": make_station_runtime(
                        site_location_id=100,
                        control_location_id=200,
                        station_uuid="station-uuid",
                        serial="STATION-1",
                        station_client=MagicMock(serial_id="STATION-1"),
                        station_name=station_name,
                        station_model="EV Wall",
                        connectors={
                            "conn-uuid": make_connector_runtime(
                                connector_key="conn-uuid",
                                connector_uuid="conn-uuid",
                                connector_position=1,
                                connector_client=connector_client,
                            )
                        },
                    )
                },
            )
        },
    )
    registry = dr.async_get(hass)
    _register_runtime_devices(hass, entry)

    def snapshot() -> dict[str, tuple[object, ...]]:
        return {
            identifier: (device.name, device.model, device.serial_number)
            for identifier in (
                "site:100",
                "station:100:200:STATION-1",
                "connector:100:200:STATION-1:conn-uuid",
            )
            if (
                device := registry.async_get_device_by_identifier(
                    (DOMAIN, identifier), entry.entry_id
                )
            )
        }

    registered = snapshot()
    assert len(registered) == 3

    # The metadata entity device_info supplies when the platforms load.
    for info in (
        make_device_info(
            100,
            "unknown",
            "site-100",
            scope="site",
            site_name=site_name,
            gateway_serial=gateway_serial,
            gateway_type="Genius",
        ),
        make_station_device_info(
            100, 200, "STATION-1", station_name=station_name, station_model="EV Wall"
        ),
        make_connector_device_info(
            100, 200, "STATION-1", "conn-uuid", "1", station_name=station_name
        ),
    ):
        registry.async_get_or_create(config_entry_id=entry.entry_id, **info)

    assert snapshot() == registered
    assert registered["site:100"][0] == (site_name or "Smappee 100")
    expected_station = station_name or "Smappee EV STATION-1"
    assert registered["connector:100:200:STATION-1:conn-uuid"][0] == (
        f"{expected_station} | Connector 1"
    )


def test_remove_legacy_led_devices_removes_only_led_devices():
    registry = MagicMock(spec=dr.DeviceRegistry)
    entry = SimpleNamespace(entry_id="smappee-entry")
    led = SimpleNamespace(id="old-led", identifiers={(DOMAIN, "led:100:200:STATION-1:led-id")})
    station = SimpleNamespace(id="station", identifiers={(DOMAIN, "station:100:200:STATION-1")})
    with patch(
        "custom_components.smappee_ev.runtime_devices.dr.async_entries_for_config_entry",
        return_value=[led, station],
    ):
        _remove_legacy_led_controller_devices(registry, entry)

    registry.async_remove_device.assert_called_once_with("old-led")


@pytest.mark.asyncio
async def test_async_remove_config_entry_device_allows_only_stale_station_devices(hass):
    entry = MagicMock()
    station_client = MagicMock()
    station_client.serial_id = "LEGACY-1"
    entry.runtime_data = RuntimeData(
        api=MagicMock(),
        mqtt={},
        sites={
            10: make_site_runtime(
                site_location_id=10,
                stations={
                    "station-uuid": make_station_runtime(
                        site_location_id=10,
                        control_location_id=20,
                        station_uuid="station-uuid",
                        serial="",
                        station_client=station_client,
                    )
                },
            )
        },
    )

    foreign_device = MagicMock(identifiers={("other", "station:10:20:LEGACY-1")})
    current_device = MagicMock(identifiers={(DOMAIN, "station:10:20:LEGACY-1")})
    stale_device = MagicMock(identifiers={(DOMAIN, "station:10:20:OLD")})

    assert await async_remove_config_entry_device(hass, entry, foreign_device) is True
    assert await async_remove_config_entry_device(hass, entry, current_device) is False
    assert await async_remove_config_entry_device(hass, entry, stale_device) is True


async def test_async_remove_config_entry_device_keeps_every_current_device(hass):
    """Sites, connectors and the status device are as current as stations."""
    entry = MockConfigEntry(domain=DOMAIN)
    _stale_device_runtime(entry, connector_uuid="")

    def allowed(identifier):
        return async_remove_config_entry_device(
            hass, entry, MagicMock(identifiers={(DOMAIN, identifier)})
        )

    assert await allowed("site:100") is False
    assert await allowed("station:100:200:STATION-1") is False
    assert await allowed("connector:100:200:STATION-1:position:2") is False
    assert await allowed(f"{entry.entry_id}:smappee_service_status") is False
    assert await allowed("site:999") is True
    assert await allowed("connector:100:200:STATION-1:position:1") is True

    entry.runtime_data.status_coordinator = None
    entry.runtime_data.sites[100].stations["station-uuid"].charging_station_serial = ""
    assert await allowed("site:100") is False
    assert await allowed(f"{entry.entry_id}:smappee_service_status") is True
