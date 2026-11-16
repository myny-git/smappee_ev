"""Published MQTT connection state on site and station coordinators."""

import logging
from unittest.mock import MagicMock

from homeassistant.core import CoreState

from custom_components.smappee_ev.api.device_handle import SmappeeDeviceHandle
from custom_components.smappee_ev.coordinator import (
    SmappeeSiteCoordinator,
    SmappeeStationCoordinator,
)
from custom_components.smappee_ev.models.state import (
    ConnectorState,
    IntegrationData,
    SiteData,
    SiteState,
    StationState,
)


def _station_coordinator(hass) -> SmappeeStationCoordinator:
    station_client = MagicMock(spec=SmappeeDeviceHandle)
    station_client.service_location_id = 200
    station_client.site_location_id = 100
    station_client.serial = "SERIAL"
    station_client.charging_station_serial = "STATION-1"
    connector_one = MagicMock(spec=SmappeeDeviceHandle)
    connector_one.connector_number = 1
    connector_two = MagicMock(spec=SmappeeDeviceHandle)
    connector_two.connector_number = 2
    coord = SmappeeStationCoordinator(
        hass,
        station_client=station_client,
        connector_clients={"conn-1": connector_one, "conn-2": connector_two},
        update_interval=60,
    )
    coord.data = IntegrationData(
        station=StationState(),
        connectors={
            "conn-1": ConnectorState(connector_number=1),
            "conn-2": ConnectorState(connector_number=2),
        },
    )
    return coord


def test_site_mqtt_connection_change_handles_no_data_and_up_down_transitions(hass, caplog):
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
    )
    coord.async_update_listeners = MagicMock()

    coord.apply_mqtt_connection_change(True)
    coord.async_update_listeners.assert_not_called()

    coord.data = SiteData(site=SiteState())
    caplog.set_level(logging.INFO, logger="custom_components.smappee_ev.coordinator")
    coord.apply_mqtt_connection_change(True)
    assert coord.data.site.mqtt_connected is True
    assert coord.data.site.last_mqtt_rx is not None
    coord.async_update_listeners.assert_called_once_with()
    assert "MQTT availability recovered" in caplog.text

    caplog.clear()
    coord.async_update_listeners.reset_mock()
    coord.apply_mqtt_connection_change(True)
    coord.async_update_listeners.assert_not_called()
    assert "MQTT availability recovered" not in caplog.text

    caplog.clear()
    coord.apply_mqtt_connection_change(False)
    assert coord.data.site.mqtt_connected is False
    coord.async_update_listeners.assert_called_once_with()
    assert caplog.text.count("MQTT unavailable") == 1

    caplog.clear()
    coord.async_update_listeners.reset_mock()
    coord.apply_mqtt_connection_change(False)
    coord.async_update_listeners.assert_not_called()
    assert "MQTT unavailable" not in caplog.text


def test_station_mqtt_connection_change_logs_only_on_transitions(hass, caplog):
    coord = _station_coordinator(hass)
    coord.async_update_listeners = MagicMock()
    caplog.set_level(logging.INFO, logger="custom_components.smappee_ev.coordinator")

    coord.apply_mqtt_connection_change(True)
    assert coord.data.station.mqtt_connected is True
    coord.async_update_listeners.assert_called_once_with()
    assert caplog.text.count("Station MQTT availability recovered") == 1

    caplog.clear()
    coord.async_update_listeners.reset_mock()
    coord.apply_mqtt_connection_change(True)
    coord.async_update_listeners.assert_not_called()
    assert "Station MQTT availability recovered" not in caplog.text

    caplog.clear()
    coord.apply_mqtt_connection_change(False)
    assert coord.data.station.mqtt_connected is False
    coord.async_update_listeners.assert_called_once_with()
    assert caplog.text.count("Station MQTT unavailable") == 1

    caplog.clear()
    coord.async_update_listeners.reset_mock()
    coord.apply_mqtt_connection_change(False)
    coord.async_update_listeners.assert_not_called()
    assert "Station MQTT unavailable" not in caplog.text


def test_mqtt_unavailable_during_shutdown_is_debug_only(hass, caplog):
    """Stopping Home Assistant closes MQTT on purpose; that is not worth an INFO line."""
    station = _station_coordinator(hass)
    station.async_update_listeners = MagicMock()
    site = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
    )
    site.async_update_listeners = MagicMock()
    site.data = SiteData(site=SiteState(mqtt_connected=True))
    station.data.station.mqtt_connected = True
    caplog.set_level(logging.DEBUG, logger="custom_components.smappee_ev")

    previous_state = hass.state
    hass.set_state(CoreState.stopping)
    try:
        site.apply_mqtt_connection_change(False)
        station.apply_mqtt_connection_change(False)
    finally:
        hass.set_state(previous_state)

    unavailable = [r for r in caplog.records if "MQTT unavailable" in r.getMessage()]
    assert len(unavailable) == 2
    assert all(r.levelno == logging.DEBUG for r in unavailable)


def test_site_apply_mqtt_properties_without_data_is_safe(hass):
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
    )
    coord.async_update_listeners = MagicMock()

    coord.apply_mqtt_properties("servicelocation/site/power", {"channelData": [1]})

    coord.async_update_listeners.assert_not_called()


def test_site_apply_mqtt_properties_does_not_notify_on_heartbeat_only(hass):
    coord = SmappeeSiteCoordinator(
        hass,
        site_location_id=100,
        site_name="Home",
        site_uuid="site-uuid",
        gateway_serial="GATEWAY",
        gateway_type="Genius",
        update_interval=60,
    )
    coord.data = SiteData(site=SiteState(mqtt_connected=True, last_mqtt_rx=1.0))
    coord.async_update_listeners = MagicMock()

    coord.apply_mqtt_properties("/homeassistant/heartbeat", {})

    assert coord.data.site.mqtt_connected is True
    assert coord.data.site.last_mqtt_rx > 1.0
    coord.async_update_listeners.assert_not_called()
