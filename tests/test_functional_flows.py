from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
import pytest

from custom_components.smappee_ev import _setup_mqtt, async_setup_entry, async_unload_entry
from custom_components.smappee_ev.api.discovery import MqttChannelSpec
from custom_components.smappee_ev.const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.smappee_ev.models.runtime_data import RuntimeData
from custom_components.smappee_ev.models.state import ConnectorState, IntegrationData, SiteData
from custom_components.smappee_ev.services import register_services
from tests.factories import (
    configure_loaded_entries,
    make_connector_client,
    make_loaded_config_entry,
    make_runtime_for_connector,
)


class _FakeDashboard:
    username = "user"
    password = "pass"  # noqa: S105 - fake test password
    refresh_token = "refresh"  # noqa: S105 - fake test token
    _token = "token"  # noqa: S105 - fake test token

    def __init__(self):
        self.async_get_service_locations_full_details = AsyncMock(
            return_value=[
                {
                    "id": 317418,
                    "serviceLocationUuid": "site-uuid",
                    "name": "Home",
                    "functionType": "ELECTRICITY",
                    "gateway": {"serialNumber": "GATEWAY123", "deviceType": "Infinity"},
                },
                {
                    "id": 317443,
                    "parentId": 317418,
                    "serviceLocationUuid": "station-location-uuid",
                    "name": "Garage Charger",
                    "functionType": "CHARGINGSTATION",
                    "chargingStation": {
                        "serialNumber": "STATION123",
                        "model": "EV Wall",
                    },
                    "gateway": {"serialNumber": "CONTROL123", "deviceType": "Connect"},
                    "writeAccess": True,
                },
            ]
        )
        self.async_get_highlevel_configuration = AsyncMock(
            side_effect=lambda sid: {
                317418: {
                    "measurements": [
                        {
                            "type": "GRID",
                            "updateChannels": {
                                "activePower": {
                                    "protocol": "MQTT",
                                    "name": "servicelocation/site-uuid/power",
                                    "aspectPaths": [{"path": "$.channelData[0]"}],
                                    "userName": "site-user",
                                    "password": "site-pass",
                                }
                            },
                        }
                    ]
                },
                317443: {
                    "measurements": [
                        {
                            "type": "APPLIANCE",
                            "appliance": {"type": "CAR_CHARGER"},
                            "updateChannels": {
                                "activePower": {
                                    "protocol": "MQTT",
                                    "name": "servicelocation/station-location-uuid/power",
                                    "aspectPaths": [{"path": "$.activePowerData[0]"}],
                                    "userName": "station-user",
                                    "password": "station-pass",
                                }
                            },
                        }
                    ]
                },
            }.get(sid, {})
        )
        self.async_get_smart_devices = AsyncMock(
            return_value=[
                {
                    "uuid": "station-uuid",
                    "id": "station-device",
                    "serialNumber": "STATION123",
                    "type": {"category": "CHARGINGSTATION"},
                },
                {
                    "uuid": "connector-uuid-1",
                    "id": "connector-device-1",
                    "position": 1,
                    "type": {"category": "CARCHARGER"},
                },
            ]
        )
        self.async_get_charging_station_details = AsyncMock(
            return_value={
                "chargingStation": {"model": "EV Wall"},
                "modules": [
                    {
                        "position": 1,
                        "smartDevice": {
                            "uuid": "connector-uuid-1",
                            "id": "connector-device-1",
                            "type": {"category": "CARCHARGER"},
                        },
                    }
                ],
            }
        )


class _FakeSiteCoordinator:
    def __init__(self, hass, **kwargs):
        self.hass = hass
        self.kwargs = kwargs
        self.data = SiteData(site=SimpleNamespace(mqtt_connected=None))
        self.last_update_success = True
        self.mqtt_connection_changes = []

    async def async_config_entry_first_refresh(self):
        return None

    def apply_mqtt_properties(self, topic, payload):
        self.last_properties = (topic, payload)

    def apply_mqtt_connection_change(self, up):
        self.mqtt_connection_changes.append(up)
        self.data.site.mqtt_connected = up

    async def async_shutdown(self):
        return None


class _FakeStationCoordinator:
    def __init__(self, hass, **kwargs):
        self.hass = hass
        self.kwargs = kwargs
        self.station_client = kwargs["station_client"]
        self.connector_clients = kwargs["connector_clients"]
        self.highlevel_configs = kwargs.get("highlevel_configs", {})
        self.update_interval = kwargs["update_interval"]
        self.last_update_success = True
        self.data = IntegrationData(
            station=SimpleNamespace(mqtt_connected=None),
            connectors={
                key: ConnectorState(connector_number=getattr(client, "connector_number", 1) or 1)
                for key, client in self.connector_clients.items()
            },
        )
        self.session_tracking_started = False
        self.mqtt_properties = []
        self.mqtt_connection_changes = []
        self.refresh_requested = AsyncMock()

    async def async_config_entry_first_refresh(self):
        return None

    def async_start_session_tracking(self):
        self.session_tracking_started = True

    def apply_mqtt_properties(self, topic, payload):
        self.mqtt_properties.append((topic, payload))

    def apply_mqtt_connection_change(self, up):
        self.mqtt_connection_changes.append(up)
        self.data.station.mqtt_connected = up

    async def async_request_refresh(self):
        await self.refresh_requested()

    def cancel_delayed_refreshes(self):
        return None

    async def async_shutdown(self):
        return None


class _FakeMqtt:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started_task = None
        self.stopped = False
        self.begin_shutdown_called = False
        self._mqtt_specs = kwargs.get("mqtt_specs") or []
        _FakeMqtt.instances.append(self)

    async def start(self):
        return None

    def track_start_task(self, task):
        self.started_task = task

    def begin_shutdown(self):
        self.begin_shutdown_called = True

    async def stop(self):
        self.stopped = True


class _ServiceRegistry:
    def __init__(self, hass):
        self.hass = hass
        self._services = {}

    def async_register(self, domain, service, handler, schema=None):
        self._services[(domain, service)] = (handler, schema)

    async def async_call(self, domain, service, data):
        handler, schema = self._services[(domain, service)]
        call = ServiceCall(
            domain=domain,
            service=service,
            data=schema(data) if schema is not None else data,
            hass=self.hass,
        )
        await handler(call)

    def has_service(self, domain, service):
        return (domain, service) in self._services


@pytest.fixture(autouse=True)
def reset_fake_mqtt_instances():
    _FakeMqtt.instances = []


@pytest.mark.asyncio
async def test_setup_entry_builds_runtime_from_dashboard_payloads(hass):
    dashboard = _FakeDashboard()
    entry = MagicMock()
    entry.data = {
        CONF_USERNAME: "user",
        CONF_PASSWORD: "pass",
        CONF_DASHBOARD_REFRESH_TOKEN: "refresh",
    }
    entry.options = {}
    entry.entry_id = "setup_entry_123456"
    entry.title = "Smappee EV"
    entry.state = ConfigEntryState.LOADED
    entry.runtime_data = None
    entry.async_on_unload = MagicMock()

    with (
        patch("custom_components.smappee_ev.async_get_clientsession", return_value=MagicMock()),
        patch("custom_components.smappee_ev._create_dashboard_client", return_value=dashboard),
        patch("custom_components.smappee_ev.SmappeeSiteCoordinator", _FakeSiteCoordinator),
        patch("custom_components.smappee_ev.SmappeeCoordinator", _FakeStationCoordinator),
        patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt", _FakeMqtt),
        patch("custom_components.smappee_ev._register_runtime_devices"),
        patch.object(hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock),
    ):
        assert await async_setup_entry(hass, entry) is True

    runtime = entry.runtime_data
    assert isinstance(runtime, RuntimeData)
    assert runtime.dashboard is dashboard
    assert set(runtime.sites) == {317418}

    site = runtime.sites[317418]
    assert site.site_name == "Home"
    assert site.site_uuid == "site-uuid"
    assert site.gateway_serial == "GATEWAY123"
    assert site.control_location_ids == [317443]
    assert site.measurement_location_ids == [317418, 317443]
    assert site.site_coordinator.kwargs["site_uuid"] == "site-uuid"
    assert set(site.highlevel_configs) == {317418, 317443}

    station = site.stations["station-uuid"]
    assert station.site_location_id == 317418
    assert station.control_location_id == 317443
    assert station.station_name == "Garage Charger"
    assert station.charging_station_serial == "STATION123"
    assert station.charging_station_model == "EV Wall"
    assert station.station_coordinator.session_tracking_started is True
    assert set(station.connectors) == {"connector-uuid-1"}

    connector = station.connectors["connector-uuid-1"]
    assert connector.connector_position == 1
    assert connector.connector_client.service_location_id == 317443
    assert connector.connector_client.site_location_id == 317418
    assert connector.connector_client.charging_station_serial == "STATION123"

    mqtt_clients = runtime.mqtt[317418]
    assert isinstance(mqtt_clients, list)
    assert len(mqtt_clients) == 2
    assert all(client.started_task is not None for client in mqtt_clients)
    assert [client.kwargs["client_id"] for client in mqtt_clients] == [
        "ha-123456-317418-1",
        "ha-123456-317418-2",
    ]
    assert [[spec.role for spec in client.kwargs["mqtt_specs"]] for client in mqtt_clients] == [
        ["grid"],
        ["car_charger"],
    ]


@pytest.mark.asyncio
async def test_setup_entry_dashboard_auth_failure_propagates_reauth(hass):
    dashboard = MagicMock()
    dashboard._token = None
    dashboard.username = None
    dashboard.password = None
    dashboard.refresh_token = "expired-refresh"  # noqa: S105 - fake test token
    dashboard.async_get_service_locations_full_details = AsyncMock(
        side_effect=ConfigEntryAuthFailed("Dashboard refresh token rejected")
    )
    entry = MagicMock()
    entry.data = {CONF_DASHBOARD_REFRESH_TOKEN: "expired-refresh"}
    entry.options = {}
    entry.entry_id = "auth_entry_123456"
    entry.title = "Smappee EV"
    entry.runtime_data = None

    with (
        patch("custom_components.smappee_ev.async_get_clientsession", return_value=MagicMock()),
        patch("custom_components.smappee_ev._create_dashboard_client", return_value=dashboard),
        pytest.raises(ConfigEntryAuthFailed, match="Dashboard refresh token rejected"),
    ):
        await async_setup_entry(hass, entry)

    assert entry.runtime_data is None


@pytest.mark.asyncio
async def test_registered_service_call_reaches_connector_and_refreshes_coordinator():
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    connector = make_connector_client(
        service_location_id=11111,
        connector_number=1,
        smart_device_uuid="connector-site-a",
    )
    runtime = make_runtime_for_connector(11111, connector)
    station = next(iter(runtime.sites[11111].stations.values()))
    coordinator = station.station_coordinator
    entry = make_loaded_config_entry("entry_a", runtime)
    configure_loaded_entries(hass, [entry])
    hass.services = _ServiceRegistry(hass)

    await register_services(hass)
    await hass.services.async_call(
        DOMAIN,
        "set_current",
        {
            "config_entry_id": "entry_a",
            "service_location_id": 11111,
            "connector_id": 1,
            "current": 17.24,
        },
    )

    connector.set_current.assert_awaited_once_with(current=17.2, min_current=6, max_current=32)
    coordinator.async_schedule_dashboard_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_registered_service_call_preserves_multi_site_validation():
    hass = MagicMock()
    site_a = make_connector_client(
        service_location_id=11111,
        connector_number=1,
        smart_device_uuid="connector-site-a",
    )
    site_b = make_connector_client(
        service_location_id=22222,
        connector_number=1,
        smart_device_uuid="connector-site-b",
    )
    entries = [
        make_loaded_config_entry("entry_a", make_runtime_for_connector(11111, site_a)),
        make_loaded_config_entry("entry_b", make_runtime_for_connector(22222, site_b)),
    ]
    configure_loaded_entries(hass, entries)
    hass.services = _ServiceRegistry(hass)

    await register_services(hass)
    with pytest.raises(ServiceValidationError) as exc:
        await hass.services.async_call(DOMAIN, "start_charging", {"connector_id": 1})

    assert exc.value.translation_key == "multiple_service_locations"
    site_a.start_charging.assert_not_called()
    site_b.start_charging.assert_not_called()


def test_mqtt_routes_deliver_properties_to_site_and_station_coordinators(hass):
    site_coordinator = MagicMock()
    site_coordinator._shutting_down = False
    site_coordinator.async_request_refresh = AsyncMock()
    station_coordinator = MagicMock()
    station_coordinator._shutting_down = False
    station_coordinator.update_interval = 30
    station_coordinator.async_request_refresh = AsyncMock()
    station_bucket = SimpleNamespace(station_coordinator=station_coordinator)
    specs = [
        MqttChannelSpec(
            317418, "grid", "activePower", "servicelocation/site-uuid/power", None, None, []
        ),
        MqttChannelSpec(
            317443,
            "car_charger",
            "activePower",
            "servicelocation/station-location-uuid/power",
            None,
            None,
            [],
        ),
    ]

    with patch("custom_components.smappee_ev.mqtt_setup.SmappeeMqtt", _FakeMqtt):
        mqtt_clients = _setup_mqtt(
            hass,
            "site-uuid",
            "GATEWAY123",
            317418,
            {"station": station_bucket},
            "ha-entry",
            30,
            mqtt_specs=specs,
            site_coordinator=site_coordinator,
            background_tasks=set(),
        )

    assert isinstance(mqtt_clients, list)
    site_mqtt, station_mqtt = mqtt_clients
    site_mqtt.kwargs["on_properties"]("servicelocation/site-uuid/power", {"channelData": [123]})
    station_mqtt.kwargs["on_properties"](
        "servicelocation/station-location-uuid/power", {"activePowerData": [456]}
    )

    site_coordinator.apply_mqtt_properties.assert_called_once_with(
        "servicelocation/site-uuid/power", {"channelData": [123]}
    )
    station_coordinator.apply_mqtt_properties.assert_called_once_with(
        "servicelocation/station-location-uuid/power", {"activePowerData": [456]}
    )


@pytest.mark.asyncio
async def test_service_call_survives_unload_reload_without_reregistering_services():
    """Domain services should keep resolving the current runtime after entry reload."""
    hass = MagicMock()
    hass.services = _ServiceRegistry(hass)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    first_connector = make_connector_client(
        service_location_id=11111,
        connector_number=1,
        smart_device_uuid="connector-before-reload",
    )
    first_runtime = make_runtime_for_connector(11111, first_connector)
    entry = make_loaded_config_entry("entry_a", first_runtime)
    configure_loaded_entries(hass, [entry])

    await register_services(hass)
    assert hass.services.has_service(DOMAIN, "set_current")

    await hass.services.async_call(
        DOMAIN,
        "set_current",
        {
            "config_entry_id": "entry_a",
            "service_location_id": 11111,
            "connector_id": 1,
            "current": 17,
        },
    )
    first_connector.set_current.assert_awaited_once_with(
        current=17.0, min_current=6, max_current=32
    )

    assert await async_unload_entry(hass, entry) is True
    assert hass.services.has_service(DOMAIN, "set_current")

    reloaded_connector = make_connector_client(
        service_location_id=11111,
        connector_number=1,
        smart_device_uuid="connector-after-reload",
    )
    entry.runtime_data = make_runtime_for_connector(11111, reloaded_connector)
    configure_loaded_entries(hass, [entry])

    await hass.services.async_call(
        DOMAIN,
        "set_current",
        {
            "config_entry_id": "entry_a",
            "service_location_id": 11111,
            "connector_id": 1,
            "current": 18,
        },
    )

    reloaded_connector.set_current.assert_awaited_once_with(
        current=18.0, min_current=6, max_current=32
    )
    first_connector.set_current.assert_awaited_once()
    hass.config_entries.async_unload_platforms.assert_awaited_once()
