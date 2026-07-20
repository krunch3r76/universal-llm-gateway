"""
Eviction cooldown class policy, constants, and oscillation tracking.

Defines REQUIRED vs OPPORTUNISTIC request classes, hard-floor seconds, and
helpers for remaining cooldown / override eligibility during planning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from model_id import ModelId

    from ..types import Gateway

COOLDOWN_HARD_FLOOR_S = 10.0
DEFAULT_EVICTION_COOLDOWN_S = 120.0
COOLDOWN_OVERRIDE_OSCILLATION_WINDOW_S = 120.0


class EvictionRequestClass(StrEnum):
    """Eviction class: required for admission vs opportunistic reclaim only."""

    REQUIRED = "required"
    OPPORTUNISTIC = "opportunistic"


def remaining_cooldown_s(
    gateway: Gateway,
    model_id: ModelId,
    eviction_cooldown_s: float,
    *,
    now: float | None = None,
) -> float:
    """Seconds remaining before ``model_id`` exits load-cooldown on ``gateway``."""
    clock = time.monotonic() if now is None else now
    elapsed = clock - gateway.model_loaded_at.get(model_id, 0.0)
    return max(0.0, eviction_cooldown_s - elapsed)


def cooldown_override_eligible(
    remaining_s: float,
    *,
    hard_floor_s: float = COOLDOWN_HARD_FLOOR_S,
) -> bool:
    """Required eviction may override cooldown only above the configured hard floor."""
    return remaining_s > hard_floor_s


@dataclass(frozen=True)
class CooldownOverrideKey:
    gateway_id: str
    victim_model_id: str


_override_timestamps: dict[CooldownOverrideKey, float] = {}


def record_cooldown_override(
    key: CooldownOverrideKey,
    *,
    now: float | None = None,
) -> None:
    """Record that a cooldown override was applied for anti-thrash bookkeeping."""
    _override_timestamps[key] = time.monotonic() if now is None else now


def oscillation_blocks_override(
    key: CooldownOverrideKey,
    *,
    window_s: float = COOLDOWN_OVERRIDE_OSCILLATION_WINDOW_S,
    now: float | None = None,
) -> bool:
    """Return True when the same victim was overridden inside the oscillation window."""
    clock = time.monotonic() if now is None else now
    prior = _override_timestamps.get(key)
    if prior is None:
        return False
    return (clock - prior) < window_s


def clear_cooldown_override_tracker() -> None:
    """Reset override timestamps (tests only)."""
    _override_timestamps.clear()
