"""Env-resolved charter supervisor intervals.

Kept separate from ``tick_loop`` so the supervisor module does not grow further
past the modularization yellow band when env knobs are wired.
"""

from __future__ import annotations

import os

_DEFAULT_TICK_INTERVAL_S = 20.0
_ENV_TICK_INTERVAL_S = "CHARTER_TICK_INTERVAL_S"
_DEFAULT_RECONCILE_INTERVAL_S = 300.0
_ENV_RECONCILE_INTERVAL_S = "CHARTER_RECONCILE_INTERVAL_S"
_MIN_RECONCILE_INTERVAL_S = 60.0


def _float_from_env(name: str, default: float, *, floor: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(floor, float(raw))
    except ValueError:
        return default


def tick_interval_from_env() -> float:
    """Resolve ``CHARTER_TICK_INTERVAL_S`` (legacy hold-heartbeat fallback).

    Empty or non-float values fall back to ``_DEFAULT_TICK_INTERVAL_S``; values
    below 5.0s clamp to 5.0 so a mis-set env cannot busy-loop manage.
    """
    return _float_from_env(_ENV_TICK_INTERVAL_S, _DEFAULT_TICK_INTERVAL_S, floor=5.0)


def reconcile_interval_from_env() -> float:
    """Resolve ``CHARTER_RECONCILE_INTERVAL_S`` for WakeConsumer floor sweeps."""
    return _float_from_env(
        _ENV_RECONCILE_INTERVAL_S,
        _DEFAULT_RECONCILE_INTERVAL_S,
        floor=_MIN_RECONCILE_INTERVAL_S,
    )


DEFAULT_TICK_INTERVAL_S = _DEFAULT_TICK_INTERVAL_S
ENV_TICK_INTERVAL_S = _ENV_TICK_INTERVAL_S
DEFAULT_RECONCILE_INTERVAL_S = _DEFAULT_RECONCILE_INTERVAL_S
ENV_RECONCILE_INTERVAL_S = _ENV_RECONCILE_INTERVAL_S

__all__ = [
    "DEFAULT_RECONCILE_INTERVAL_S",
    "DEFAULT_TICK_INTERVAL_S",
    "ENV_RECONCILE_INTERVAL_S",
    "ENV_TICK_INTERVAL_S",
    "reconcile_interval_from_env",
    "tick_interval_from_env",
]
