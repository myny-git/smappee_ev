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
        service_location_id: int | str,
        on_connection_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._slu = service_location_uuid
        self._client_id = client_id
        self._serial = serial_number
        self._on_properties = on_properties
        self._slu_id = service_location_id
        self._on_conn = on_connection_change

        self._client: Client | None = None
        self._stop = asyncio.Event()
        self._runner_task: asyncio.Task | None = None
        self._track_task: asyncio.Task | None = None

    # ---------- helpers ----------

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

    async def _subscribe_all(self, client: Client) -> None:
        """(Re)subscribe all topics after connect/reconnect."""
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

        await client.subscribe(
            f"servicelocation/{self._slu}/homeassistant/heartbeat",
            qos=1,
        )
        # Optional: servicelocation power feed
        await client.subscribe(
            f"servicelocation/{self._slu}/power",
            qos=1,
        )

    def _notify_conn(self, up: bool) -> None:
        cb = self._on_conn
        if not cb:
            return
        try:
            cb(up)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError) as err:
            _LOGGER.debug("on_connection_change callback error: %s", err)

    async def _runner_main(self, ssl_ctx: ssl.SSLContext) -> None:
        """Maintain connection with auto-reconnect."""
        backoff = 1.0  # seconds
        max_backoff = 60.0

        try:
            while not self._stop.is_set():
                try:
                    async with Client(
                        hostname=MQTT_HOST,
                        port=MQTT_PORT_TLS,
                        username=self._slu,
                        password=self._slu,
                        identifier=self._client_id,  # aiomqtt v2.x
                        tls_context=ssl_ctx,
                        clean_session=True,
                        keepalive=MQTT_TRACK_INTERVAL_SEC,
                    ) as client:
                        self._client = client
                        _LOGGER.info("MQTT connected to %s:%s", MQTT_HOST, MQTT_PORT_TLS)
                        self._notify_conn(True)

                        # Success → reset backoff
                        backoff = 1.0

                        # (Re)subscribe all topics
                        await self._subscribe_all(client)  # RESUBSCRIBE

                        # First tracking ping en periodically
                        await self._publish_tracking_once()
                        self._track_task = asyncio.create_task(
                            self._tracking_loop(), name="smappee-mqtt-tracking"
                        )

                        # Consume loop

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
                                    try:
                                        inner = json.loads(payload["jsonContent"])
                                    except json.JSONDecodeError:
                                        inner = None
                                    if isinstance(inner, dict):
                                        for k in ("deviceUUID", "messageType", "messsageType"):
                                            if k in payload:
                                                inner.setdefault(k, payload[k])
                                        payload = inner
                            except json.JSONDecodeError:
                                _LOGGER.debug(
                                    "Non-JSON MQTT payload on %s: %r", topic_str, payload_raw
                                )
                                continue

                            if topic_str.endswith("/homeassistant/heartbeat"):
                                self._notify_conn(True)
                                try:
                                    self._on_properties(
                                        topic_str,
                                        payload
                                        if isinstance(payload, dict)
                                        else {"raw": payload_raw},
                                    )
                                except (
                                    RuntimeError,
                                    ValueError,
                                    TypeError,
                                    KeyError,
                                    AttributeError,
                                ) as err:
                                    _LOGGER.debug("on_properties (heartbeat) raised: %s", err)
                                continue

                            try:
                                self._on_properties(topic_str, payload)
                            except (RuntimeError, ValueError, TypeError, KeyError) as err:
                                _LOGGER.warning("on_properties raised: %s", err)

                except asyncio.CancelledError:
                    # stop()
                    break
                except (MqttError, OSError, TimeoutError) as err:
                    _LOGGER.warning("MQTT disconnected/error: %s (retry in %.0fs)", err, backoff)
                    self._notify_conn(False)

                    if self._track_task:
                        self._track_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await self._track_task
                        self._track_task = None
                    self._client = None

                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    backoff = min(backoff * 2.0, max_backoff)

                finally:
                    if self._track_task:
                        self._track_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await self._track_task
                        self._track_task = None
                    self._client = None
                    _LOGGER.info("MQTT stopped (looping=%s)", not self._stop.is_set())
                    if self._stop.is_set():
                        self._notify_conn(False)

        finally:
            self._client = None

    async def start(self) -> None:
        """Start the MQTT client, subscribe, and begin tracking."""
        self._stop.clear()

        # Build SSL context off the event loop (create_default_context, and possibly
        # set_default_verify_paths / load_default_certs are blocking).
        async def _build_ssl_ctx() -> ssl.SSLContext:
            def _mk() -> ssl.SSLContext:
                ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
                # Any attribute that might touch cert paths stays inside the thread too
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                ctx.check_hostname = True
                ctx.verify_mode = ssl.CERT_REQUIRED
                return ctx

            return await asyncio.to_thread(_mk)

        ssl_ctx = await _build_ssl_ctx()

        self._runner_task = asyncio.create_task(
            self._runner_main(ssl_ctx), name="smappee-mqtt-runner"
        )

    async def stop(self) -> None:
        """Stop loops and disconnect."""
        self._stop.set()
        if self._runner_task:
            self._runner_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None

    async def _tracking_loop(self) -> None:
        """Publish RT_VALUES tracking ping every minute."""
        try:
            while not self._stop.is_set():
                await self._publish_tracking_once()
                await self._publish_ha_heartbeat_once()
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

    async def _publish_ha_heartbeat_once(self) -> None:
        client = self._client
        if not client:
            return
        topic = f"servicelocation/{self._slu}/homeassistant/heartbeat"
        value: int | str | None = self._slu_id
        try:
            # best-effort integer if str
            if isinstance(value, str):
                value = int(value)
        except (TypeError, ValueError):
            # keep as-is
            pass
        payload = {"serviceLocationId": value}
        with suppress(MqttError):
            await client.publish(topic, json.dumps(payload), qos=0)
            _LOGGER.debug("MQTT HA heartbeat published to %s: %s", topic, payload)
