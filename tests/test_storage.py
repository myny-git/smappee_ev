"""Battery discovery, signed measurements and existing-entity regressions (#301)."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
import pytest

from custom_components.smappee_ev import sensor
from custom_components.smappee_ev.api.discovery import parse_mqtt_channel_specs_from_highlevel
from custom_components.smappee_ev.coordinator import SmappeeSiteCoordinator
from custom_components.smappee_ev.coordinators.storage import StorageMeasurements
from custom_components.smappee_ev.models.state import SiteData, SiteState
from custom_components.smappee_ev.mqtt_setup import _build_mqtt_routes
from custom_components.smappee_ev.mqtt_specs import _split_highlevel_configs_by_scope
from tests.factories import make_config_entry, make_runtime_data, make_site_runtime

TOPIC = "servicelocation/test/power"


def channel(*paths, topic=TOPIC):
    return {
        "protocol": "MQTT",
        "name": topic,
        "aspectPaths": [{"path": path, "multiplier": multiplier} for path, multiplier in paths],
    }


def storage_config():
    """Only measurement structure from #301; no personal data or credentials."""
    return {
        "measurements": [
            {
                "type": "STORAGE",
                "updateChannels": {
                    "activePower": channel(("$.channelData[9]", 1)),
                    "meterReadings": channel(
                        ("$.importActiveEnergyData[3]", 1),
                        ("$.exportActiveEnergyData[3]", -1),
                    ),
                },
            }
        ]
    }


def coordinator(hass, config):
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=1,
        site_name="Home",
        site_uuid="site",
        gateway_serial="gateway",
        gateway_type="GENIUS",
        update_interval=30,
        highlevel_configs={1: config},
    )
    coord.async_set_updated_data(SiteData(site=SiteState()))
    return coord


async def entities(hass, coord):
    entry = make_config_entry(
        runtime_data=make_runtime_data(
            sites={1: make_site_runtime(site_location_id=1, site_coordinator=coord)}
        )
    )
    added = MagicMock()
    await sensor.async_setup_entry(hass, entry, added)
    return added.call_args.args[0]


async def test_storage_discovery_routes_only_to_site(hass):
    cfg = storage_config()
    specs = parse_mqtt_channel_specs_from_highlevel(1, cfg)
    assert {spec.role for spec in specs} == {"storage"}
    assert _split_highlevel_configs_by_scope({1: cfg}) == ({1: cfg}, {})
    coord = coordinator(hass, cfg)
    station = MagicMock()
    routes = _build_mqtt_routes(specs, coord, {"station": station})
    assert routes == {TOPIC: [coord]}
    listener = MagicMock()
    remove_listener = coord.async_add_listener(listener)
    coord.apply_mqtt_properties(TOPIC, {"channelData": [0] * 9 + [-318]})
    assert coord.data.site.storage_power_total == -318
    listener.assert_called_once()
    remove_listener()


@pytest.mark.parametrize("multiplier", [None, "invalid", True, 0, float("nan"), float("inf")])
def test_invalid_multiplier_does_not_create_a_metric(multiplier):
    cfg = {
        "measurements": [
            {
                "type": "STORAGE",
                "updateChannels": {"activePower": channel(("$.channelData[9]", multiplier))},
            }
        ]
    }
    assert not StorageMeasurements({1: cfg}).metrics


@pytest.mark.parametrize("path", ["$.channelData[-1]", "$.channelData", "$.otherData[0]", ""])
def test_invalid_or_unsupported_power_paths_do_not_create_a_metric(path):
    cfg = {
        "measurements": [{"type": "STORAGE", "updateChannels": {"activePower": channel((path, 1))}}]
    }
    assert not StorageMeasurements({1: cfg}).metrics


def test_actuals_and_parent_paths_are_not_counted_twice():
    cfg = storage_config()
    measurement = cfg["measurements"][0]
    measurement["actuals"] = [{"updateChannels": deepcopy(measurement["updateChannels"])}]
    storage = StorageMeasurements({1: cfg})
    site = SiteState()
    storage.apply(site, TOPIC, {"channelData": [0] * 9 + [-318]})
    assert site.storage_power_total == -318


@pytest.mark.parametrize(
    ("power", "import_wh", "export_wh"), [(-318, 7767, 14582), (30, 7890, 16317), (0, 0, 0)]
)
async def test_issue_301_samples_and_sensor_metadata(hass, power, import_wh, export_wh):
    coord = coordinator(hass, storage_config())
    await coord._ensure_power_index_map()
    assert coord._handle_power(
        TOPIC,
        {
            "channelData": [0] * 9 + [power],
            "activePowerData": [999] * 4,  # Must use the configured array.
            "importActiveEnergyData": [0, 0, 0, import_wh],
            "exportActiveEnergyData": [0, 0, 0, export_wh],
        },
    )
    battery = [
        entity
        for entity in await entities(hass, coord)
        if isinstance(entity, sensor.SiteBatteryPower | sensor.SiteBatteryEnergy)
    ]
    assert len(battery) == 3
    by_key = {entity.translation_key: entity for entity in battery}
    assert by_key["battery_power"].native_value == power
    assert by_key["battery_power"].device_class == SensorDeviceClass.POWER
    assert by_key["battery_power"].state_class == SensorStateClass.MEASUREMENT
    assert by_key["battery_power"].native_unit_of_measurement == "W"
    assert by_key["battery_charged_energy"].native_value == export_wh / 1000
    assert by_key["battery_discharged_energy"].native_value == import_wh / 1000
    for entity in battery[1:]:
        assert entity.state_class == SensorStateClass.TOTAL_INCREASING
        assert entity.device_class == SensorDeviceClass.ENERGY
        assert entity.native_unit_of_measurement == "kWh"
        assert entity.device_info == battery[0].device_info
    assert len({entity.unique_id for entity in battery}) == 3


@pytest.mark.parametrize("bad", [None, "invalid", True, float("nan"), float("inf")])
def test_invalid_measurement_preserves_previous_value(bad):
    storage = StorageMeasurements({1: storage_config()})
    site = SiteState()
    storage.apply(site, TOPIC, {"channelData": [0] * 9 + [-318]})
    assert not storage.apply(site, TOPIC, {"channelData": [0] * 9 + [bad]})
    assert site.storage_power_total == -318
    assert site.storage_charged_energy_kwh is None


def test_separate_topics_multiphase_deduplication_and_missing_groups():
    cfg = storage_config()
    cfg["measurements"][0]["updateChannels"] = {
        "activePower": channel(("$.activePowerData[1]", 1), ("$.activePowerData[3]", -1)),
        "meterReadings": channel(
            ("$.exportActiveEnergyData[2]", -1), ("$.exportActiveEnergyData[4]", -1), topic="energy"
        ),
    }
    storage = StorageMeasurements({1: cfg, 2: deepcopy(cfg)})
    site = SiteState()
    assert storage.apply(site, TOPIC, {"activePowerData": [900, -100, 900, 200]})
    assert site.storage_power_total == -300
    assert not storage.apply(site, TOPIC, {"activePowerData": [900, -100]})
    assert not storage.apply(site, "wrong-topic", {"activePowerData": [0] * 4})
    assert not storage.apply(site, TOPIC, {})
    assert site.storage_power_total == -300
    assert storage.apply(site, "energy", {"exportActiveEnergyData": [0, 0, 1000, 0, 2000]})
    assert site.storage_charged_energy_kwh == 3
    assert not storage.apply(site, "energy", {"exportActiveEnergyData": [0, 0, 0]})
    assert site.storage_charged_energy_kwh == 3
    assert storage.apply(site, TOPIC, {"activePowerData": [0] * 4})
    assert site.storage_power_total == 0


def test_multiple_batteries_on_separate_topics_wait_for_complete_total():
    cfg = storage_config()
    cfg["measurements"].append(
        {
            "type": "STORAGE",
            "updateChannels": {"activePower": channel(("$.activePowerData[0]", 1), topic="second")},
        }
    )
    storage = StorageMeasurements({1: cfg})
    site = SiteState()
    assert not storage.apply(site, TOPIC, {"channelData": [0] * 9 + [-318]})
    assert site.storage_power_total is None
    assert storage.apply(site, "second", {"activePowerData": [30]})
    assert site.storage_power_total == -288
    assert storage.apply(site, "second", {"activePowerData": [0]})
    assert site.storage_power_total == -318


def test_energy_paths_have_independent_indexes_and_follow_configured_direction():
    cfg = storage_config()
    cfg["measurements"][0]["updateChannels"]["meterReadings"] = channel(
        ("$.importActiveEnergyData[1]", -1), ("$.exportActiveEnergyData[4]", 1)
    )
    storage = StorageMeasurements({1: cfg})
    site = SiteState()
    storage.apply(
        site,
        TOPIC,
        {
            "importActiveEnergyData": [0, 3000],
            "exportActiveEnergyData": [0, 0, 0, 0, 5000],
        },
    )
    assert site.storage_charged_energy_kwh == 3
    assert site.storage_discharged_energy_kwh == 5
    assert not storage.apply(site, TOPIC, {"importActiveEnergyData": [0, -100]})
    assert site.storage_charged_energy_kwh == 3


@pytest.mark.parametrize("power_only", [True, False])
async def test_only_supported_battery_entities_created(hass, power_only):
    cfg = storage_config() if power_only else {"measurements": []}
    if power_only:
        del cfg["measurements"][0]["updateChannels"]["meterReadings"]
    result = await entities(hass, coordinator(hass, cfg))
    assert sum(isinstance(entity, sensor.SiteBatteryPower) for entity in result) == int(power_only)
    assert not any(isinstance(entity, sensor.SiteBatteryEnergy) for entity in result)


async def test_existing_site_entities_and_values_are_unchanged(hass):
    cfg = {
        "measurements": [
            {
                "type": "GRID",
                "updateChannels": {
                    "activePower": channel(("$.activePowerData[1]", 1)),
                    "meterReadings": channel(("$.importActiveEnergyData[1]", 1)),
                },
            },
            {
                "type": "PRODUCTION",
                "updateChannels": {
                    "activePower": channel(("$.activePowerData[2]", 1)),
                    "meterReadings": channel(("$.importActiveEnergyData[2]", 1)),
                },
            },
        ]
    }
    baseline = coordinator(hass, cfg)
    with_storage = deepcopy(cfg)
    with_storage["measurements"] += storage_config()["measurements"]
    expanded = coordinator(hass, with_storage)
    payload = {
        "activePowerData": [3311, 30, 3700, -318],
        "channelData": [0] * 9 + [-318],
        "importActiveEnergyData": [7550790, 8005109, 17249444, 7767],
        "exportActiveEnergyData": [3167, 10197580, 39258, 14582],
        "consumptionPower": 71,
        "alwaysOn": 42,
    }
    for coord in (baseline, expanded):
        await coord._ensure_power_index_map()
        coord._handle_power(TOPIC, payload)
    assert baseline._power_index_maps_by_topic == expanded._power_index_maps_by_topic
    before = asdict(baseline.data.site)
    after = asdict(expanded.data.site)
    for key, value in before.items():
        if not key.startswith("storage_"):
            assert after[key] == value
    old_entities = await entities(hass, baseline)
    new_entities = await entities(hass, expanded)
    existing = {entity.unique_id: entity for entity in old_entities}
    assert len(new_entities) == len(old_entities) + 3
    for entity in new_entities:
        if entity.unique_id not in existing:
            continue
        old = existing.pop(entity.unique_id)
        assert type(entity) is type(old)
        for attr in (
            "native_value",
            "device_info",
            "translation_key",
            "device_class",
            "state_class",
            "native_unit_of_measurement",
        ):
            assert getattr(entity, attr) == getattr(old, attr)
    assert not existing


async def test_battery_power_freshness_and_energy_restore(hass, monkeypatch):
    coord = coordinator(hass, storage_config())
    coord.mqtt_transport_connected = True
    coord.last_real_power_rx = datetime.now(UTC)
    power = sensor.SiteBatteryPower(coord, 1)
    assert power.available
    coord.last_real_power_rx -= timedelta(hours=1)
    assert not power.available
    coord.mqtt_transport_connected = False
    assert not power.available
    energy = sensor.SiteBatteryEnergy(coord, 1, charging=True)
    monkeypatch.setattr(sensor.SmappeeSiteEntity, "async_added_to_hass", AsyncMock())
    energy.async_get_last_sensor_data = AsyncMock(return_value=MagicMock(native_value=14.582))
    await energy.async_added_to_hass()
    assert energy.native_value == 14.582
    coord.data.site.storage_charged_energy_kwh = 14.0
    assert energy.native_value == 14.582
    coord.data.site.storage_charged_energy_kwh = 16.317
    assert energy.native_value == 16.317
