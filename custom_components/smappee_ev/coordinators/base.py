"""Shared base helpers for coordinator mixins."""

from __future__ import annotations

from typing import Any


class CoordinatorMixin:
    """Allow mixins to reference runtime attributes supplied by the coordinator."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)
