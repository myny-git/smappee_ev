# custom_components/smappee_ev/mqtt_gateway.py
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
import logging
import ssl
from typing import cast

from aiomqtt import Client, MqttError

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
        self._runner_task: asyncio.Task | None = None
        self._track_task: asyncio.Task | None = None

    @staticmethod
    def _to_text(raw: object) -> str:
        dec = getattr(raw, "decode", None)
        if callable(dec):
            try:
                return cast(str, dec("utf-8", "ignore"))
            except (TypeError, UnicodeDecodeError) as err:
                _LOGGER.debug("decode() failed on payload: %s", err)

        tob = getattr(raw, "tobytes", None)
        if callable(tob):
            try:
                b = tob()  # expected: bytes
                return cast(bytes, b).decode("utf-8", "ignore")
            except (TypeError, AttributeError, ValueError, UnicodeDecodeError) as err:
                _LOGGER.debug("tobytes()/decode failed on payload: %s", err)

        if isinstance(raw, str):
            return raw

        return str(raw)

    async def start(self) -> None:
        """Start the MQTT client, subscribe, and begin tracking."""
        self._stop.clear()

        ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_ctx.check_hostname = True
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED

        async def _runner() -> None:
            try:
                async with Client(
                    hostname=MQTT_HOST,
                    port=MQTT_PORT_TLS,
                    username=self._slu,
                    password=self._slu,
                    identifier=self._client_id,
                    tls_context=ssl_ctx,
                    clean_session=True,
                    keepalive=MQTT_TRACK_INTERVAL_SEC,
                ) as client:
                    self._client = client
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
                    await client.subscribe(
                        f"servicelocation/{self._slu}/etc/carcharger/acchargingcontroller/v1/devices/updated",
                        qos=1,
                    )
                    await client.subscribe(
                        f"servicelocation/{self._slu}/etc/led/acledcontroller/v1/devices/updated",
                        qos=1,
                    )
                    # Optional:
                    # await client.subscribe(f"servicelocation/{self._slu}/power", qos=1)

                    await self._publish_tracking_once()

                    self._track_task = asyncio.create_task(
                        self._tracking_loop(), name="smappee-mqtt-tracking"
                    )

                    async for msg in client.messages:
                        if self._stop.is_set():
                            break

                        topic_str = (
                            msg.topic.value if hasattr(msg.topic, "value") else str(msg.topic)
                        )
                        payload_raw = self._to_text(msg.payload)

                        _LOGGER.debug("MQTT RX %s: %s", topic_str, payload_raw[:400])

                        # JSON + jsonContent wrapper
                        try:
                            payload = json.loads(payload_raw)
                            if isinstance(payload, dict) and "jsonContent" in payload:
                                payload = json.loads(payload["jsonContent"])
                        except json.JSONDecodeError:
                            _LOGGER.debug("Non-JSON MQTT payload on %s: %r", topic_str, payload_raw)
                            continue

                        try:
                            self._on_properties(topic_str, payload)
                        except (RuntimeError, ValueError, TypeError, KeyError) as err:
                            _LOGGER.warning("on_properties raised: %s", err)

            except asyncio.CancelledError:
                pass
            except MqttError as err:
                _LOGGER.error("MQTT error: %s", err)
            finally:
                if self._track_task:
                    self._track_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._track_task
                    self._track_task = None

                self._client = None
                _LOGGER.info("MQTT stopped")

        self._runner_task = asyncio.create_task(_runner(), name="smappee-mqtt-runner")

    async def stop(self) -> None:
        """Stop loops and disconnect."""
        self._stop.set()
        if self._runner_task:
            self._runner_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None

    async def _tracking_loop(self) -> None:
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
