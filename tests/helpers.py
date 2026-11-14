"""Shared helpers for integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
import inspect
from typing import Any

import pytest

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
