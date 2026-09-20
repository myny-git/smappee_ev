"""Serial bootstrap regression coverage for accounts with empty service locations."""

from unittest.mock import MagicMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smappee_ev import (
    config_flow as flow_module,
    dashboard_discovery as discovery,
)
from custom_components.smappee_ev.api.dashboard_client import SmappeeDashboardClient
from custom_components.smappee_ev.api.errors import (
    SmappeeMaintenanceError,
    SmappeeNotFoundError,
    SmappeeServerError,
)
from custom_components.smappee_ev.const import (
    CONF_DASHBOARD_REFRESH_TOKEN,
    CONF_PASSWORD,
    CONF_STATION_SERIAL,
    CONF_USERNAME,
    DOMAIN,
)

CHILD = {
    "id": 20,
    "uuid": "child",
    "functionType": "CHARGINGSTATION",
    "parentId": 10,
    "chargingStation": {"serialNumber": "BASE123"},
}
PARENT = {"id": 10, "uuid": "parent", "functionType": "CHARGINGPARK"}
DATA = {CONF_USERNAME: "user", CONF_PASSWORD: "pass", CONF_DASHBOARD_REFRESH_TOKEN: "refresh"}


@pytest.fixture
def client():
    client = MagicMock(spec=SmappeeDashboardClient)
    client.async_get_charging_station_details.return_value = {"serviceLocation": {"id": 20}}
    client.async_get_service_location_details.side_effect = [dict(CHILD), dict(PARENT)]
    return client


@pytest.mark.parametrize("parent", [True, False])
async def test_serial_builds_existing_topology(client, parent):
    child = dict(CHILD)
    if not parent:
        child.pop("parentId")
    client.async_get_service_location_details.side_effect = [child, PARENT]
    result = await discovery._dashboard_discover_topologies_by_serial(client, "BASE123")
    assert len(result) == 1
    topology = result[0]
    assert topology.site_location_id == (10 if parent else 20)
    assert topology.control_location_id == 20
    assert topology.measurement_location_ids == ([10, 20] if parent else [20])
    assert topology.charging_station_serial == "BASE123"
    client.async_get_charging_station_details.assert_awaited_once_with("BASE123")
    assert client.async_get_service_location_details.await_count == (2 if parent else 1)


@pytest.mark.parametrize(
    "station",
    [
        None,
        [],
        {},
        {"serviceLocation": {}},
        {"serviceLocation": {"id": []}},
        {"serviceLocation": {"id": True}},
        {"serviceLocation": {"id": "bad"}},
    ],
)
async def test_invalid_station(client, station):
    client.async_get_charging_station_details.return_value = station
    assert await discovery._dashboard_discover_topologies_by_serial(client, "bad") == []
    client.async_get_service_location_details.assert_not_awaited()


@pytest.mark.parametrize(
    "lookup", ["async_get_charging_station_details", "async_get_service_location_details"]
)
async def test_serial_resource_not_found(client, lookup):
    getattr(client, lookup).side_effect = SmappeeNotFoundError()
    assert await discovery._dashboard_discover_topologies_by_serial(client, "bad") == []


async def test_invalid_parent_id(client):
    client.async_get_service_location_details.side_effect = [{**CHILD, "parentId": "bad"}]
    with pytest.raises(ValueError, match="invalid parent id"):
        await discovery._dashboard_discover_topologies_by_serial(client, "BASE123")


async def test_malformed_automatic_response_is_not_empty(client):
    client.username = "user"
    client.password = "pass"  # noqa: S105 - fake test password
    client.async_get_service_locations_full_details.return_value = None
    with pytest.raises(ValueError, match="no valid list"):
        await discovery._dashboard_discover_topologies(client)


async def test_blank_serial_does_not_call_api(flow):
    flow._pending_data = dict(DATA)
    with patch.object(flow_module, "_dashboard_discover_topologies_by_serial") as fallback:
        result = await flow.async_step_station_serial({CONF_STATION_SERIAL: "  "})
    assert result["errors"] == {"base": "invalid_station_serial"}
    fallback.assert_not_called()


@pytest.mark.parametrize(
    "child",
    [None, [], {}, {"id": 99}, {"id": 20}, {"id": 20, "chargingStation": {"model": "EV Base"}}],
)
async def test_missing_or_unusable_child(client, child):
    client.async_get_service_location_details.side_effect = [child]
    assert await discovery._dashboard_discover_topologies_by_serial(client, "BASE123") == []


@pytest.mark.parametrize(
    "parent",
    [None, {}, {"id": 99}, TimeoutError(), SmappeeServerError(), SmappeeMaintenanceError()],
)
async def test_known_parent_must_resolve(client, parent):
    client.async_get_service_location_details.side_effect = [CHILD, parent]
    with pytest.raises((ValueError, TimeoutError, SmappeeServerError, SmappeeMaintenanceError)):
        await discovery._dashboard_discover_topologies_by_serial(client, "BASE123")


@pytest.mark.parametrize("serial", ["BASE456", "", None])
async def test_station_details_serial_must_match(client, serial):
    client.async_get_charging_station_details.return_value["serialNumber"] = serial
    assert await discovery._dashboard_discover_topologies_by_serial(client, "BASE123") == []
    client.async_get_service_location_details.assert_not_awaited()


async def test_topology_serial_must_match(client):
    client.async_get_charging_station_details.return_value["serialNumber"] = "BASE123"
    child = {**CHILD, "chargingStation": {"serialNumber": "BASE456"}}
    client.async_get_service_location_details.side_effect = [child, PARENT]
    assert await discovery._dashboard_discover_topologies_by_serial(client, "BASE123") == []


async def test_serial_comparison_trims_whitespace(client):
    client.async_get_charging_station_details.return_value["serialNumber"] = " BASE123 "
    child = {**CHILD, "chargingStation": {"serialNumber": " BASE123 "}}
    client.async_get_service_location_details.side_effect = [child, PARENT]
    result = await discovery._dashboard_discover_topologies_by_serial(client, " BASE123 ")
    assert result[0].charging_station_serial == "BASE123"
    client.async_get_charging_station_details.assert_awaited_once_with("BASE123")


async def test_empty_bootstrap_serial_does_not_call_api(client):
    assert await discovery._dashboard_discover_topologies_by_serial(client, "  ") == []
    client.async_get_charging_station_details.assert_not_awaited()


async def test_parent_not_found_is_invalid_serial_in_flow(flow, client):
    flow._pending_data = dict(DATA)
    client.async_get_service_location_details.side_effect = [CHILD, SmappeeNotFoundError()]
    with patch.object(flow, "_discovery_client", return_value=client):
        result = await flow.async_step_station_serial({CONF_STATION_SERIAL: "BASE123"})
    assert result["step_id"] == "station_serial"
    assert result["errors"] == {"base": "invalid_station_serial"}


@pytest.mark.parametrize("payload", [{"id": 20}, [], None, "bad"])
async def test_direct_location_client(payload):
    client = SmappeeDashboardClient(
        session=MagicMock(),
        username=None,
        password=None,
        refresh_token=None,
        token_update_callback=None,
    )
    with patch.object(client, "_request", return_value=payload) as request:
        assert await client.async_get_service_location_details(20) == (
            payload if isinstance(payload, dict) else None
        )
    request.assert_awaited_once_with(
        "GET", "v10/servicelocation/20?includeDetails=true", return_json=True
    )


async def test_direct_location_client_propagates_error():
    client = SmappeeDashboardClient(
        session=MagicMock(),
        username=None,
        password=None,
        refresh_token=None,
        token_update_callback=None,
    )
    with patch.object(client, "_request", side_effect=TimeoutError), pytest.raises(TimeoutError):
        await client.async_get_service_location_details(20)


@pytest.mark.parametrize(
    ("automatic", "serial", "used"),
    [([object()], "BASE123", False), ([], "BASE123", True), ([], None, False)],
)
async def test_runtime_fallback(client, automatic, serial, used):
    with (
        patch.object(discovery, "_dashboard_discover_topologies", return_value=automatic),
        patch.object(
            discovery, "_dashboard_discover_topologies_by_serial", return_value=[object()]
        ) as fallback,
    ):
        if not automatic and not serial:
            with pytest.raises(ConfigEntryNotReady):
                await discovery._load_dashboard_topologies(client, serial)
        else:
            assert await discovery._load_dashboard_topologies(client, serial)
    assert fallback.called == used


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), SmappeeServerError(), SmappeeMaintenanceError(), ConfigEntryAuthFailed()],
)
async def test_runtime_error_never_uses_serial(client, error):
    with (
        patch.object(discovery, "_dashboard_discover_topologies", side_effect=error),
        patch.object(discovery, "_dashboard_discover_topologies_by_serial") as fallback,
        pytest.raises(
            (
                ConfigEntryNotReady,
                SmappeeServerError,
                SmappeeMaintenanceError,
                ConfigEntryAuthFailed,
            )
        ),
    ):
        await discovery._load_dashboard_topologies(client, "BASE123")
    fallback.assert_not_called()


@pytest.fixture
def flow(hass):
    flow = flow_module.SmappeeEvConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    with (
        patch.object(flow_module, "async_get_clientsession", return_value=MagicMock()),
        patch.object(flow_module, "_async_dashboard_auth_data", return_value=(dict(DATA), None)),
    ):
        yield flow


async def test_empty_discovery_prompts_and_validates_serial(flow):
    with patch.object(flow_module, "_dashboard_discover_topologies", return_value=[]):
        result = await flow.async_step_user(DATA)
    assert result["step_id"] == "station_serial"
    with patch.object(
        flow_module, "_dashboard_discover_topologies_by_serial", side_effect=[[], [object()]]
    ) as fallback:
        result = await flow.async_step_station_serial({CONF_STATION_SERIAL: "bad"})
        assert result["errors"] == {"base": "invalid_station_serial"}
        result = await flow.async_step_station_serial({CONF_STATION_SERIAL: " BASE123 "})
    assert result["type"] == "create_entry"
    assert result["data"][CONF_STATION_SERIAL] == "BASE123"
    assert fallback.await_args.args[1] == "BASE123"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), "cannot_connect"),
        (SmappeeServerError(), "cannot_connect"),
        (SmappeeMaintenanceError(), "cannot_connect"),
        (ConfigEntryAuthFailed(), "auth_failed"),
        (ValueError(), "unknown"),
    ],
)
async def test_flow_discovery_errors_do_not_prompt(flow, error, expected):
    with patch.object(flow_module, "_dashboard_discover_topologies", side_effect=error):
        result = await flow.async_step_user(DATA)
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected}


@pytest.mark.parametrize(("stored", "valid"), [(None, False), ("BASE123", True), ("old", False)])
async def test_reconfigure_reuses_or_replaces_serial(flow, stored, valid):
    data = dict(DATA)
    if stored:
        data[CONF_STATION_SERIAL] = stored
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id="smappee_ev:user")
    entry.add_to_hass(flow.hass)
    flow.context = {"source": "reconfigure", "entry_id": entry.entry_id}
    with (
        patch.object(flow_module, "_dashboard_discover_topologies", return_value=[]),
        patch.object(
            flow_module,
            "_dashboard_discover_topologies_by_serial",
            return_value=[object()] if valid else [],
        ) as fallback,
        patch.object(flow.hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await flow.async_step_reconfigure(DATA)
        if not valid:
            assert result["step_id"] == "station_serial"
            if stored:
                key = next(iter(result["data_schema"].schema))
                assert key.default() == stored
            fallback.return_value = [object()]
            result = await flow.async_step_station_serial({CONF_STATION_SERIAL: "replacement"})
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_STATION_SERIAL] == (stored if valid else "replacement")
        reload.assert_called_once_with(entry.entry_id)


async def test_reauth_preserves_serial_without_discovery(flow):
    entry = MockConfigEntry(
        domain=DOMAIN, data={**DATA, CONF_STATION_SERIAL: "BASE123"}, unique_id="smappee_ev:user"
    )
    entry.add_to_hass(flow.hass)
    flow.context = {"source": "reauth", "entry_id": entry.entry_id}
    with (
        patch.object(flow_module, "_dashboard_discover_topologies") as automatic,
        patch.object(flow_module, "_dashboard_discover_topologies_by_serial") as fallback,
        patch.object(flow.hass.config_entries, "async_schedule_reload"),
    ):
        result = await flow.async_step_reauth_confirm(DATA)
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_STATION_SERIAL] == "BASE123"
    automatic.assert_not_called()
    fallback.assert_not_called()
