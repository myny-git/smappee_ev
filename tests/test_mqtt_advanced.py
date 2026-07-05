# Advanced tests for SmappeeMqtt covering subscriptions, message parsing, heartbeat,
# tracking loop publishing, reconnection backoff, and stop behaviour.
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from typing import Any

import pytest

from custom_components.smappee_ev.api.mqtt_gateway import MQTT_HEARTBEAT_TOPIC_SUFFIX, SmappeeMqtt
from tests.helpers import wait_until


class FakeMsg:
    def __init__(self, topic: str, payload: Any):
        self.topic = type("T", (), {"value": topic})()
        self.payload = payload


class MsgStream:
    def __init__(self):
        self._q: asyncio.Queue[FakeMsg] = asyncio.Queue()
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> FakeMsg:
        if self._closed:
            raise StopAsyncIteration
        msg = await self._q.get()
        if msg is None:  # type: ignore[truthy-bool]
            raise StopAsyncIteration
        return msg

    async def push(self, topic: str, payload: Any):
        await self._q.put(FakeMsg(topic, payload))

    def close(self):
        self._closed = True
        # Drain queue
        with suppress(Exception):
            self._q.put_nowait(FakeMsg("close", "{}"))


class DisconnectingMsgStream:
    """Message stream that drops the MQTT connection when iteration starts."""

    def __aiter__(self):
        return self

    async def __anext__(self) -> FakeMsg:
        from aiomqtt import MqttError  # type: ignore

        raise MqttError("connection lost")


class FakeClient:
    def __init__(self, messages: MsgStream, fail_publish: bool = False):
        self.messages = messages
        self._subs: list[tuple[str, int]] = []
        self.published: list[tuple[str, str]] = []
        self.fail_publish = fail_publish

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def subscribe(self, topic: str, qos: int = 0):  # store subscription
        self._subs.append((topic, qos))

    async def publish(self, topic: str, payload: str, qos: int = 0):
        if self.fail_publish:
            from aiomqtt import MqttError  # type: ignore

            raise MqttError("publish fail")
        self.published.append((topic, payload))


class ClientFactory:
    """Produces FakeClient instances sequentially; first may raise to simulate error."""

    def __init__(self, *clients: FakeClient, raise_first: bool = False):
        self._clients = list(clients)
        self._raise_first = raise_first
        self.calls = 0

    def build(self):
        if self._raise_first and self.calls == 0:
            self.calls += 1
            from aiomqtt import MqttError  # type: ignore

            raise MqttError("connect error")
        if not self._clients:
            raise AssertionError("No more clients queued")
        self.calls += 1
        return self._clients.pop(0)


def _patch_client(monkeypatch, factory: ClientFactory) -> None:
    class PatchedClient:
        def __call__(self, *_, **__):  # called as Client(...)
            return factory.build()

    monkeypatch.setattr("custom_components.smappee_ev.api.mqtt_gateway.Client", PatchedClient())


@pytest.mark.asyncio
async def test_subscriptions_and_message_parsing(monkeypatch):
    stream = MsgStream()
    fc = FakeClient(stream)
    factory = ClientFactory(fc)

    topics_props: list[tuple[str, dict]] = []

    def on_props(t: str, d: dict):
        topics_props.append((t, d))

    conn_states: list[bool] = []

    def on_conn(up: bool):
        conn_states.append(up)

    mqtt = SmappeeMqtt(
        service_location_uuid="slu-1",
        client_id="cid1",
        serial_number="SERIAL1",
        on_properties=on_props,
        service_location_id=123,
        on_connection_change=on_conn,
    )

    # aiomqtt stub already installed via conftest
    _patch_client(monkeypatch, factory)

    # Start
    await mqtt.start()

    # Simulate device state JSON with nested jsonContent structure
    nested_payload = json.dumps(
        {
            "jsonContent": json.dumps({"power": 100, "deviceUUID": "dev-1"}),
            "messageType": "update",
        }
    )
    await stream.push(
        "servicelocation/slu-1/etc/carcharger/acchargingcontroller/v1/devices/ABC/state",
        nested_payload,
    )

    # Heartbeat
    hb_payload = json.dumps({"serviceLocationId": 123})
    await stream.push(f"servicelocation/slu-1{MQTT_HEARTBEAT_TOPIC_SUFFIX}", hb_payload)

    await wait_until(
        lambda: (
            any(d.get("power") == 100 and d.get("deviceUUID") == "dev-1" for _, d in topics_props)
            and any(t.endswith(MQTT_HEARTBEAT_TOPIC_SUFFIX) for t, _ in topics_props)
        )
    )

    # Stop
    await mqtt.stop()

    # Assertions
    # Connection states: up then down
    assert conn_states
    assert conn_states[0] is True
    assert conn_states[-1] is False

    # Subscriptions list populated
    assert any("devices/+/state" in t for t, _ in fc._subs)

    # Parsed payload should have merged deviceUUID
    assert any(d.get("power") == 100 and d.get("deviceUUID") == "dev-1" for _, d in topics_props)

    # Heartbeat callback included
    assert any(t.endswith(MQTT_HEARTBEAT_TOPIC_SUFFIX) for t, _ in topics_props)


@pytest.mark.asyncio
async def test_non_json_regular_payload_is_ignored(monkeypatch):
    stream = MsgStream()
    factory = ClientFactory(FakeClient(stream))
    calls: list[tuple[str, dict]] = []
    mqtt = SmappeeMqtt(
        service_location_uuid="slu-json",
        client_id="cid-json",
        serial_number="SERIAL-JSON",
        on_properties=lambda t, d: calls.append((t, d)),
        service_location_id=123,
    )

    _patch_client(monkeypatch, factory)
    await mqtt.start()

    await stream.push(
        "servicelocation/slu-json/etc/carcharger/acchargingcontroller/v1/devices/ABC/state",
        "not-json",
    )
    await stream.push(
        "servicelocation/slu-json/etc/carcharger/acchargingcontroller/v1/devices/ABC/state",
        json.dumps({"seq": 1}),
    )
    await wait_until(lambda: bool(calls))

    assert calls == [
        (
            "servicelocation/slu-json/etc/carcharger/acchargingcontroller/v1/devices/ABC/state",
            {"seq": 1},
        )
    ]
    await mqtt.stop()


@pytest.mark.asyncio
async def test_broken_nested_json_content_keeps_wrapper_payload(monkeypatch):
    stream = MsgStream()
    factory = ClientFactory(FakeClient(stream))
    calls: list[tuple[str, dict]] = []
    mqtt = SmappeeMqtt(
        service_location_uuid="slu-json",
        client_id="cid-json",
        serial_number="SERIAL-JSON",
        on_properties=lambda t, d: calls.append((t, d)),
        service_location_id=123,
    )

    _patch_client(monkeypatch, factory)
    await mqtt.start()

    topic = "servicelocation/slu-json/etc/carcharger/acchargingcontroller/v1/devices/ABC/state"
    wrapper = {"jsonContent": "{broken", "deviceUUID": "outer-device", "messageType": "update"}
    await stream.push(topic, json.dumps(wrapper))
    await wait_until(lambda: bool(calls))

    assert calls == [(topic, wrapper)]
    await mqtt.stop()


@pytest.mark.asyncio
async def test_nested_json_content_merges_outer_identifiers(monkeypatch):
    stream = MsgStream()
    factory = ClientFactory(FakeClient(stream))
    calls: list[tuple[str, dict]] = []
    mqtt = SmappeeMqtt(
        service_location_uuid="slu-json",
        client_id="cid-json",
        serial_number="SERIAL-JSON",
        on_properties=lambda t, d: calls.append((t, d)),
        service_location_id=123,
    )

    _patch_client(monkeypatch, factory)
    await mqtt.start()

    topic = "servicelocation/slu-json/etc/carcharger/acchargingcontroller/v1/devices/ABC/state"
    await stream.push(
        topic,
        json.dumps(
            {
                "jsonContent": json.dumps({"power": 100}),
                "deviceUUID": "outer-device",
                "messageType": "update",
            }
        ),
    )
    await wait_until(lambda: bool(calls))

    assert calls == [(topic, {"power": 100, "deviceUUID": "outer-device", "messageType": "update"})]
    await mqtt.stop()


@pytest.mark.asyncio
async def test_nested_json_content_preserves_messsage_type_typo(monkeypatch):
    stream = MsgStream()
    factory = ClientFactory(FakeClient(stream))
    calls: list[tuple[str, dict]] = []
    mqtt = SmappeeMqtt(
        service_location_uuid="slu-json",
        client_id="cid-json",
        serial_number="SERIAL-JSON",
        on_properties=lambda t, d: calls.append((t, d)),
        service_location_id=123,
    )

    _patch_client(monkeypatch, factory)
    await mqtt.start()

    topic = "servicelocation/slu-json/etc/carcharger/acchargingcontroller/v1/devices/ABC/state"
    await stream.push(
        topic,
        json.dumps(
            {
                "jsonContent": json.dumps({"power": 101}),
                "deviceUUID": "outer-device",
                "messsageType": "typo-update",
            }
        ),
    )
    await wait_until(lambda: bool(calls))

    assert calls == [
        (
            topic,
            {"power": 101, "deviceUUID": "outer-device", "messsageType": "typo-update"},
        )
    ]
    await mqtt.stop()


@pytest.mark.asyncio
async def test_runner_survives_attribute_error_on_regular_topic(monkeypatch):
    stream = MsgStream()
    fc = FakeClient(stream)
    factory = ClientFactory(fc)

    calls: list[tuple[str, dict]] = []
    processed: list[dict] = []

    def on_props(t: str, d: dict):
        calls.append((t, d))
        if len(calls) == 1:
            raise AttributeError("unexpected payload shape")
        processed.append(d)

    mqtt = SmappeeMqtt(
        service_location_uuid="slu-attr",
        client_id="cid-attr",
        serial_number="SERIAL-ATTR",
        on_properties=on_props,
        service_location_id=123,
    )

    _patch_client(monkeypatch, factory)
    await mqtt.start()

    topic = "servicelocation/slu-attr/etc/carcharger/acchargingcontroller/v1/devices/ABC/state"
    await stream.push(topic, json.dumps({"seq": 1}))
    await wait_until(lambda: len(calls) == 1)
    assert mqtt._runner_task is not None
    assert not mqtt._runner_task.done()

    await stream.push(topic, json.dumps({"seq": 2}))
    await wait_until(lambda: any(payload.get("seq") == 2 for payload in processed))

    await mqtt.stop()


@pytest.mark.asyncio
async def test_runner_survives_attribute_error_on_heartbeat_topic(monkeypatch):
    stream = MsgStream()
    fc = FakeClient(stream)
    factory = ClientFactory(fc)

    calls: list[tuple[str, dict]] = []
    processed: list[dict] = []

    def on_props(t: str, d: dict):
        calls.append((t, d))
        if len(calls) == 1:
            raise AttributeError("unexpected heartbeat payload")
        processed.append(d)

    mqtt = SmappeeMqtt(
        service_location_uuid="slu-hb",
        client_id="cid-hb",
        serial_number="SERIAL-HB",
        on_properties=on_props,
        service_location_id=123,
    )

    _patch_client(monkeypatch, factory)
    await mqtt.start()

    topic = f"servicelocation/slu-hb{MQTT_HEARTBEAT_TOPIC_SUFFIX}"
    await stream.push(topic, json.dumps({"serviceLocationId": 123, "seq": 1}))
    await wait_until(lambda: len(calls) == 1)
    assert mqtt._runner_task is not None
    assert not mqtt._runner_task.done()

    await stream.push(topic, json.dumps({"serviceLocationId": 123, "seq": 2}))
    await wait_until(lambda: any(payload.get("seq") == 2 for payload in processed))

    await mqtt.stop()


@pytest.mark.asyncio
async def test_tracking_and_heartbeat_publish(monkeypatch):
    stream = MsgStream()
    fc = FakeClient(stream)
    factory = ClientFactory(fc)
    published: list[tuple[str, str]] = fc.published

    mqtt = SmappeeMqtt(
        service_location_uuid="slu-2",
        client_id="cid2",
        serial_number="SERIAL2",
        on_properties=lambda *_: None,
        service_location_id="789",  # str to test int conversion
    )

    _patch_client(monkeypatch, factory)
    await mqtt.start()

    await wait_until(
        lambda: (
            any(t.endswith("/tracking") for t, _ in published)
            and any(t.endswith(MQTT_HEARTBEAT_TOPIC_SUFFIX) for t, _ in published)
        )
    )
    await mqtt.stop()

    tracking_topics = [t for t, _ in published if t.endswith("/tracking")]
    assert tracking_topics
    hb_topics = [t for t, _ in published if t.endswith(MQTT_HEARTBEAT_TOPIC_SUFFIX)]
    assert hb_topics

    # Heartbeat payload serviceLocationId numeric conversion
    hb_payloads = [json.loads(p) for t, p in published if t.endswith(MQTT_HEARTBEAT_TOPIC_SUFFIX)]
    assert any(pl.get("serviceLocationId") == 789 for pl in hb_payloads)


@pytest.mark.asyncio
async def test_tracking_task_is_cancelled_when_connection_drops(monkeypatch):
    fc = FakeClient(DisconnectingMsgStream())
    stream2 = MsgStream()
    c2 = FakeClient(stream2)
    factory = ClientFactory(fc, c2)
    events: list[bool] = []

    mqtt = SmappeeMqtt(
        service_location_uuid="slu-drop",
        client_id="cid-drop",
        serial_number="SERIAL-DROP",
        on_properties=lambda *_: None,
        service_location_id=1,
        on_connection_change=lambda up: events.append(up),
    )

    _patch_client(monkeypatch, factory)
    monkeypatch.setattr(
        "custom_components.smappee_ev.api.mqtt_gateway.MQTT_RECONNECT_INITIAL_BACKOFF", 0.01
    )
    monkeypatch.setattr(
        "custom_components.smappee_ev.api.mqtt_gateway.MQTT_RECONNECT_MAX_BACKOFF", 0.02
    )

    await mqtt.start()
    await wait_until(lambda: events[:2] == [True, False])

    assert mqtt._track_task is None
    await mqtt.stop()


@pytest.mark.asyncio
async def test_reconnect_backoff(monkeypatch):
    stream1 = MsgStream()
    c1 = FakeClient(stream1)
    stream2 = MsgStream()
    c2 = FakeClient(stream2)
    # First attempt raises connect error, second returns c1 (success), third returns c2 (unused)
    factory = ClientFactory(c1, c2, raise_first=True)

    events: list[bool] = []
    mqtt = SmappeeMqtt(
        service_location_uuid="slu-3",
        client_id="cid3",
        serial_number="SERIAL3",
        on_properties=lambda *_: None,
        service_location_id=1,
        on_connection_change=lambda up: events.append(up),
    )

    _patch_client(monkeypatch, factory)

    # Shrink backoff constants so retry happens quickly
    monkeypatch.setattr(
        "custom_components.smappee_ev.api.mqtt_gateway.MQTT_RECONNECT_INITIAL_BACKOFF", 0.01
    )
    monkeypatch.setattr(
        "custom_components.smappee_ev.api.mqtt_gateway.MQTT_RECONNECT_MAX_BACKOFF", 0.02
    )

    await mqtt.start()
    await wait_until(lambda: factory.calls >= 1)
    factory._raise_first = False  # allow success on next attempt
    await wait_until(lambda: factory.calls >= 2 and True in events)
    await mqtt.stop()

    # Should have at least one successful connection (True) and final False
    assert True in events
    assert events[-1] is False
    assert factory.calls >= 2
