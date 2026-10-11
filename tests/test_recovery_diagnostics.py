"""Recovery progress is accurate, serializable and free of exception text."""

import asyncio
from datetime import UTC, datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.exceptions import ConfigEntryNotReady
import pytest

from custom_components.smappee_ev.api.errors import (
    SmappeeConnectionError,
    SmappeeMaintenanceError,
    SmappeeProtocolError,
    SmappeeRateLimitError,
    SmappeeServerError,
)
from custom_components.smappee_ev.diagnostics import async_get_config_entry_diagnostics
from custom_components.smappee_ev.mqtt_recovery import _async_recover
from tests import test_mqtt_bootstrap as bootstrap

entry = bootstrap.entry
online = bootstrap.online


@pytest.mark.parametrize(
    ("error_cls", "category"),
    [
        (SmappeeConnectionError, "connection"),
        (SmappeeMaintenanceError, "maintenance"),
        (SmappeeServerError, "server"),
        (SmappeeProtocolError, "protocol"),
        (ConfigEntryNotReady, "incomplete"),
        (ValueError, "incomplete"),
        (RuntimeError, "unexpected"),
        (SmappeeRateLimitError, "rate_limit"),
    ],
)
async def test_recovery_diagnostics_record_failures_retry_deadlines_and_cancellation(
    hass, entry, online, error_cls, category
):
    entry.runtime_data = online
    private_text = "PRIVATE_RESPONSE_SHOULD_NOT_APPEAR"
    error = error_cls(1200) if error_cls is SmappeeRateLimitError else error_cls(private_text)
    sleeps = 0

    async def prepare(*_args):
        assert online.recovery.status == "probing"
        assert online.recovery.attempts == 1
        assert online.recovery.last_attempt.tzinfo is UTC
        assert online.recovery.next_attempt is None
        raise error

    async def advance(delay):
        nonlocal sleeps
        sleeps += 1
        assert online.recovery.status == "waiting"
        assert online.recovery.next_attempt.tzinfo is UTC
        remaining = (online.recovery.next_attempt - datetime.now(UTC)).total_seconds()
        assert delay - 1 <= remaining <= delay
        if sleeps == 2:
            assert delay == (1200 if category == "rate_limit" else 60)
            diagnostics = await async_get_config_entry_diagnostics(hass, entry)
            info = diagnostics["recovery"]
            assert info["attempts"] == 1
            assert info["last_error_category"] == category
            assert info["last_attempt"] == online.recovery.last_attempt.isoformat()
            assert info["next_attempt"] == online.recovery.next_attempt.isoformat()
            assert private_text not in json.dumps(diagnostics)
            raise asyncio.CancelledError

    clock = SimpleNamespace(**{**vars(asyncio), "sleep": advance})
    with (
        patch("custom_components.smappee_ev.mqtt_recovery.asyncio", clock),
        patch("custom_components.smappee_ev.mqtt_recovery.random.uniform", return_value=1),
        patch(
            "custom_components.smappee_ev.mqtt_recovery._create_dashboard_client",
            return_value=online.api,
        ),
        patch(
            "custom_components.smappee_ev.diagnostics.async_get_integration",
            new=AsyncMock(return_value=SimpleNamespace(version="test")),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await _async_recover(hass, entry, online, prepare)

    assert online.recovery.status == "stopped"
    assert online.recovery.next_attempt is None
    assert online.recovery.last_error_category == category
    assert online.recovery.attempts == 1


async def test_recovery_diagnostics_unwrap_expected_failure_causes(hass, entry, online):
    from custom_components.smappee_ev.mqtt_recovery import _recovery_error_category

    wrapped = ConfigEntryNotReady("topology failed")
    wrapped.__cause__ = SmappeeConnectionError("private server response")
    assert _recovery_error_category(wrapped) == "connection"
    # Even a malformed cyclic exception chain must not hang diagnostics.
    wrapped.__cause__ = wrapped
    assert _recovery_error_category(wrapped) == "incomplete"
