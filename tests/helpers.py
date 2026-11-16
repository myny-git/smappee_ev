"""Shared helpers for integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
import inspect
from typing import Any

import pytest

from custom_components.smappee_ev import sensor
from custom_components.smappee_ev.const import DOMAIN


async def wait_until(
    condition: Callable[[], bool | Awaitable[bool]],
    *,
    timeout_seconds: float = 2.0,
    interval: float = 0.01,
) -> None:
    """Poll until a condition is true or the timeout expires."""
    async with asyncio.timeout(timeout_seconds):
        while True:
            result: Any = condition()
            if inspect.isawaitable(result):
                result = await result
            if result:
                return
            await asyncio.sleep(interval)


@contextmanager
def raises_translated(
    exc_type: type[BaseException], translation_key: str
) -> Iterator[pytest.ExceptionInfo[Any]]:
    """Expect an exception whose user-facing message comes from strings.json."""
    with pytest.raises(exc_type) as caught:
        yield caught
    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == translation_key


def site_sensor(key: str) -> Callable[..., sensor.SiteMeasurementSensor]:
    """Build a plain site measurement sensor by its description key."""
    description = next(item for item in sensor.SITE_MEASUREMENTS if item.key == key)

    def build(
        coordinator: Any, _api: Any, sid: int, _station_uuid: str
    ) -> sensor.SiteMeasurementSensor:
        return sensor.SiteMeasurementSensor(coordinator, sid, description)

    return build


def connector_sensor(key: str) -> Callable[..., sensor.ConnectorMeasurementSensor]:
    """Build a plain connector measurement sensor by its description key."""
    description = next(item for item in sensor.CONNECTOR_MEASUREMENTS if item.key == key)

    def build(
        coordinator: Any, api: Any, sid: int, station_uuid: str, connector_uuid: str
    ) -> sensor.ConnectorMeasurementSensor:
        return sensor.ConnectorMeasurementSensor(
            coordinator, api, sid, station_uuid, connector_uuid, description
        )

    return build
