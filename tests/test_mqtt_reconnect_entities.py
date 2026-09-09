"""Reconnect regression through transport, routing, REST and HA entity state."""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiomqtt import MqttError
from homeassistant.helpers.entity_component import EntityComponent
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.smappee_ev.api.errors import SmappeeConnectionError
from custom_components.smappee_ev.mqtt_setup import _setup_mqtt
from custom_components.smappee_ev.number import SmappeeMinSurplusPctNumber
from custom_components.smappee_ev.sensor import ConnectorPowerSensor
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


@pytest.mark.parametrize("reported_surplus", [None, 60])
async def test_mqtt_reconnect_restores_connector_entities_without_reload(
    hass, entry, online, monkeypatch, reported_surplus
):
    """Exercise the real reconnect loop and entity listeners with fake remote I/O."""
    bucket = online.sites[1].stations["station-1"]
    coord = bucket.station_coordinator
    client = bucket.connectors["connector-1"].connector_client
    entry.runtime_data = online
    cloud_up = True

    async def smartdevice(_device_id):
        if not cloud_up:
            raise SmappeeConnectionError("simulated internet outage")
        if reported_surplus is None:
            return {"configurationProperties": []}
        return {
            "configurationProperties": [
                {
                    "spec": {"name": "etc.smart.device.type.car.charger.config.min.excesspct"},
                    "value": {"value": reported_surplus},
                }
            ],
        }

    client.async_get_smartdevice = AsyncMock(side_effect=smartdevice)
    bucket.station_client.async_get_smartdevices = AsyncMock(return_value=[])
    # Dashboard enrichment is outside this connector REST regression.
    coord.dashboard_client = None
    coord.data.connectors["connector-1"].min_surpluspct = 40
    coord.data.connectors["connector-1"].power_total = 1000
    power = ConnectorPowerSensor(coord, client, 1, "station-1", "connector-1")
    surplus = SmappeeMinSurplusPctNumber(coord, client, 1, "station-1", "connector-1")
    power.entity_id = "sensor.reconnect_power"
    surplus.entity_id = "number.reconnect_surplus"
    power_component = EntityComponent(logging.getLogger(__name__), "sensor", hass)
    number_component = EntityComponent(logging.getLogger(__name__), "number", hass)
    power_component._platforms["sensor"].config_entry = entry
    number_component._platforms["number"].config_entry = entry
    await power_component.async_add_entities([power])
    await number_component.async_add_entities([surplus])

    incoming = [asyncio.Queue(), asyncio.Queue()]
    connected = [asyncio.Event(), asyncio.Event()]
    disconnected = asyncio.Event()
    network_restored = asyncio.Event()
    clients = []

    class BrokerClient:
        def __init__(self, **_kwargs):
            self.index = len(clients)
            self.subscribe = AsyncMock()
            self.publish = AsyncMock()
            self.messages = self
            clients.append(self)

        async def __aenter__(self):
            if self.index:
                await network_restored.wait()
            connected[self.index].set()
            return self

        async def __aexit__(self, *_args):
            if self.index == 0:
                disconnected.set()
            return

        def __aiter__(self):
            return self

        async def __anext__(self):
            message = await incoming[self.index].get()
            if isinstance(message, Exception):
                raise message
            return message

    monkeypatch.setattr("custom_components.smappee_ev.api.mqtt_gateway.Client", BrokerClient)
    monkeypatch.setattr(
        "custom_components.smappee_ev.api.mqtt_gateway.MQTT_RECONNECT_INITIAL_BACKOFF", 0.01
    )
    mqtt = _setup_mqtt(
        hass,
        suuid="site-uuid",
        serial_str="gateway",
        sid=1,
        stations={"station-1": bucket},
        client_id_prefix="reconnect-test",
        update_interval=30,
        start_clients=False,
    )
    with patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock) as reload_entry:
        mqtt._runner_task = asyncio.create_task(mqtt._runner_main(MagicMock()))
        try:
            await asyncio.wait_for(connected[0].wait(), 2)
            await hass.async_block_till_done()
            assert hass.states.get(power.entity_id).state == "1000.0"
            assert hass.states.get(surplus.entity_id).state == "40"

            cloud_up = False
            await incoming[0].put(MqttError("simulated disconnect"))
            # The disconnect callback starts the real REST safety-net refresh.
            await asyncio.wait_for(disconnected.wait(), 2)
            await hass.async_block_till_done()
            assert coord.data.station.mqtt_connected is False
            assert power.available is False
            assert hass.states.get(power.entity_id).state == "unavailable"
            assert hass.states.get(surplus.entity_id).state == "unavailable"
            assert surplus.native_value == 40

            cloud_up = True
            network_restored.set()
            await asyncio.wait_for(connected[1].wait(), 2)
            await incoming[1].put(
                SimpleNamespace(
                    topic=bootstrap.TOPIC,
                    payload=json.dumps({"activePowerData": [0, 2300, 0]}).encode(),
                )
            )
            async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=35))
            await hass.async_block_till_done(wait_background_tasks=True)
            assert coord.mqtt_transport_connected is True
            assert hass.states.get(power.entity_id).state == "2300.0"
            assert hass.states.get(surplus.entity_id).state == str(reported_surplus or 40)
            assert entry.runtime_data is online
            assert bucket.station_coordinator is coord
            assert len(clients) == 2
            assert clients[0].subscribe.call_args_list == clients[1].subscribe.call_args_list
            reload_entry.assert_not_awaited()
        finally:
            await mqtt.stop()
            await power_component._async_reset()
            await number_component._async_reset()
