"""Trigger service runtime configuration helpers."""

from __future__ import annotations

import os

_DEFAULT_INTERVAL_S = 30.0


def fire_interval_s() -> float:
    """Background fire-loop poll interval (also used as defer step)."""
    raw = os.environ.get("TRIGGER_FIRE_INTERVAL_S", "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_S
    try:
        return max(5.0, float(raw))
    except ValueError:
        return _DEFAULT_INTERVAL_S


_DEFAULT_DEFER_THRESHOLD = 100


def defer_threshold() -> int:
    """Consecutive predicate defers before ``giw.trigger.defer_degraded``."""
    raw = os.environ.get("TRIGGER_DEFER_THRESHOLD", "").strip()
    if not raw:
        return _DEFAULT_DEFER_THRESHOLD
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_DEFER_THRESHOLD
