# tests/test_mqtt_simple.py
"""Simple tests for SmappeeMqtt."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.smappee_ev.mqtt_gateway import SmappeeMqtt


class TestSmappeeMqttBasic:
    """Basic test cases for SmappeeMqtt class."""

    def test_initialization(self):
        """Test SmappeeMqtt initialization."""
        mqtt_gateway = SmappeeMqtt(
            service_location_uuid="test_uuid",
            client_id="test_client",
            serial_number="TEST123",
            on_properties=MagicMock(),
            service_location_id=12345,
        )

        assert mqtt_gateway._slu == "test_uuid"
        assert mqtt_gateway._client_id == "test_client"
        assert mqtt_gateway._serial == "TEST123"
        assert mqtt_gateway._slu_id == 12345
        assert mqtt_gateway._client is None

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        """Test that start creates a background task."""
        mqtt_gateway = SmappeeMqtt(
            service_location_uuid="test_uuid",
            client_id="test_client",
            serial_number="TEST123",
            on_properties=MagicMock(),
            service_location_id=12345,
        )

        # Mock the _runner_main method to avoid actual MQTT connection
        mqtt_gateway._runner_main = AsyncMock()

        await mqtt_gateway.start()

        assert mqtt_gateway._runner_task is not None
        assert not mqtt_gateway._runner_task.done()

        # Cleanup
        await mqtt_gateway.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        """Test that stop cancels the background task."""
        mqtt_gateway = SmappeeMqtt(
            service_location_uuid="test_uuid",
            client_id="test_client",
            serial_number="TEST123",
            on_properties=MagicMock(),
            service_location_id=12345,
        )

        # Mock the _runner_main method
        mqtt_gateway._runner_main = AsyncMock()

        await mqtt_gateway.start()
        await mqtt_gateway.stop()

        assert mqtt_gateway._stop.is_set()

    def test_text_conversion(self):
        """Test _to_text helper method."""
        # Test string input
        result = SmappeeMqtt._to_text("test_string")
        assert result == "test_string"

        # Test bytes input
        result = SmappeeMqtt._to_text(b"test_bytes")
        assert result == "test_bytes"

        # Test other types
        result = SmappeeMqtt._to_text(123)
        assert result == "123"
