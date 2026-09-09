"""Stargate continuity subsystem."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "continuity_router":
        from .route import router as continuity_router

        return continuity_router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["continuity_router"]
