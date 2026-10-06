"""Real aiomqtt/Mosquitto smoke test; run separately from the HA pytest suite.

Docker starts a disposable broker on localhost with a randomly assigned port.
Only TLS is disabled in the test client; production TLS settings stay unchanged.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch
from uuid import uuid4

from aiomqtt import Client, MqttError

from custom_components.smappee_ev.api import mqtt_gateway

BROKER_IMAGE = "eclipse-mosquitto:2.0.22"
POWER_TOPIC = "servicelocation/test-site/power"
STATE_TOPIC = (
    "servicelocation/test-site/etc/carcharger/acchargingcontroller/v1/devices/test-connector/state"
)
TRACKING_TOPIC = "servicelocation/test-site/tracking"


class TestMqttTransport(unittest.IsolatedAsyncioTestCase):
    """Exercise subscription, payload handling, reconnect and shutdown."""

    # aiomqtt needs add_reader(), including when this is run on Windows.
    loop_factory = asyncio.SelectorEventLoop

    async def asyncSetUp(self):
        self.docker = shutil.which("docker")
        if self.docker is None:
            self.fail("This smoke test requires Docker with a running daemon")
        self.container = f"smappee-mqtt-test-{uuid4().hex}"
        self.gateway = None
        self.patches = ExitStack()
        self.addAsyncCleanup(self._cleanup)
        config = Path(__file__).with_name("mosquitto.conf").resolve()
        await self._docker(
            "run",
            "--detach",
            "--name",
            self.container,
            "--publish",
            "127.0.0.1::1883",
            "--mount",
            f"type=bind,src={config},dst=/mosquitto/config/mosquitto.conf,readonly",
            BROKER_IMAGE,
        )
        self.port = await self._broker_port()
        await self._wait_for_broker()
        self.received = asyncio.Queue()
        self.connections = asyncio.Queue()
        self.connection_history = []

        def local_client(**kwargs):
            kwargs["tls_context"] = None
            kwargs["timeout"] = 3
            return Client(**kwargs)

        for name, value in (
            ("Client", local_client),
            ("MQTT_HOST", "127.0.0.1"),
            ("MQTT_PORT_TLS", self.port),
            ("MQTT_RECONNECT_INITIAL_BACKOFF", 0.2),
            ("MQTT_RECONNECT_MAX_BACKOFF", 0.4),
        ):
            self.patches.enter_context(patch.object(mqtt_gateway, name, value))

    async def _docker(self, *args, check=True):
        def run():
            # The executable comes from PATH; arguments target our own container.
            return subprocess.run(  # noqa: S603
                [self.docker, *args], check=check, capture_output=True, text=True, timeout=30
            ).stdout.strip()

        return await asyncio.to_thread(run)

    async def _broker_port(self):
        address = await self._docker("port", self.container, "1883/tcp")
        return int(address.rsplit(":", 1)[1])

    async def _wait_for_broker(self):
        async with asyncio.timeout(15):
            while True:
                try:
                    async with Client("127.0.0.1", port=self.port, timeout=1):
                        return
                except (MqttError, OSError):
                    await asyncio.sleep(0.1)

    def _on_properties(self, topic, payload):
        if topic in (POWER_TOPIC, STATE_TOPIC):
            self.received.put_nowait((topic, payload))

    def _on_connection(self, up):
        self.connection_history.append(up)
        self.connections.put_nowait(up)

    async def _wait_connection(self, expected):
        async with asyncio.timeout(15):
            while await self.connections.get() is not expected:
                pass

    async def _receive(self):
        return await asyncio.wait_for(self.received.get(), 15)

    async def _cleanup(self):
        try:
            if self.gateway is not None:
                await asyncio.wait_for(self.gateway.stop(), 5)
        finally:
            self.patches.close()
            await self._docker("rm", "--force", self.container, check=False)

    async def test_messages_reconnect_and_shutdown(self):
        self.gateway = mqtt_gateway.SmappeeMqtt(
            service_location_uuid="test-site",
            client_id=f"smappee-test-{uuid4().hex}",
            serial_number="test-gateway",
            service_location_id=1,
            on_properties=self._on_properties,
            on_connection_change=self._on_connection,
        )
        async with Client("127.0.0.1", port=self.port) as publisher:
            # Retained data published before startup proves the subscription.
            await publisher.publish(POWER_TOPIC, '{"power":0}', qos=1, retain=True)
            await publisher.subscribe(TRACKING_TOPIC, qos=1)
            await self.gateway.start()
            await self._wait_connection(True)
            self.assertEqual(await self._receive(), (POWER_TOPIC, {"power": 0}))
            tracking = await asyncio.wait_for(anext(publisher.messages), 15)
            self.assertEqual(json.loads(bytes(tracking.payload))["value"], "ON")

            # Invalid JSON is ignored; an empty object reaches the application,
            # whose measurement-level validation is covered by the unit suite.
            await publisher.publish(POWER_TOPIC, "not JSON", qos=1)
            await publisher.publish(POWER_TOPIC, "{}", qos=1)
            self.assertEqual(await self._receive(), (POWER_TOPIC, {}))
            for sequence in range(10):
                topic = STATE_TOPIC if sequence % 2 else POWER_TOPIC
                await publisher.publish(topic, json.dumps({"sequence": sequence}), qos=1)
            burst = [await self._receive() for _ in range(10)]
            self.assertEqual(sorted(payload["sequence"] for _, payload in burst), list(range(10)))
            for topic, payload in burst:
                self.assertEqual(topic, STATE_TOPIC if payload["sequence"] % 2 else POWER_TOPIC)

            envelope = {"jsonContent": '{"connectionStatus":"CONNECTED"}', "deviceUUID": "c"}
            await publisher.publish(STATE_TOPIC, json.dumps(envelope), qos=1)
            self.assertEqual(
                await self._receive(),
                (STATE_TOPIC, {"connectionStatus": "CONNECTED", "deviceUUID": "c"}),
            )

        await self._docker("stop", "--time", "1", self.container)
        await self._wait_connection(False)
        self.assertIsNotNone(self.gateway._runner_task)
        await self._docker("start", self.container)
        self.port = await self._broker_port()
        mqtt_gateway.MQTT_PORT_TLS = self.port
        await self._wait_for_broker()
        await self._wait_connection(True)
        async with Client("127.0.0.1", port=self.port) as publisher:
            # Retain also covers the brief gap before re-subscription completes.
            await publisher.publish(POWER_TOPIC, '{"power":3200}', qos=1, retain=True)
            self.assertEqual(await self._receive(), (POWER_TOPIC, {"power": 3200}))

        # Shutdown must interrupt reconnect backoff and leave no background task.
        await self._docker("stop", "--time", "1", self.container)
        await self._wait_connection(False)
        await asyncio.wait_for(self.gateway.stop(), 5)
        self.assertIsNone(self.gateway._runner_task)
        self.assertIsNone(self.gateway._track_task)
        history = list(self.connection_history)
        await asyncio.sleep(0.5)
        self.assertEqual(self.connection_history, history)
