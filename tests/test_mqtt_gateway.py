# tests/test_mqtt_gateway.py
import asyncio
from unittest.mock import MagicMock

from aiomqtt import MqttError
import pytest

from custom_components.smappee_ev.api.discovery import MqttChannelSpec
from custom_components.smappee_ev.api.mqtt_gateway import SmappeeMqtt, redact_mqtt_topic


@pytest.fixture
def mock_properties_callback():
    """Create a mock properties callback."""
    return MagicMock()


@pytest.fixture
def mock_connection_callback():
    """Create a mock connection change callback."""
    return MagicMock()


@pytest.fixture
def mqtt_gateway(mock_properties_callback, mock_connection_callback):
    """Create a SmappeeMqtt instance for testing."""
    return SmappeeMqtt(
        service_location_uuid="test-uuid",
        client_id="test-client",
        serial_number="TEST123",
        on_properties=mock_properties_callback,
        service_location_id=12345,
        on_connection_change=mock_connection_callback,
    )


class TestSmappeeMqtt:
    """Updated tests matching current SmappeeMqtt implementation (runner_main + tracking_loop)."""

    def test_redact_mqtt_topic_masks_service_location_and_device_uuid(self):
        topic = (
            "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/"
            "devices/aa6a3217-cc6a-44a8-8ff9-1ea67618ec15/property/chargingstate"
        )

        redacted = redact_mqtt_topic(topic)

        assert "site-uuid" not in redacted
        assert "aa6a3217-cc6a-44a8-8ff9-1ea67618ec15" not in redacted
        assert "servicelocation/site****uuid/" in redacted
        assert "devices/aa6a****ec15/property/chargingstate" in redacted

    def test_redact_mqtt_topic_keeps_subscription_wildcards(self):
        topic = "servicelocation/site-uuid/etc/carcharger/acchargingcontroller/v1/devices/+/state"

        redacted = redact_mqtt_topic(topic)

        assert redacted.endswith("/devices/+/state")

    def test_initialization(self, mqtt_gateway, mock_properties_callback, mock_connection_callback):
        assert mqtt_gateway._slu == "test-uuid"
        assert mqtt_gateway._client_id == "test-client"
        assert mqtt_gateway._serial == "TEST123"
        assert mqtt_gateway._on_properties is mock_properties_callback
        assert mqtt_gateway._slu_id == 12345
        assert mqtt_gateway._on_conn is mock_connection_callback
        assert mqtt_gateway._runner_task is None
        assert mqtt_gateway._track_task is None

    @pytest.mark.asyncio
    async def test_start_and_stop_lifecycle(self, mqtt_gateway, monkeypatch):
        # Patch runner_main to a dummy coroutine so we don't attempt network operations
        async def dummy_runner(self, ssl_ctx):
            await self._stop.wait()

        called = {}

        async def wrapper(self, ssl_ctx):
            called["ran"] = True
            await dummy_runner(self, ssl_ctx)

        monkeypatch.setattr(
            "custom_components.smappee_ev.api.mqtt_gateway.SmappeeMqtt._runner_main", wrapper
        )
        await mqtt_gateway.start()
        assert mqtt_gateway._runner_task is not None
        # allow scheduled task to start
        await asyncio.sleep(0)
        await mqtt_gateway.stop()
        assert mqtt_gateway._runner_task is None
        assert called.get("ran") is True

    @pytest.mark.asyncio
    async def test_runner_cleanup_cancels_tracking_task_on_stop(
        self, mock_properties_callback, monkeypatch
    ):
        """Test the real runner tears down subscriptions/tracking when stopped."""
        allow_exit = asyncio.Event()
        connection_events: list[bool] = []

        class WaitingMessages:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await allow_exit.wait()
                raise StopAsyncIteration

        class FakeClient:
            messages = WaitingMessages()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def subscribe(self, *_args, **_kwargs):
                return None

            async def publish(self, *_args, **_kwargs):
                return None

        monkeypatch.setattr(
            "custom_components.smappee_ev.api.mqtt_gateway.Client",
            lambda *_args, **_kwargs: FakeClient(),
        )

        gw = SmappeeMqtt(
            service_location_uuid="u",
            client_id="c",
            serial_number="s",
            on_properties=mock_properties_callback,
            service_location_id=1,
            on_connection_change=connection_events.append,
        )
        runner_task = asyncio.create_task(gw._runner_main(MagicMock()))

        for _ in range(20):
            if gw._track_task is not None:
                break
            await asyncio.sleep(0)

        assert gw._track_task is not None

        gw._stop.set()
        allow_exit.set()
        await asyncio.wait_for(runner_task, timeout=1)

        assert gw._track_task is None
        assert gw._client is None
        assert connection_events == [True, False]

    @pytest.mark.asyncio
    async def test_runner_does_not_reconnect_after_shutdown_request(
        self, mock_properties_callback, monkeypatch, caplog
    ):
        """Runner should not start a reconnect cycle after HA shutdown begins."""
        connection_events: list[bool] = []
        gw = SmappeeMqtt(
            service_location_uuid="u",
            client_id="c",
            serial_number="s",
            on_properties=mock_properties_callback,
            service_location_id=1,
            on_connection_change=connection_events.append,
        )

        class ShutdownMessages:
            def __aiter__(self):
                return self

            async def __anext__(self):
                gw.begin_shutdown()
                raise StopAsyncIteration

        class FakeClient:
            messages = ShutdownMessages()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def subscribe(self, *_args, **_kwargs):
                return None

            async def publish(self, *_args, **_kwargs):
                return None

        monkeypatch.setattr(
            "custom_components.smappee_ev.api.mqtt_gateway.Client",
            lambda *_args, **_kwargs: FakeClient(),
        )

        with caplog.at_level("DEBUG", logger="custom_components.smappee_ev.api.mqtt_gateway"):
            await gw._runner_main(MagicMock())

        assert "MQTT connection loop ended; reconnecting" not in caplog.text
        assert connection_events == [True, False]
        assert gw._track_task is None
        assert gw._client is None

    @pytest.mark.asyncio
    async def test_publish_no_client_no_errors(self, mqtt_gateway):
        # No client set → functions should return silently
        await mqtt_gateway._publish_tracking_once()
        await mqtt_gateway._publish_ha_heartbeat_once()
        # nothing to assert, just no exception

    @pytest.mark.asyncio
    async def test_publish_with_client(self, mqtt_gateway):
        publishes: list[tuple[str, str]] = []

        class FakeClient:
            async def publish(self, topic, payload, qos=0):
                publishes.append((topic, payload))

        mqtt_gateway._client = FakeClient()  # type: ignore[assignment]
        await mqtt_gateway._publish_tracking_once()
        await mqtt_gateway._publish_ha_heartbeat_once()
        assert any("tracking" in t for t, _ in publishes)
        assert any("heartbeat" in t for t, _ in publishes)

    @pytest.mark.asyncio
    async def test_subscribe_all_deduplicates_specs_and_legacy_topics(
        self, mock_properties_callback
    ):
        subscribed: list[str] = []
        topic = "servicelocation/u/power"

        class FakeClient:
            async def subscribe(self, sub_topic, qos=0):
                subscribed.append(sub_topic)

        spec = MqttChannelSpec(1, "grid", "activePower", topic, None, None, [])
        gw = SmappeeMqtt(
            service_location_uuid="u",
            service_location_uuids=["u", "u"],
            client_id="c",
            serial_number="s",
            on_properties=mock_properties_callback,
            service_location_id=1,
            mqtt_specs=[spec, spec],
        )

        await gw._subscribe_all(FakeClient())

        assert subscribed.count(topic) == 1
        assert "servicelocation/u/homeassistant/heartbeat" in subscribed
        assert any(item.endswith("/property/chargingstate") for item in subscribed)

    @pytest.mark.asyncio
    async def test_heartbeat_publish_uses_uuid_specific_ids_and_null_for_bad_ids(
        self, mock_properties_callback
    ):
        publishes: list[tuple[str, str]] = []

        class FakeClient:
            async def publish(self, topic, payload, qos=0):
                publishes.append((topic, payload))

        gw = SmappeeMqtt(
            service_location_uuid="u1",
            service_location_uuids=["u1", "u2"],
            service_location_ids_by_uuid={"u1": "123", "u2": "not-numeric"},
            client_id="c",
            serial_number="s",
            on_properties=mock_properties_callback,
            service_location_id=1,
        )
        gw._client = FakeClient()  # type: ignore[assignment]

        await gw._publish_ha_heartbeat_once()

        assert '"serviceLocationId": 123' in publishes[0][1]
        assert '"serviceLocationId": null' in publishes[1][1]

    def test_connection_callback_error_swallowed(self, mock_properties_callback):
        # Callback that raises should be swallowed by _notify_conn
        errors: list[bool] = []

        def bad_cb(_: bool):
            errors.append(True)
            raise ValueError("boom")

        gw = SmappeeMqtt(
            service_location_uuid="u",
            client_id="c",
            serial_number="s",
            on_properties=mock_properties_callback,
            service_location_id=1,
            on_connection_change=bad_cb,
        )
        # invoke via internal (protected) for coverage
        gw._notify_conn(True)  # type: ignore[attr-defined]
        assert errors == [True]

    @pytest.mark.asyncio
    async def test_runner_notifies_disconnected_on_transient_error(self, monkeypatch):
        events: list[bool] = []
        gw = SmappeeMqtt(
            service_location_uuid="u",
            client_id="c",
            serial_number="s",
            on_properties=MagicMock(),
            service_location_id=1,
            on_connection_change=events.append,
        )

        class FailingClient:
            def __call__(self, *_, **__):
                raise MqttError("connect failed")

        async def stop_during_backoff(awaitable, **_kwargs):
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            gw._stop.set()

        monkeypatch.setattr("custom_components.smappee_ev.api.mqtt_gateway.Client", FailingClient())
        monkeypatch.setattr(
            "custom_components.smappee_ev.api.mqtt_gateway.asyncio.wait_for",
            stop_during_backoff,
        )

        await gw._runner_main(MagicMock())

        assert events
        assert events[0] is False

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, mqtt_gateway, monkeypatch):
        # Start dummy runner
        async def dummy_runner(self, ssl_ctx):
            await self._stop.wait()

        monkeypatch.setattr(
            "custom_components.smappee_ev.api.mqtt_gateway.SmappeeMqtt._runner_main", dummy_runner
        )
        await mqtt_gateway.start()
        await mqtt_gateway.stop()
        # Second stop should not fail
        await mqtt_gateway.stop()

    @pytest.mark.asyncio
    async def test_start_aborts_if_stopped_during_ssl_build(self, mqtt_gateway, monkeypatch):
        """Test start does not create a runner if stop was requested during SSL setup."""

        async def stop_during_to_thread(_func):
            mqtt_gateway._stop.set()
            return MagicMock()

        monkeypatch.setattr(
            "custom_components.smappee_ev.api.mqtt_gateway.asyncio.to_thread",
            stop_during_to_thread,
        )

        await mqtt_gateway.start()

        assert mqtt_gateway._runner_task is None

    def test_properties_without_connection_callback(self):
        gw = SmappeeMqtt(
            service_location_uuid="x",
            client_id="y",
            serial_number="z",
            on_properties=MagicMock(),
            service_location_id=2,
            on_connection_change=None,
        )
        assert gw._on_conn is None

        gw._notify_conn(True)
        assert gw._mqtt_was_connected is None

    def test_text_conversion_fallback_paths(self):
        """Test payload conversion handles broken decode and tobytes-style payloads."""

        class BrokenDecode:
            def decode(self, *_args):
                raise TypeError("bad decode")

        class BytesLike:
            def tobytes(self):
                return b"bytes-like"

        class BrokenBytesLike:
            def tobytes(self):
                raise ValueError("bad bytes")

        assert "BrokenDecode" in SmappeeMqtt._to_text(BrokenDecode())
        assert SmappeeMqtt._to_text(BytesLike()) == "bytes-like"
        assert "BrokenBytesLike" in SmappeeMqtt._to_text(BrokenBytesLike())

    def test_connection_transition_logging_state(self, mqtt_gateway):
        """Test connection transition tracking only changes when state changes."""
        mqtt_gateway._log_mqtt_connection_transition(True)
        assert mqtt_gateway._mqtt_was_connected is True

        mqtt_gateway._log_mqtt_connection_transition(True)
        assert mqtt_gateway._mqtt_was_connected is True

        mqtt_gateway._log_mqtt_connection_transition(False, MqttError("down"), 1)
        assert mqtt_gateway._mqtt_was_connected is False

        mqtt_gateway._log_mqtt_connection_transition(False, MqttError("still down"), 2)
        assert mqtt_gateway._mqtt_was_connected is False

    def test_task_done_callbacks_clear_tasks_and_swallow_errors(
        self, mqtt_gateway, mock_connection_callback
    ):
        """Test startup/runner done callbacks handle cancellation and failures."""
        startup_task = MagicMock()
        startup_task.cancelled.return_value = False
        startup_task.result.side_effect = RuntimeError("startup failed")
        mqtt_gateway._start_task = startup_task

        mqtt_gateway._startup_done(startup_task)

        assert mqtt_gateway._start_task is None

        cancelled_task = MagicMock()
        cancelled_task.cancelled.return_value = True
        mqtt_gateway._startup_done(cancelled_task)

        runner_task = MagicMock()
        runner_task.cancelled.return_value = False
        runner_task.result.side_effect = RuntimeError("runner failed")
        mqtt_gateway._runner_task = runner_task

        mqtt_gateway._runner_done(runner_task)

        assert mqtt_gateway._runner_task is None
        mock_connection_callback.assert_called_with(False)

    @pytest.mark.asyncio
    async def test_start_ssl_context_failure_notifies_disconnected(self, mqtt_gateway, monkeypatch):
        """Test start reports disconnected if SSL context creation fails."""
        monkeypatch.setattr(
            "custom_components.smappee_ev.api.mqtt_gateway.asyncio.to_thread",
            MagicMock(side_effect=RuntimeError("ssl failed")),
        )

        await mqtt_gateway.start()

        assert mqtt_gateway._runner_task is None
        mqtt_gateway._on_conn.assert_called_with(False)

    @pytest.mark.asyncio
    async def test_stop_cancels_tracked_start_task(self, mqtt_gateway):
        """Test stop cancels a HA-created startup task."""
        start_task = asyncio.create_task(asyncio.sleep(60))
        mqtt_gateway.track_start_task(start_task)

        await mqtt_gateway.stop()

        assert mqtt_gateway._start_task is None
        assert start_task.cancelled()

    @pytest.mark.asyncio
    async def test_tracking_loop_handles_cancellation(self, mqtt_gateway):
        """Test tracking loop exits cleanly when cancelled."""
        task = asyncio.create_task(mqtt_gateway._tracking_loop())
        await asyncio.sleep(0)

        await mqtt_gateway._cancel_and_wait(task)

        assert task.done()

    @pytest.mark.asyncio
    async def test_publish_heartbeat_nonnumeric_location_and_publish_errors(self):
        """Test heartbeat publishes null for nonnumeric location and suppresses MQTT errors."""
        publishes: list[tuple[str, str]] = []

        class FakeClient:
            async def publish(self, topic, payload, qos=0):
                publishes.append((topic, payload))

        gw = SmappeeMqtt(
            service_location_uuid="u",
            client_id="c",
            serial_number="s",
            on_properties=MagicMock(),
            service_location_id="not-numeric",
        )
        gw._client = FakeClient()  # type: ignore[assignment]

        await gw._publish_ha_heartbeat_once()

        assert '"serviceLocationId": null' in publishes[0][1]

        class FailingClient:
            async def publish(self, *_args, **_kwargs):
                raise MqttError("publish failed")

        gw._client = FailingClient()  # type: ignore[assignment]
        await gw._publish_tracking_once()
        await gw._publish_ha_heartbeat_once()
