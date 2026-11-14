"""Behavioural regression tests for Dashboard discovery and MQTT credential grouping.

These tests pin down what setup does when the Smappee Dashboard answers with
empty, missing or failing data: whether Home Assistant retries, starts reauth,
reports maintenance, or skips a location.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientError
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import pytest

from custom_components.smappee_ev.api.discovery import MqttChannelSpec
from custom_components.smappee_ev.api.errors import (
    SmappeeAuthenticationError,
    SmappeeConnectionError,
    SmappeeMaintenanceError,
    SmappeeServerError,
)
from custom_components.smappee_ev.dashboard_discovery import (
    _dashboard_fetch_devices,
    _load_dashboard_topologies,
)
from custom_components.smappee_ev.mqtt_specs import _group_mqtt_specs_by_credentials

DISCOVERY_LOGGER = "custom_components.smappee_ev.dashboard_discovery"


def _dashboard(**methods) -> MagicMock:
    """Return a Dashboard client double that counts as configured."""
    dashboard = MagicMock()
    dashboard._token = "token"  # noqa: S105 - fake test token
    dashboard.refresh_token = None
    dashboard.username = None
    dashboard.password = None
    for name, value in methods.items():
        setattr(dashboard, name, value)
    return dashboard


def _unconfigured_dashboard() -> MagicMock:
    dashboard = _dashboard()
    dashboard._token = None
    return dashboard


# ---------------------------------------------------------------------------
# Setup loaders: retry vs. reauth vs. maintenance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        SmappeeAuthenticationError("reauth required"),
        SmappeeMaintenanceError("maintenance"),
        SmappeeConnectionError("connection lost"),
        SmappeeServerError("502"),
    ],
    ids=["auth", "maintenance", "connection", "server"],
)
async def test_setup_loaders_propagate_auth_and_outage_errors_unchanged(error):
    """Auth failures and Dashboard outages must reach setup with their own type.

    Prevents: wrapping them in ConfigEntryNotReady, which would hide the reauth
    flow behind endless retries and lose the "Dashboard under maintenance"
    retry reason shown to the user.
    """
    dashboard = _dashboard(async_get_service_locations_full_details=AsyncMock(side_effect=error))

    with pytest.raises(type(error)) as caught:
        await _load_dashboard_topologies(dashboard)

    assert caught.value is error
    assert not isinstance(caught.value, ConfigEntryNotReady)
    if isinstance(error, SmappeeAuthenticationError):
        assert isinstance(caught.value, ConfigEntryAuthFailed)


async def test_setup_loaders_retry_when_account_has_nothing_to_set_up():
    """An account without candidate charging locations must make setup retry.

    Prevents: setup succeeding with zero devices (an entry that silently does
    nothing) or failing permanently while the Dashboard is still provisioning
    a new station.
    """
    dashboard = _dashboard(async_get_service_locations_full_details=AsyncMock(return_value=[]))

    with pytest.raises(ConfigEntryNotReady) as caught:
        await _load_dashboard_topologies(dashboard)

    assert caught.value.translation_key == "no_charging_stations"


async def test_topology_loader_retries_when_dashboard_is_not_configured():
    """Missing Dashboard credentials must lead to a retry, not an empty setup.

    Prevents: an entry without (yet) usable credentials loading with no stations.
    """
    with pytest.raises(ConfigEntryNotReady) as caught:
        await _load_dashboard_topologies(_unconfigured_dashboard())

    assert caught.value.translation_key == "dashboard_not_configured"


async def test_topology_loader_treats_missing_location_list_as_retryable_failure():
    """A ``None`` location list from the Dashboard is a failed call, not "no sites".

    Prevents: a broken Dashboard answer being reported as "no charging stations".
    """
    dashboard = _dashboard(async_get_service_locations_full_details=AsyncMock(return_value=None))

    with pytest.raises(ConfigEntryNotReady) as caught:
        await _load_dashboard_topologies(dashboard)

    assert caught.value.translation_key == "cannot_load_topology"
    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize(
    "error",
    [ClientError("dashboard unreachable"), ValueError("dashboard unreachable")],
    ids=["network", "malformed-payload"],
)
async def test_discovery_logs_warning_and_retries_on_dashboard_error(caplog, error):
    """A network/payload error during discovery is logged and leads to a retry.

    Prevents: the failure disappearing from the log (only a vague "not ready"
    remaining) or being treated as a permanent setup error.
    """
    dashboard = _dashboard(async_get_service_locations_full_details=AsyncMock(side_effect=error))

    with (
        caplog.at_level(logging.WARNING, logger=DISCOVERY_LOGGER),
        pytest.raises(ConfigEntryNotReady) as caught,
    ):
        await _load_dashboard_topologies(dashboard)

    assert caught.value.translation_key == "cannot_load_topology"
    assert caught.value.translation_placeholders == {"error": "dashboard unreachable"}
    assert caught.value.__cause__ is error
    warnings = [
        record
        for record in caplog.records
        if record.name == DISCOVERY_LOGGER and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "topology discovery failed" in warnings[0].getMessage()
    assert "dashboard unreachable" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Smart device discovery
# ---------------------------------------------------------------------------


async def test_smart_device_discovery_treats_none_answer_as_failure():
    """A ``None`` smart-device answer must raise, never mean "no devices here".

    Prevents: a hiccup in the Dashboard (answering without a body) being read
    as an empty device list, which would build the site without stations and
    connectors and make their entities disappear until the next restart.
    """
    dashboard = _dashboard(async_get_smart_devices=AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="returned no data"):
        await _dashboard_fetch_devices(dashboard, 42)


async def test_smart_device_discovery_keeps_genuinely_empty_list():
    """A real empty list is a valid answer and must be returned as such.

    Prevents: the ``None`` guard being widened to falsy values, which would turn
    monitor-only locations (no smart devices) into setup failures.
    """
    dashboard = _dashboard(async_get_smart_devices=AsyncMock(return_value=[]))

    assert await _dashboard_fetch_devices(dashboard, 42) == []


# ---------------------------------------------------------------------------
# MQTT credential grouping
# ---------------------------------------------------------------------------


def _spec(topic: str, *, sid: int = 1) -> MqttChannelSpec:
    return MqttChannelSpec(
        service_location_id=sid,
        role="grid",
        metric="activePower",
        topic=topic,
        username=None,
        password=None,
        aspect_paths=[],
    )


@pytest.mark.parametrize(
    "topic",
    ["", "servicelocation/null/power", "custom/prefix/power"],
    ids=["empty-topic", "null-uuid", "foreign-prefix"],
)
def test_mqtt_specs_without_topic_uuid_route_to_site_uuid(topic):
    """Specs whose topic carries no usable UUID use the site UUID as credentials.

    A "null" path segment from the API is not a UUID. Prevents: an MQTT client
    logging in as "null" (or with no credentials) and subscribing to a
    service location that does not exist, so live values never arrive.
    """
    groups = _group_mqtt_specs_by_credentials("site-uuid", [_spec(topic, sid=99)])

    assert len(groups) == 1
    specs, service_location_uuids, ids_by_uuid = groups[0]
    assert [spec.topic for spec in specs] == [topic]
    assert service_location_uuids == ["site-uuid"]
    assert ids_by_uuid == {"site-uuid": 99}


def test_mqtt_specs_with_and_without_topic_uuid_share_site_credentials():
    """A UUID-less topic joins the site's own credential group.

    Prevents: opening a second MQTT connection with the same credentials for
    the same site (duplicate client id clash / double message handling).
    """
    specs = [
        _spec("servicelocation/site-uuid/power", sid=99),
        _spec("servicelocation/null/current", sid=99),
    ]

    groups = _group_mqtt_specs_by_credentials("site-uuid", specs)

    assert len(groups) == 1
    assert [spec.topic for spec in groups[0][0]] == [spec.topic for spec in specs]
    assert groups[0][1] == ["site-uuid"]
