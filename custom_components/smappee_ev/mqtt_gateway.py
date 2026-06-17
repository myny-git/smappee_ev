# custom_components/smappee_ev/mqtt_gateway.py
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import json
import logging
import re
import ssl
from typing import Any, cast

from aiomqtt import Client, MqttError

from .const import (
    MQTT_HEARTBEAT_TOPIC_SUFFIX,
    MQTT_HOST,
    MQTT_PORT_TLS,
    MQTT_QOS_AT_LEAST_ONCE,
    MQTT_RECONNECT_INITIAL_BACKOFF,
    MQTT_RECONNECT_MAX_BACKOFF,
    MQTT_TRACK_INTERVAL_SEC,
    MQTT_TRACKING_TYPE_RT_VALUES,
)
from .helpers import anonymize_uuid

_LOGGER = logging.getLogger(__name__)
_TOPIC_SECRET_RE = re.compile(r"(servicelocation/)([^/]+)(/)")


def redact_mqtt_topic(topic: str) -> str:
    """Redact service-location UUIDs from MQTT topics using anonymization."""

    def replacer(match: re.Match) -> str:
        # Group 1: 'servicelocation/'
        # Group 2: UUID
        # Group 3: '/'
        prefix = match.group(1)
        uuid = match.group(2)
        suffix = match.group(3)

        return f"{prefix}{anonymize_uuid(uuid)}{suffix}"

    return _TOPIC_SECRET_RE.sub(replacer, topic)


class SmappeeMqtt:
    """Lightweight MQTT client for Smappee live updates."""

    def __init__(
        self,
        *,
        service_location_uuid: str | None,
        client_id: str,
        serial_number: str,
        on_properties: Callable[[str, dict], None],
        service_location_id: int | str,
        service_location_uuids: list[str] | None = None,
        service_location_ids_by_uuid: dict[str, int | str] | None = None,
        on_connection_change: Callable[[bool], None] | None = None,
        mqtt_specs: list[Any] | None = None,
    ) -> None:
        slus = service_location_uuids or ([service_location_uuid] if service_location_uuid else [])
        self._slus = tuple(dict.fromkeys(str(slu) for slu in slus if slu))
        self._slu = self._slus[0] if self._slus else service_location_uuid
        self._slu_ids = service_location_ids_by_uuid or {}
        self._client_id = client_id
        self._serial = serial_number
        self._on_properties = on_properties
        self._slu_id = service_location_id
        self._on_conn = on_connection_change
        self._mqtt_specs = mqtt_specs or []
        self._mqtt_username = self._first_spec_attr("username") or service_location_uuid
        self._mqtt_password = self._first_spec_attr("password") or service_location_uuid

        self._client: Client | None = None
        self._stop = asyncio.Event()
        self._start_task: asyncio.Task | None = None
        self._runner_task: asyncio.Task | None = None
        self._track_task: asyncio.Task | None = None
        self._mqtt_was_connected: bool | None = None

    # ---------- helpers ----------

    def _first_spec_attr(self, attr: str) -> str | None:
        for spec in self._mqtt_specs:
            value = getattr(spec, attr, None)
            if value:
                return str(value)
        return None

    def _spec_topic(self, spec: Any) -> str | None:
        topic = getattr(spec, "topic", None)
        if topic is None and isinstance(spec, dict):
            topic = spec.get("topic")
        text = str(topic).strip() if topic is not None else ""
        return text or None

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

    @staticmethod
    async def _cancel_and_wait(task: asyncio.Task) -> None:
        """Cancel a task and wait until cancellation has propagated."""
        task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(task)

    async def _subscribe_all(self, client: Client) -> None:
        """(Re)subscribe all topics after connect/reconnect."""
        topics: list[str] = []
        for spec in self._mqtt_specs:
            topic = self._spec_topic(spec)
            if topic:
                topics.append(topic)
        for slu in self._slus:
            topics.extend(
                [
                    f"servicelocation/{slu}/etc/carcharger/acchargingcontroller/v1/devices/+/state",
                    f"servicelocation/{slu}/etc/carcharger/acchargingcontroller/v1/devices/+/property/chargingstate",
                    f"servicelocation/{slu}/etc/carcharger/acchargingcontroller/v1/devices/updated",
                    f"servicelocation/{slu}/etc/led/acledcontroller/v1/devices/updated",
                    f"servicelocation/{slu}{MQTT_HEARTBEAT_TOPIC_SUFFIX}",
                    f"servicelocation/{slu}/power",
                ]
            )
        topics = list(dict.fromkeys(topics))
        for t in topics:
            await client.subscribe(t, qos=MQTT_QOS_AT_LEAST_ONCE)

    def _notify_conn(self, up: bool) -> None:
        cb = self._on_conn
        if not cb:
            return
        try:
            cb(up)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError) as err:
            _LOGGER.debug("on_connection_change callback error: %s", err)

    def _log_mqtt_connection_transition(
        self, up: bool, err: BaseException | None = None, backoff: float | None = None
    ) -> None:
        """Log MQTT availability only when it changes."""
        if self._mqtt_was_connected is up:
            if not up and err is not None and backoff is not None:
                _LOGGER.debug("MQTT reconnect attempt failed: %s (retry in %.0fs)", err, backoff)
            return

        self._mqtt_was_connected = up
        if up:
            _LOGGER.info("MQTT connected to %s:%s", MQTT_HOST, MQTT_PORT_TLS)
        elif err is not None and backoff is not None:
            _LOGGER.info("MQTT disconnected/error: %s; will retry in %.0fs", err, backoff)
        else:
            _LOGGER.info("MQTT disconnected")

    def track_start_task(self, task: asyncio.Task) -> None:
        """Track the HA-created startup task so stop() can cancel it during unload."""
        self._start_task = task
        task.add_done_callback(self._startup_done)

    def _startup_done(self, task: asyncio.Task) -> None:
        if self._start_task is task:
            self._start_task = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("MQTT startup failed: %s", err)

    def _runner_done(self, task: asyncio.Task) -> None:
        if self._runner_task is task:
            self._runner_task = None
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("MQTT runner stopped unexpectedly: %s", err)
            self._notify_conn(False)

    async def _runner_main(self, ssl_ctx: ssl.SSLContext) -> None:
        """Maintain connection with auto-reconnect."""
        backoff = MQTT_RECONNECT_INITIAL_BACKOFF
        max_backoff = MQTT_RECONNECT_MAX_BACKOFF

        try:
            while not self._stop.is_set():
                try:
                    async with Client(
                        hostname=MQTT_HOST,
                        port=MQTT_PORT_TLS,
                        username=self._mqtt_username,
                        password=self._mqtt_password,
                        identifier=self._client_id,  # aiomqtt v2.x
                        tls_context=ssl_ctx,
                        clean_session=True,
                        keepalive=MQTT_TRACK_INTERVAL_SEC,
                    ) as client:
                        self._client = client
                        self._log_mqtt_connection_transition(True)
                        self._notify_conn(True)

                        # Success → reset backoff
                        backoff = MQTT_RECONNECT_INITIAL_BACKOFF

                        # (Re)subscribe all topics
                        await self._subscribe_all(client)

                        # First tracking ping en periodically
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
                            _LOGGER.debug(
                                "MQTT RX %s (%d bytes)",
                                redact_mqtt_topic(topic_str),
                                len(payload_raw),
                            )

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
                                    "Non-JSON MQTT payload on %s",
                                    redact_mqtt_topic(topic_str),
                                )
                                continue

                            if topic_str.endswith(MQTT_HEARTBEAT_TOPIC_SUFFIX):
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
                    break
                except (MqttError, OSError, TimeoutError) as err:
                    self._log_mqtt_connection_transition(False, err, backoff)
                    self._notify_conn(False)

                    if self._track_task:
                        await self._cancel_and_wait(self._track_task)
                        self._track_task = None
                    self._client = None

                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    backoff = min(backoff * 2.0, max_backoff)

                finally:
                    if self._track_task:
                        await self._cancel_and_wait(self._track_task)
                        self._track_task = None
                    self._client = None
                    if self._stop.is_set():
                        _LOGGER.info("MQTT stopped")
                        self._notify_conn(False)
                    else:
                        self._log_mqtt_connection_transition(False)
                        self._notify_conn(False)
                        _LOGGER.debug("MQTT connection loop ended; reconnecting")
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

        try:
            ssl_ctx = await _build_ssl_ctx()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("MQTT startup failed: %s", err)
            self._notify_conn(False)
            return

        # stop() may have been called while the SSL context was being built.
        if self._stop.is_set():
            _LOGGER.debug("MQTT start aborted: stop() was called during SSL context build")
            return

        self._runner_task = asyncio.create_task(
            self._runner_main(ssl_ctx), name="smappee-mqtt-runner"
        )
        self._runner_task.add_done_callback(self._runner_done)

    async def stop(self) -> None:
        """Stop loops and disconnect."""
        self._stop.set()
        start_task = self._start_task
        if start_task is not None:
            await self._cancel_and_wait(start_task)
            if self._start_task is start_task:
                self._start_task = None
        runner_task = self._runner_task
        if runner_task is not None:
            await self._cancel_and_wait(runner_task)
            if self._runner_task is runner_task:
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
        if not client or not self._slus:
            return
        for slu in self._slus:
            topic = f"servicelocation/{slu}/tracking"
            payload = {
                "value": "ON",
                "clientId": self._client_id,
                "serialNumber": self._serial,
                "type": MQTT_TRACKING_TYPE_RT_VALUES,
            }
            with suppress(MqttError):
                await client.publish(topic, json.dumps(payload), qos=0)
                _LOGGER.debug("MQTT tracking published to %s", redact_mqtt_topic(topic))

    async def _publish_ha_heartbeat_once(self) -> None:
        client = self._client
        if not client or not self._slus:
            return
        for slu in self._slus:
            topic = f"servicelocation/{slu}{MQTT_HEARTBEAT_TOPIC_SUFFIX}"
            value: int | str | None = self._slu_ids.get(slu, self._slu_id)
            with suppress(TypeError, ValueError):
                if isinstance(value, str):
                    value = int(value)
            if not isinstance(value, (int, float)):
                _LOGGER.debug(
                    "Heartbeat serviceLocationId not numeric (slu_id=%r); sending null", value
                )
                value = None
            payload = {"serviceLocationId": value}
            with suppress(MqttError):
                await client.publish(topic, json.dumps(payload), qos=0)
                _LOGGER.debug(
                    "MQTT HA heartbeat published to %s",
                    redact_mqtt_topic(topic),
                )
