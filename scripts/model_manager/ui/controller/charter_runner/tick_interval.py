"""Env-resolved charter tick interval (``CHARTER_TICK_INTERVAL_S``).

Kept separate from ``tick_loop`` so the supervisor module does not grow further
past the modularization yellow band when the env knob is wired.
"""

from __future__ import annotations

import os

_DEFAULT_TICK_INTERVAL_S = 20.0
_ENV_TICK_INTERVAL_S = "CHARTER_TICK_INTERVAL_S"


def tick_interval_from_env() -> float:
    """Resolve ``CHARTER_TICK_INTERVAL_S`` for the supervisor sleep.

    Empty or non-float values fall back to ``_DEFAULT_TICK_INTERVAL_S``; values
    below 5.0s clamp to 5.0 so a mis-set env cannot busy-loop manage.
    """
    raw = os.environ.get(_ENV_TICK_INTERVAL_S, "").strip()
    if not raw:
        return _DEFAULT_TICK_INTERVAL_S
    try:
        return max(5.0, float(raw))
    except ValueError:
        return _DEFAULT_TICK_INTERVAL_S


DEFAULT_TICK_INTERVAL_S = _DEFAULT_TICK_INTERVAL_S
ENV_TICK_INTERVAL_S = _ENV_TICK_INTERVAL_S

__all__ = [
    "DEFAULT_TICK_INTERVAL_S",
    "ENV_TICK_INTERVAL_S",
    "tick_interval_from_env",
]
