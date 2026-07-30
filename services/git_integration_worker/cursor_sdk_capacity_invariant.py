"""Pure capacity invariant helpers for cursor-sdk (I1 scaffold — unwired at boot)."""

from __future__ import annotations

from typing import Literal


def evaluate_i1(
    standard_limit: int,
    operator_limit: int,
    headroom: int,
) -> Literal["ok", "clamp"]:
    """Return ``clamp`` when configured lane capacity exceeds write headroom."""
    if standard_limit + operator_limit <= headroom:
        return "ok"
    return "clamp"
