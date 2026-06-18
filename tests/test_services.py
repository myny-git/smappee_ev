# tests/test_services.py
from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
import voluptuous as vol

from custom_components.smappee_ev import services
from custom_components.smappee_ev.data import ConnectorState, IntegrationData, StationState
from custom_components.smappee_ev.device_handle import SmappeeDeviceHandle
from tests.factories import (
    configure_loaded_entries,
    make_connector_client,
    make_loaded_config_entry,
    make_runtime_data,
    make_runtime_for_connector,
)


@pytest.fixture
def mock_hass():
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    hass.config_entries.async_get_entry.return_value = None
    hass.services.async_register = MagicMock()
    hass.services.async_remove = MagicMock()
    return hass


@pytest.fixture
def mock_config_entry():
    """Create a mock ConfigEntry."""
    return make_loaded_config_entry()


@pytest.fixture
def mock_api_client():
    """Create a mock SmappeeDeviceHandle."""
    return make_connector_client(serial="SERIAL_A")


@pytest.fixture
def mock_runtime_data(mock_api_client):
    """Create a mock RuntimeData."""
    sites = {
        12345: {
            "stations": {
                "station1": {
                    "station_client": mock_api_client,
                    "connector_clients": {
                        "connector_uuid_1": mock_api_client,
                    },
                }
            }
        }
    }
    return make_runtime_data(api=mock_api_client, sites=sites, mqtt=MagicMock())


@pytest.fixture
def mock_loaded_entries(mock_config_entry, mock_runtime_data):
    """Create mock loaded config entries."""
    mock_config_entry.runtime_data = mock_runtime_data
    mock_config_entry.state = ConfigEntryState.LOADED  # Ensure this is set
    return [mock_config_entry]


@pytest.fixture
def mock_hass_with_entries(mock_hass, mock_loaded_entries):
    """Create a mock HomeAssistant instance with loaded entries."""
    mock_hass.config_entries.async_entries.return_value = mock_loaded_entries

    # Setup async_get_entry to return the entry by ID
    def get_entry_by_id(entry_id):
        for entry in mock_loaded_entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    mock_hass.config_entries.async_get_entry = get_entry_by_id
    return mock_hass


class TestServiceHelpers:
    """Test cases for service helper functions."""

    def test_iter_loaded_entries(self, mock_hass_with_entries, mock_loaded_entries):
        """Test iteration over loaded entries."""
        result = services._iter_loaded_entries(mock_hass_with_entries)

        assert len(result) == 1
        assert result[0].entry_id == "test_entry_id"

    def test_first_runtime(self, mock_hass_with_entries, mock_loaded_entries, mock_runtime_data):
        """Test getting first runtime data."""
        result = services._first_runtime(mock_hass_with_entries)

        assert result is mock_runtime_data

    def test_first_runtime_no_entries(self, mock_hass):
        """Test getting first runtime data with no entries."""
        mock_hass.config_entries.async_entries.return_value = []

        result = services._first_runtime(mock_hass)

        assert result is None

    def test_runtime_by_entry_id(
        self, mock_hass_with_entries, mock_loaded_entries, mock_runtime_data
    ):
        """Test getting runtime data by entry ID."""
        result = services._runtime_by_entry_id(mock_hass_with_entries, "test_entry_id")

        assert result is mock_runtime_data

    def test_runtime_by_entry_id_not_found(self, mock_hass_with_entries):
        """Test getting runtime data by entry ID when not found."""
        result = services._runtime_by_entry_id(mock_hass_with_entries, "nonexistent")

        assert result is None

    def test_runtime_by_entry_id_not_loaded(self, mock_hass_with_entries, mock_config_entry):
        """Test unloaded entries are ignored when resolving by entry ID."""
        mock_config_entry.state = ConfigEntryState.NOT_LOADED

        result = services._runtime_by_entry_id(mock_hass_with_entries, "test_entry_id")

        assert result is None

    def test_find_runtime_for_sid(
        self, mock_hass_with_entries, mock_loaded_entries, mock_runtime_data
    ):
        """Test finding runtime data for a specific service location ID."""
        result = services._find_runtime_for_sid(mock_hass_with_entries, 12345)

        assert result is mock_runtime_data

    def test_find_runtime_for_sid_not_found(self, mock_hass, mock_loaded_entries):
        """Test finding runtime data for non-existent service location ID."""
        mock_hass.config_entries.async_entries.return_value = mock_loaded_entries

        result = services._find_runtime_for_sid(mock_hass, 99999)

        assert result is None

    def test_only_or_single_sid(self):
        """Test getting single service location ID."""
        sites = {12345: {"test": "data"}}
        result = services._only_or_single_sid(sites)
        assert result == 12345

        # Multiple sites should return None
        sites = {12345: {"test": "data"}, 67890: {"test": "data2"}}
        result = services._only_or_single_sid(sites)
        assert result is None

    def test_resolve_sid_rejects_service_location_outside_explicit_entry(
        self, mock_hass_with_entries
    ):
        call = ServiceCall(
            domain="smappee_ev",
            service="pause_charging",
            data={"config_entry_id": "test_entry_id", "service_location_id": 99999},
            hass=mock_hass_with_entries,
        )

        with pytest.raises(ServiceValidationError, match="does not belong"):
            services._resolve_sid(mock_hass_with_entries, call)

    def test_resolve_sid_accepts_service_location_inside_explicit_entry(
        self, mock_hass_with_entries, mock_runtime_data
    ):
        call = ServiceCall(
            domain="smappee_ev",
            service="pause_charging",
            data={"config_entry_id": "test_entry_id", "service_location_id": 12345},
            hass=mock_hass_with_entries,
        )

        rt, sid = services._resolve_sid(mock_hass_with_entries, call)

        assert rt is mock_runtime_data
        assert sid == 12345

    def test_resolve_sid_uses_runtime_containing_requested_sid(
        self, mock_hass, mock_loaded_entries, mock_api_client
    ):
        other_runtime = make_runtime_data(
            api=mock_api_client,
            sites={67890: {"stations": {}}},
            mqtt=MagicMock(),
        )
        other_entry = make_loaded_config_entry("other", other_runtime)
        mock_hass.config_entries.async_entries.return_value = [mock_loaded_entries[0], other_entry]
        call = ServiceCall(
            domain="smappee_ev",
            service="pause_charging",
            data={"service_location_id": 67890},
            hass=mock_hass,
        )

        rt, sid = services._resolve_sid(mock_hass, call)

        assert rt is other_runtime
        assert sid == 67890

    def test_resolve_sid_unknown_sid_raises_service_location_not_found(
        self, mock_hass_with_entries
    ):
        call = ServiceCall(
            domain="smappee_ev",
            service="pause_charging",
            data={"service_location_id": 99999},
            hass=mock_hass_with_entries,
        )

        with pytest.raises(ServiceValidationError, match="was not found"):
            services._resolve_sid(mock_hass_with_entries, call)

    def test_get_station_client(self, mock_runtime_data, mock_api_client):
        """Test getting station client."""
        result = services.get_station_client(mock_runtime_data, 12345)

        assert result is mock_api_client

    def test_get_station_client_no_runtime(self):
        """Test getting station client with no runtime data."""
        result = services.get_station_client(None, 12345)

        assert result is None

    def test_get_station_client_without_site_or_station(self, mock_runtime_data):
        """Test station client lookup when site data is missing or empty."""
        assert services.get_station_client(mock_runtime_data, 99999) is None
        mock_runtime_data.sites[12345] = {"stations": {}}
        assert services.get_station_client(mock_runtime_data, 12345) is None

    def test_get_connector_client_by_id(self, mock_runtime_data, mock_api_client):
        """Test getting connector client by connector ID."""
        result = services.get_connector_client(mock_runtime_data, 12345, 1)

        assert result is mock_api_client

    def test_get_connector_client_single(self, mock_runtime_data, mock_api_client):
        """Test getting single connector client when no ID specified."""
        result = services.get_connector_client(mock_runtime_data, 12345, None)

        assert result is mock_api_client

    def test_get_connector_client_not_found(self, mock_runtime_data):
        """Test getting connector client that doesn't exist."""
        result = services.get_connector_client(mock_runtime_data, 12345, 99)

        assert result is None

    def test_get_connector_client_ambiguous_without_id(self, mock_runtime_data, mock_api_client):
        """Test connector lookup refuses to guess when multiple connectors exist."""
        other_client = MagicMock(spec=SmappeeDeviceHandle)
        other_client.connector_number = 2
        mock_runtime_data.sites[12345]["stations"]["station1"]["connector_clients"][2] = (
            other_client
        )

        result = services.get_connector_client(mock_runtime_data, 12345, None)

        assert result is None

    def test_connector_current_range_uses_live_state_and_clamps_bad_range(self, mock_api_client):
        """Test current ranges come from live connector state when available."""
        mock_api_client.smart_device_uuid = "uuid"
        coord = MagicMock()
        coord.data = IntegrationData(
            station=StationState(),
            connectors={"uuid": ConnectorState(connector_number=1, min_current=10, max_current=6)},
        )
        runtime = make_runtime_data(
            api=mock_api_client,
            sites={12345: {"stations": {"station1": {"coordinator": coord}}}},
            mqtt=MagicMock(),
        )

        assert services._connector_current_range(runtime, mock_api_client) == (10, 10)

    def test_connector_current_range_defaults_without_runtime_or_uuid(self, mock_api_client):
        """Test current range falls back when no live connector state can be found."""
        assert services._connector_current_range(None, mock_api_client) == (6, 32)

        mock_api_client.smart_device_uuid = None
        runtime = make_runtime_data(
            api=mock_api_client, sites={12345: {"stations": {}}}, mqtt=MagicMock()
        )
        assert services._connector_current_range(runtime, mock_api_client) == (6, 32)

        mock_api_client.smart_device_uuid = "missing"
        assert services._connector_current_range(runtime, mock_api_client) == (6, 32)


class TestServiceHandlers:
    """Test cases for service handlers."""

    @pytest.mark.asyncio
    async def test_handle_start_charging_success(
        self, mock_hass_with_entries, mock_loaded_entries, mock_api_client
    ):
        """Test successful start charging service call."""
        call = ServiceCall(
            domain="smappee_ev",
            service="start_charging",
            data={"current": 16, "connector_id": 1},
            hass=mock_hass_with_entries,
        )

        await services.handle_start_charging(call)

        mock_api_client.start_charging.assert_called_once_with(
            current=16, min_current=6, max_current=32
        )

    @pytest.mark.asyncio
    async def test_handle_start_charging_defaults_to_min_current(
        self, mock_hass_with_entries, mock_api_client
    ):
        """Test start charging defaults to the connector minimum current."""
        call = ServiceCall(
            domain="smappee_ev",
            service="start_charging",
            data={"connector_id": 1},
            hass=mock_hass_with_entries,
        )

        await services.handle_start_charging(call)

        mock_api_client.start_charging.assert_called_once_with(
            current=6, min_current=6, max_current=32
        )

    @pytest.mark.asyncio
    async def test_handle_start_charging_validation_error(
        self, mock_hass, mock_loaded_entries, mock_api_client
    ):
        """Test start charging with current out of range."""
        mock_hass.config_entries.async_entries.return_value = mock_loaded_entries

        call = ServiceCall(
            domain="smappee_ev",
            service="start_charging",
            data={"current": 50, "connector_id": 1},  # Too high
            hass=mock_hass,
        )

        with pytest.raises(ServiceValidationError, match="current 50 A out of range"):
            await services.handle_start_charging(call)

    @pytest.mark.asyncio
    async def test_handle_pause_charging_success(
        self, mock_hass, mock_loaded_entries, mock_api_client
    ):
        """Test successful pause charging service call."""
        mock_hass.config_entries.async_entries.return_value = mock_loaded_entries

        call = ServiceCall(
            domain="smappee_ev", service="pause_charging", data={"connector_id": 1}, hass=mock_hass
        )

        await services.handle_pause_charging(call)

        mock_api_client.pause_charging.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_stop_charging_success(
        self, mock_hass, mock_loaded_entries, mock_api_client
    ):
        """Test successful stop charging service call."""
        mock_hass.config_entries.async_entries.return_value = mock_loaded_entries

        call = ServiceCall(
            domain="smappee_ev", service="stop_charging", data={"connector_id": 1}, hass=mock_hass
        )

        await services.handle_stop_charging(call)

        mock_api_client.stop_charging.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_resume_charging_uses_selected_mode(
        self, mock_hass_with_entries, mock_runtime_data, mock_api_client
    ):
        """Test resume charging restores the live selected mode."""
        coord = MagicMock()
        coord.data = IntegrationData(
            station=StationState(),
            connectors={
                "connector_uuid_1": ConnectorState(
                    connector_number=1,
                    selected_mode="solar",
                    ui_mode_base="SMART",
                )
            },
        )
        mock_runtime_data.sites[12345]["stations"]["station1"]["coordinator"] = coord
        call = ServiceCall(
            domain="smappee_ev",
            service="resume_charging",
            data={"connector_id": 1},
            hass=mock_hass_with_entries,
        )

        await services.handle_resume_charging(call)

        mock_api_client.set_charging_mode.assert_awaited_once_with(mode="SOLAR")

    @pytest.mark.asyncio
    async def test_handle_resume_charging_defaults_to_standard(
        self, mock_hass_with_entries, mock_api_client
    ):
        """Test resume charging falls back to STANDARD when no mode is known."""
        call = ServiceCall(
            domain="smappee_ev",
            service="resume_charging",
            data={"connector_id": 1},
            hass=mock_hass_with_entries,
        )

        await services.handle_resume_charging(call)

        mock_api_client.set_charging_mode.assert_awaited_once_with(mode="STANDARD")

    @pytest.mark.asyncio
    async def test_handle_set_charging_mode_success(
        self, mock_hass, mock_loaded_entries, mock_api_client
    ):
        """Test successful set charging mode service call."""
        mock_hass.config_entries.async_entries.return_value = mock_loaded_entries

        call = ServiceCall(
            domain="smappee_ev",
            service="set_charging_mode",
            data={"mode": "SMART", "connector_id": 1},
            hass=mock_hass,
        )

        await services.handle_set_charging_mode(call)

        mock_api_client.set_charging_mode.assert_called_once_with(mode="SMART")

    @pytest.mark.asyncio
    async def test_handle_service_no_client(self, mock_hass):
        """Test service call when no client is found."""
        mock_hass.config_entries.async_entries.return_value = []

        call = ServiceCall(
            domain="smappee_ev", service="start_charging", data={"current": 16}, hass=mock_hass
        )

        with pytest.raises(ServiceValidationError, match="Cannot resolve connector client"):
            await services.handle_start_charging(call)

    @pytest.mark.asyncio
    async def test_handle_service_multi_site_no_id(self, mock_hass, mock_loaded_entries):
        """Test service call with multiple sites but no service_location_id."""
        # Add another site to mock runtime data
        mock_loaded_entries[0].runtime_data.sites[67890] = {"test": "data"}
        mock_hass.config_entries.async_entries.return_value = mock_loaded_entries

        call = ServiceCall(
            domain="smappee_ev", service="start_charging", data={"current": 16}, hass=mock_hass
        )

        with pytest.raises(ServiceValidationError, match="Multiple service locations"):
            await services.handle_start_charging(call)

    @pytest.mark.asyncio
    async def test_multi_entry_connector_service_requires_explicit_site_or_entry(self, mock_hass):
        """Protect against silently charging the first matching connector across sites."""
        site_a_client = make_connector_client(
            service_location_id=11111,
            connector_number=1,
            smart_device_uuid="connector-site-a",
        )
        site_b_client = make_connector_client(
            service_location_id=22222,
            connector_number=1,
            smart_device_uuid="connector-site-b",
        )
        entries = [
            make_loaded_config_entry("entry_a", make_runtime_for_connector(11111, site_a_client)),
            make_loaded_config_entry("entry_b", make_runtime_for_connector(22222, site_b_client)),
        ]
        configure_loaded_entries(mock_hass, entries)
        call = ServiceCall(
            domain="smappee_ev",
            service="start_charging",
            data={"current": 16, "connector_id": 1},
            hass=mock_hass,
        )

        with pytest.raises(ServiceValidationError, match="Multiple service locations"):
            await services.handle_start_charging(call)

        site_a_client.start_charging.assert_not_called()
        site_b_client.start_charging.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_entry_connector_service_config_entry_id_selects_target(self, mock_hass):
        """Ensure explicit config_entry_id dispatches only to that entry's connector."""
        site_a_client = make_connector_client(
            service_location_id=11111,
            connector_number=1,
            smart_device_uuid="connector-site-a",
        )
        site_b_client = make_connector_client(
            service_location_id=22222,
            connector_number=1,
            smart_device_uuid="connector-site-b",
        )
        entries = [
            make_loaded_config_entry("entry_a", make_runtime_for_connector(11111, site_a_client)),
            make_loaded_config_entry("entry_b", make_runtime_for_connector(22222, site_b_client)),
        ]
        configure_loaded_entries(mock_hass, entries)
        call = ServiceCall(
            domain="smappee_ev",
            service="start_charging",
            data={"config_entry_id": "entry_a", "current": 16, "connector_id": 1},
            hass=mock_hass,
        )

        await services.handle_start_charging(call)

        site_a_client.start_charging.assert_awaited_once_with(
            current=16, min_current=6, max_current=32
        )
        site_b_client.start_charging.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_entry_connector_service_rejects_mismatched_entry_and_site(self, mock_hass):
        """Reject mismatched selectors instead of falling back to another site."""
        site_a_client = make_connector_client(
            service_location_id=11111,
            connector_number=1,
            smart_device_uuid="connector-site-a",
        )
        site_b_client = make_connector_client(
            service_location_id=22222,
            connector_number=2,
            smart_device_uuid="connector-site-b",
        )
        entries = [
            make_loaded_config_entry("entry_a", make_runtime_for_connector(11111, site_a_client)),
            make_loaded_config_entry("entry_b", make_runtime_for_connector(22222, site_b_client)),
        ]
        configure_loaded_entries(mock_hass, entries)
        call = ServiceCall(
            domain="smappee_ev",
            service="start_charging",
            data={
                "config_entry_id": "entry_a",
                "service_location_id": 22222,
                "connector_id": 2,
                "current": 16,
            },
            hass=mock_hass,
        )

        with pytest.raises(ServiceValidationError, match="does not belong"):
            await services.handle_start_charging(call)

        site_a_client.start_charging.assert_not_called()
        site_b_client.start_charging.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_service_api_error(self, mock_hass, mock_loaded_entries, mock_api_client):
        """Test service call when API method fails."""
        mock_hass.config_entries.async_entries.return_value = mock_loaded_entries
        mock_api_client.start_charging.side_effect = Exception("API Error")

        call = ServiceCall(
            domain="smappee_ev",
            service="start_charging",
            data={"current": 16, "connector_id": 1},
            hass=mock_hass,
        )

        with pytest.raises(HomeAssistantError, match="Connector service 'start_charging' failed"):
            await services.handle_start_charging(call)

    @pytest.mark.asyncio
    async def test_async_handle_station_service_success(
        self, mock_hass_with_entries, mock_api_client
    ):
        """Test generic station service dispatch."""
        call = ServiceCall(
            domain="smappee_ev",
            service="station",
            data={},
            hass=mock_hass_with_entries,
        )

        await services.async_handle_station_service(
            mock_hass_with_entries, call, "pause_charging", {"reason": "test"}
        )

        mock_api_client.pause_charging.assert_awaited_once_with(reason="test")

    @pytest.mark.asyncio
    async def test_async_handle_station_service_validation_paths(
        self, mock_hass_with_entries, mock_loaded_entries
    ):
        """Test station service validation for ambiguous, missing, and unknown methods."""
        mock_loaded_entries[0].runtime_data.sites[67890] = {"stations": {}}
        call = ServiceCall(
            domain="smappee_ev",
            service="station",
            data={},
            hass=mock_hass_with_entries,
        )

        with pytest.raises(ServiceValidationError, match="Multiple service locations"):
            await services.async_handle_station_service(
                mock_hass_with_entries, call, "pause_charging"
            )

        del mock_loaded_entries[0].runtime_data.sites[67890]
        call = ServiceCall(
            domain="smappee_ev",
            service="station",
            data={"service_location_id": 99999},
            hass=mock_hass_with_entries,
        )
        with pytest.raises(ServiceValidationError, match="was not found"):
            await services.async_handle_station_service(
                mock_hass_with_entries, call, "pause_charging"
            )

        call = ServiceCall(
            domain="smappee_ev", service="station", data={}, hass=mock_hass_with_entries
        )
        with pytest.raises(ServiceValidationError, match="Station method"):
            await services.async_handle_station_service(
                mock_hass_with_entries, call, "missing_method"
            )

    @pytest.mark.asyncio
    async def test_async_handle_station_service_api_error(
        self, mock_hass_with_entries, mock_api_client
    ):
        """Test station service wraps API failures."""
        mock_api_client.pause_charging.side_effect = Exception("station failed")
        call = ServiceCall(
            domain="smappee_ev", service="station", data={}, hass=mock_hass_with_entries
        )

        with pytest.raises(HomeAssistantError, match="Station service 'pause_charging' failed"):
            await services.async_handle_station_service(
                mock_hass_with_entries, call, "pause_charging"
            )

    @pytest.mark.asyncio
    async def test_async_handle_connector_service_method_missing(self, mock_hass_with_entries):
        """Test generic connector service reports unknown client methods."""
        call = ServiceCall(
            domain="smappee_ev",
            service="connector",
            data={"connector_id": 1},
            hass=mock_hass_with_entries,
        )

        with pytest.raises(ServiceValidationError, match="Connector method"):
            await services.async_handle_connector_service(
                mock_hass_with_entries, call, "missing_method"
            )

    @pytest.mark.asyncio
    async def test_async_handle_connector_service_validation_paths(
        self, mock_hass_with_entries, mock_loaded_entries
    ):
        """Test generic connector service validation for ambiguity and missing client."""
        mock_loaded_entries[0].runtime_data.sites[67890] = {"stations": {}}
        call = ServiceCall(
            domain="smappee_ev",
            service="connector",
            data={},
            hass=mock_hass_with_entries,
        )

        with pytest.raises(ServiceValidationError, match="Multiple service locations"):
            await services.async_handle_connector_service(
                mock_hass_with_entries, call, "pause_charging"
            )

        del mock_loaded_entries[0].runtime_data.sites[67890]
        call = ServiceCall(
            domain="smappee_ev",
            service="connector",
            data={"connector_id": 99},
            hass=mock_hass_with_entries,
        )
        with pytest.raises(ServiceValidationError, match="No matching connector client"):
            await services.async_handle_connector_service(
                mock_hass_with_entries, call, "pause_charging"
            )

    @pytest.mark.asyncio
    async def test_handle_set_current_success_and_validation_paths(
        self, mock_hass_with_entries, mock_api_client
    ):
        """Test set_current success plus no-client and range validation."""
        call = ServiceCall(
            domain="smappee_ev",
            service="set_current",
            data={"connector_id": 1, "current": 17.24},
            hass=mock_hass_with_entries,
        )

        await services.handle_set_current(call)

        mock_api_client.set_current.assert_awaited_once_with(
            current=17.2, min_current=6, max_current=32
        )

        no_client_call = ServiceCall(
            domain="smappee_ev",
            service="set_current",
            data={"connector_id": 99, "current": 17},
            hass=mock_hass_with_entries,
        )
        with pytest.raises(ServiceValidationError, match="No matching connector client"):
            await services.handle_set_current(no_client_call)

        out_of_range_call = ServiceCall(
            domain="smappee_ev",
            service="set_current",
            data={"connector_id": 1, "current": 40},
            hass=mock_hass_with_entries,
        )
        with pytest.raises(ServiceValidationError, match="out of range"):
            await services.handle_set_current(out_of_range_call)

    @pytest.mark.asyncio
    async def test_handle_set_current_refreshes_only_owning_coordinator(self, mock_hass):
        """A connector write must refresh only the station that owns that connector."""
        site_a_client = make_connector_client(
            service_location_id=11111,
            connector_number=1,
            smart_device_uuid="connector-site-a",
        )
        site_b_client = make_connector_client(
            service_location_id=22222,
            connector_number=1,
            smart_device_uuid="connector-site-b",
        )
        runtime_a = make_runtime_for_connector(11111, site_a_client)
        runtime_b = make_runtime_for_connector(22222, site_b_client)
        coord_a = next(iter(runtime_a.sites[11111]["stations"].values()))["coordinator"]
        coord_b = next(iter(runtime_b.sites[22222]["stations"].values()))["coordinator"]
        entries = [
            make_loaded_config_entry("entry_a", runtime_a),
            make_loaded_config_entry("entry_b", runtime_b),
        ]
        configure_loaded_entries(mock_hass, entries)
        call = ServiceCall(
            domain="smappee_ev",
            service="set_current",
            data={"config_entry_id": "entry_a", "connector_id": 1, "current": 17},
            hass=mock_hass,
        )

        await services.handle_set_current(call)

        site_a_client.set_current.assert_awaited_once_with(
            current=17.0, min_current=6, max_current=32
        )
        coord_a.async_schedule_dashboard_refresh.assert_called_once()
        coord_b.async_schedule_dashboard_refresh.assert_not_called()
        site_b_client.set_current.assert_not_called()


class TestServiceRegistration:
    """Test cases for service registration."""

    @pytest.mark.asyncio
    async def test_register_services(self, mock_hass):
        """Test service registration."""
        await services.register_services(mock_hass)

        # Check that all services were registered
        assert mock_hass.services.async_register.call_count == 6

        # Check specific service registrations
        calls = mock_hass.services.async_register.call_args_list
        service_names = [call[0][1] for call in calls]

        assert "start_charging" in service_names
        assert "pause_charging" in service_names
        assert "stop_charging" in service_names
        assert "resume_charging" in service_names
        assert "set_charging_mode" in service_names
        assert "set_current" in service_names

    @pytest.mark.asyncio
    async def test_unregister_services(self, mock_hass):
        """Test service unregistration."""
        await services.unregister_services(mock_hass)

        # Check that all services were removed
        assert mock_hass.services.async_remove.call_count == 6

        # Check specific service removals
        calls = mock_hass.services.async_remove.call_args_list
        service_names = [call[0][1] for call in calls]

        assert "start_charging" in service_names
        assert "pause_charging" in service_names
        assert "stop_charging" in service_names
        assert "resume_charging" in service_names
        assert "set_charging_mode" in service_names
        assert "set_current" in service_names


class TestServiceSchemas:
    """Test cases for service schemas."""

    def test_start_charging_schema_valid(self):
        """Test start charging schema with valid data."""
        data = {
            "config_entry_id": "test_id",
            "service_location_id": 12345,
            "connector_id": 1,
            "current": 16,
        }

        # Schema should not raise exception
        result = services.START_CHARGING_SCHEMA(data)
        assert result["current"] == 16

    def test_start_charging_schema_minimum_current(self):
        """Test start charging schema with minimum current validation."""
        data = {"current": 3}  # Below minimum of 6

        with pytest.raises(vol.Invalid):
            services.START_CHARGING_SCHEMA(data)

    def test_set_mode_schema_valid(self):
        """Test set mode schema with valid mode."""
        data = {"mode": "smart"}  # Should be converted to uppercase

        result = services.SET_MODE_SCHEMA(data)
        assert result["mode"] == "SMART"

    def test_set_mode_schema_invalid(self):
        """Test set mode schema with invalid mode."""
        data = {"mode": "INVALID"}

        with pytest.raises(vol.Invalid):
            services.SET_MODE_SCHEMA(data)

    def test_pause_stop_schema_valid(self):
        """Test pause/stop schema with valid data."""
        data = {"config_entry_id": "test_id", "service_location_id": 12345, "connector_id": 1}

        # Schema should not raise exception
        result = services.PAUSE_STOP_SCHEMA(data)
        assert result["connector_id"] == 1
