"""
Eviction hysteresis: cooldown, demand protection, and escape hatches.

Filters idle candidates before victim selection so REQUIRED requests can override
cooldown above the hard floor while OPPORTUNISTIC honors cooldown as a veto.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .eviction_cooldown_policy import (
    COOLDOWN_HARD_FLOOR_S,
    EvictionRequestClass,
    cooldown_override_eligible,
    remaining_cooldown_s,
)

if TYPE_CHECKING:
    from ..types import Gateway

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HysteresisResult:
    """Surviving evictable set plus metadata for EvictionPlanSummary."""

    evictable: list[ModelId]
    cooldown_protected_count: int
    demand_protected_count: int
    escape_hatch_used: bool = False
    escape_reason: str | None = None
    escape_cooldown_remaining_s: float | None = None
    escape_model_id: str | None = None
    cooldown_override_pending: bool = False
    cooldown_override_victim_id: str | None = None
    cooldown_override_remaining_s: float | None = None


def filter_evictable_with_hysteresis(
    gateway: Gateway,
    evictable: list[ModelId],
    *,
    eviction_cooldown_s: float,
    has_demand: Callable[[str], bool] | None,
    eviction_request_class: EvictionRequestClass,
) -> HysteresisResult | None:
    """Apply cooldown + demand filters and class-gated escape hatches.

    Returns None when hysteresis leaves no legal victim (plan abort).
    """
    now = time.monotonic()
    cooldown_protected: list[ModelId] = []
    past_cooldown: list[ModelId] = []
    for mid in evictable:
        elapsed = now - gateway.model_loaded_at.get(mid, 0.0)
        if elapsed < eviction_cooldown_s:
            cooldown_protected.append(mid)
        else:
            past_cooldown.append(mid)

    if cooldown_protected:
        logger.info(
            f"🛡️ Cooldown protection: {len(cooldown_protected)} models "
            f"within {eviction_cooldown_s}s window"
        )

    evictable = past_cooldown

    demand_protected: list[ModelId] = []
    if has_demand is not None and evictable:
        still_evictable: list[ModelId] = []
        for mid in evictable:
            if has_demand(mid.routing_key):
                demand_protected.append(mid)
            else:
                still_evictable.append(mid)
        if demand_protected:
            logger.info(
                f"🛡️ Demand protection: {len(demand_protected)} models "
                f"have queued consumers"
            )
        evictable = still_evictable

    escape_hatch_used = False
    escape_reason: str | None = None
    escape_cooldown_remaining_s: float | None = None
    escape_model_id: str | None = None
    cooldown_override_pending = False
    cooldown_override_victim_id: str | None = None
    cooldown_override_remaining_s: float | None = None

    if not evictable and demand_protected:
        escape_candidate = sorted(
            demand_protected,
            key=lambda m: gateway.model_loaded_at.get(m, 0.0),
        )[0]
        evictable = [escape_candidate]
        escape_hatch_used = True
        escape_reason = "demand"
        escape_model_id = str(escape_candidate)
        logger.warning(
            f"⚠️ Demand escape hatch: evicting {escape_candidate} on {gateway.name}"
        )

    if not evictable and cooldown_protected:
        if eviction_request_class == EvictionRequestClass.OPPORTUNISTIC:
            logger.info(
                f"🛡️ Opportunistic eviction blocked: all idle candidates in cooldown "
                f"on {gateway.name}"
            )
            return None

        eligible = [
            (
                mid,
                remaining_cooldown_s(gateway, mid, eviction_cooldown_s, now=now),
            )
            for mid in cooldown_protected
        ]
        eligible = [
            (mid, rem)
            for mid, rem in eligible
            if cooldown_override_eligible(rem, hard_floor_s=COOLDOWN_HARD_FLOOR_S)
        ]
        if not eligible:
            logger.info(
                f"🛡️ Required eviction blocked: cooldown victims at/below "
                f"{COOLDOWN_HARD_FLOOR_S}s floor on {gateway.name}"
            )
            return None

        escape_candidate, remaining = sorted(
            eligible,
            key=lambda item: gateway.model_loaded_at.get(item[0], 0.0),
        )[0]
        evictable = [escape_candidate]
        escape_hatch_used = True
        escape_reason = "cooldown"
        escape_cooldown_remaining_s = remaining
        escape_model_id = str(escape_candidate)
        cooldown_override_pending = True
        cooldown_override_victim_id = str(escape_candidate)
        cooldown_override_remaining_s = remaining
        logger.warning(
            f"⚠️ Required cooldown override planned for {escape_candidate} "
            f"(remaining={remaining:.1f}s) on {gateway.name}"
        )

    if not evictable:
        logger.debug("No evictable models after hysteresis filtering")
        return None

    return HysteresisResult(
        evictable=evictable,
        cooldown_protected_count=len(cooldown_protected),
        demand_protected_count=len(demand_protected),
        escape_hatch_used=escape_hatch_used,
        escape_reason=escape_reason,
        escape_cooldown_remaining_s=escape_cooldown_remaining_s,
        escape_model_id=escape_model_id,
        cooldown_override_pending=cooldown_override_pending,
        cooldown_override_victim_id=cooldown_override_victim_id,
        cooldown_override_remaining_s=cooldown_override_remaining_s,
    )
