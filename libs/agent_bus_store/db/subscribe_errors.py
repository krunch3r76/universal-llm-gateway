"""Subscribe refusal types for the endpoint subscription journal."""

from __future__ import annotations

from typing import Any


class SubscribeError(Exception):
    """Refused subscribe. ``code`` is the HTTP detail code."""

    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.extra = extra


__all__ = ["SubscribeError"]
