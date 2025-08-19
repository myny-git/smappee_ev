from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
import logging
import ssl

from asyncio_mqtt import Client, MqttError

from .const import MQTT_HOST, MQTT_PORT_TLS, MQTT_TRACK_INTERVAL_SEC

_LOGGER = logging.getLogger(__name__)


class SmappeeMqtt:
    """Lightweight MQTT client for Smappee live updates."""

    def __init__(
        self,
        *,
        service_location_uuid: str,
        client_id: str,
        serial_number: str,
        on_properties: Callable[[str, dict], None],
    ) -> None:
        self._slu = service_location_uuid
        self._client_id = client_id
        self._serial = serial_number
        self._on_properties = on_properties
        self._client: Client | None = None
        self._stop = asyncio.Event()
        self._consume_task: asyncio.Task | None = None
        self._track_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Connect, subscribe, and start tracking."""
        ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_ctx.check_hostname = True
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        client = Client(
            hostname=MQTT_HOST,
            port=MQTT_PORT_TLS,
            username=self._slu,
            password=self._slu,
            client_id=self._client_id,
            tls_context=ssl_ctx,
            clean_session=True,
            keepalive=MQTT_TRACK_INTERVAL_SEC,
        )
        self._client = client

        try:
            await client.connect()
            _LOGGER.info("MQTT connected to %s:%s", MQTT_HOST, MQTT_PORT_TLS)

            # Subscriptions
            await client.subscribe(
                f"servicelocation/{self._slu}/etc/carcharger/acchargingcontroller/v1/devices/+/state",
                qos=1,
            )
            await client.subscribe(
                f"servicelocation/{self._slu}/etc/carcharger/acchargingcontroller/v1/devices/+/property/chargingstate",
                qos=1,
            )
            await client.subscribe(f"servicelocation/{self._slu}/power", qos=1)
            await client.subscribe(f"servicelocation/{self._slu}/etc/led/#", qos=1)

            await client.subscribe(
                f"servicelocation/{self._slu}/etc/carcharger/acchargingcontroller/v1/devices/updated",
                qos=1,
            )

            # Background tasks
            self._consume_task = asyncio.create_task(
                self._consume_loop(), name="smappee-mqtt-consume"
            )
            self._track_task = asyncio.create_task(
                self._tracking_loop(), name="smappee-mqtt-tracking"
            )
        except MqttError as err:
            _LOGGER.error("MQTT connect failed: %s", err)
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop loops and disconnect."""
        self._stop.set()

        tasks = [t for t in (self._consume_task, self._track_task) if t]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._consume_task = None
        self._track_task = None

        client = self._client
        self._client = None
        if client:
            with suppress(Exception):
                await client.disconnect()
        _LOGGER.info("MQTT stopped")

    async def _consume_loop(self) -> None:
        client = self._client
        if not client:
            return

        try:
            async with client.unfiltered_messages() as messages:
                async for msg in messages:
                    if self._stop.is_set():
                        break
                    payload_raw = msg.payload.decode("utf-8", "ignore")
                    try:
                        payload = json.loads(payload_raw)
                        if isinstance(payload, dict) and "jsonContent" in payload:
                            payload = json.loads(payload["jsonContent"])
                    except json.JSONDecodeError:
                        _LOGGER.debug("Non-JSON MQTT payload on %s: %r", msg.topic, payload_raw)
                        continue

                    self._on_properties(msg.topic, payload)
        except asyncio.CancelledError:
            return
        except MqttError as err:
            _LOGGER.warning("MQTT consume error: %s", err)

    async def _tracking_loop(self) -> None:
        """Publish RT_VALUES tracking ping every minute (keeps live feed active)."""
        try:
            while not self._stop.is_set():
                await self._publish_tracking_once()
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=MQTT_TRACK_INTERVAL_SEC)
        except asyncio.CancelledError:
            return

    async def _publish_tracking_once(self) -> None:
        client = self._client
        if not client:
            return
        topic = f"servicelocation/{self._slu}/tracking"
        payload = {
            "value": "ON",
            "clientId": self._client_id,
            "serialNumber": self._serial,
            "type": "RT_VALUES",
        }
        with suppress(MqttError):
            await client.publish(topic, json.dumps(payload), qos=0)
            _LOGGER.debug("MQTT tracking published")
