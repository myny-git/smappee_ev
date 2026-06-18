"""Shared helpers for integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import inspect
from typing import Any


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
