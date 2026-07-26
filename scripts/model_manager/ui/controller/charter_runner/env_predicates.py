"""Declarative environment-half predicates for charter tick admission (§5.3).

Pure evaluations over a typed ``EnvironmentSnapshot`` — no I/O inside predicates.
``eligibility.evaluate_root`` consumes the registry; substrate adapters
(``giw_live_hold``, intent store) populate the snapshot in ``tick_loop``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Posture = Literal["CLEAR", "HOLD", "UNKNOWN"]
PredicateClass = Literal["obs", "res"]

# Bound in checkpoint-schema-profiles.md §5.1 — one tick wall budget.
DEFAULT_SNAPSHOT_TTL_S = 60.0

GIW_DRAIN_INTENT_ID = "giw_drain_intent"
GIW_LIVE_HOLD_ID = "giw_live_hold"
ADMIT_INTENT_ORPHAN_ID = "admit_intent_orphan"

GIW_DRAIN_BLOCKS_RESTART_REASON = "giw_drain_blocks_restart_pickup"
GIW_HOLD_BLOCKS_RESTART_REASON = "giw_live_hold_blocks_restart_pickup"
ADMIT_INTENT_ORPHAN_REASON = "admit_intent_orphan"
ENV_SNAPSHOT_STALE_REASON = "env_snapshot_stale"

SOURCE_GIW_DRAIN = "giw_drain_intent"
SOURCE_GIW_LIVE = "giw_live_hold"
SOURCE_ADMIT_INTENT = "admit_intent_orphan"


@dataclass(frozen=True)
class SourceRead:
    """One tick-scoped substrate observation."""

    status: Literal["ok", "degraded", "error"]
    payload: Any | None = None
    error_class: str | None = None
    latency_ms: float | None = None
    scope: Literal["tick", "root"] = "tick"


@dataclass(frozen=True)
class EnvironmentSnapshot:
    """Tick-scoped world facts for ENV-half evaluation."""

    observed_at: datetime
    ttl_s: float
    sources: dict[str, SourceRead | Any] = field(default_factory=dict)

    def age_s(self, *, now: datetime | None = None) -> float:
        ref = now or datetime.now(UTC)
        observed = self.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        return max(0.0, (ref - observed).total_seconds())

    def is_stale(self, *, now: datetime | None = None) -> bool:
        return self.age_s(now=now) > self.ttl_s


@dataclass(frozen=True)
class EnvEvalContext:
    """Per-root facts that gate which predicates apply."""

    restart_shaped: bool
    admit_intent_orphan: bool


@dataclass(frozen=True)
class EnvPredicateSpec:
    """Registry row — ids/posture/skip_reason must match §5.3 prose SOT."""

    id: str
    predicate_class: PredicateClass
    skip_reason: str
    posture_justification: str


@dataclass(frozen=True)
class EnvSkip:
    """One ENV-half refusal at phase P2."""

    reason: str
    predicate_id: str
    posture: Posture = "HOLD"


# §5.3 vocabulary — mechanical conformance test compares against this table.
ENV_PREDICATE_REGISTRY: tuple[EnvPredicateSpec, ...] = (
    EnvPredicateSpec(
        id=GIW_DRAIN_INTENT_ID,
        predicate_class="obs",
        skip_reason=GIW_DRAIN_BLOCKS_RESTART_REASON,
        posture_justification="non-terminal drain intent blocks restart-shaped pickup",
    ),
    EnvPredicateSpec(
        id=GIW_LIVE_HOLD_ID,
        predicate_class="res",
        skip_reason=GIW_HOLD_BLOCKS_RESTART_REASON,
        posture_justification=(
            "live busy/lease held blocks restart; ConnectError→CLEAR per D-rule"
        ),
    ),
    EnvPredicateSpec(
        id=ADMIT_INTENT_ORPHAN_ID,
        predicate_class="obs",
        skip_reason=ADMIT_INTENT_ORPHAN_REASON,
        posture_justification=(
            "stale admit-intent without live WIP pointer — HOLD in S0; "
            "orchestration may heal to CLEAR (agent-bus:5824)"
        ),
    ),
    EnvPredicateSpec(
        id="env_snapshot_stale",
        predicate_class="obs",
        skip_reason=ENV_SNAPSHOT_STALE_REASON,
        posture_justification="snapshot older than ttl_s — fail-closed for remaining roots",
    ),
)

_REGISTRY_BY_ID = {row.id: row for row in ENV_PREDICATE_REGISTRY}
_REGISTRY_BY_REASON = {row.skip_reason: row for row in ENV_PREDICATE_REGISTRY}


def registry_skip_reasons() -> frozenset[str]:
    """Skip reasons owned by the ENV predicate registry (exhaustiveness helper)."""
    return frozenset(row.skip_reason for row in ENV_PREDICATE_REGISTRY)


def _intent_from_source(snapshot: EnvironmentSnapshot) -> Any | None:
    read = snapshot.sources.get(SOURCE_GIW_DRAIN)
    if isinstance(read, SourceRead):
        return read.payload
    return read


def _eval_giw_drain_intent(
    snapshot: EnvironmentSnapshot, ctx: EnvEvalContext
) -> Posture:
    if not ctx.restart_shaped:
        return "CLEAR"
    intent = _intent_from_source(snapshot)
    if intent is None:
        return "CLEAR"
    if getattr(intent, "is_terminal", False):
        return "CLEAR"
    return "HOLD"


def _eval_giw_live_hold(snapshot: EnvironmentSnapshot, ctx: EnvEvalContext) -> Posture:
    if not ctx.restart_shaped:
        return "CLEAR"
    read = snapshot.sources.get(SOURCE_GIW_LIVE)
    if not isinstance(read, SourceRead):
        return "HOLD"
    if read.status == "degraded" and read.error_class == "ConnectError":
        return "CLEAR"
    if read.status == "ok":
        held = bool(read.payload)
        return "HOLD" if held else "CLEAR"
    return "HOLD"


def _eval_admit_intent_orphan(_snapshot: EnvironmentSnapshot, ctx: EnvEvalContext) -> Posture:
    if not ctx.admit_intent_orphan:
        return "CLEAR"
    return "HOLD"


_EVALUATORS: dict[str, Any] = {
    GIW_DRAIN_INTENT_ID: _eval_giw_drain_intent,
    GIW_LIVE_HOLD_ID: _eval_giw_live_hold,
    ADMIT_INTENT_ORPHAN_ID: _eval_admit_intent_orphan,
}


def evaluate_env_half(
    snapshot: EnvironmentSnapshot | None,
    ctx: EnvEvalContext,
    *,
    now: datetime | None = None,
) -> EnvSkip | None:
    """Return the first ENV refusal, or None when the half is CLEAR."""
    if snapshot is None:
        return None
    if snapshot.is_stale(now=now):
        return EnvSkip(
            ENV_SNAPSHOT_STALE_REASON,
            "env_snapshot_stale",
            posture="HOLD",
        )
    for spec in ENV_PREDICATE_REGISTRY:
        if spec.id == "env_snapshot_stale":
            continue
        eval_fn = _EVALUATORS.get(spec.id)
        if eval_fn is None:
            continue
        posture = eval_fn(snapshot, ctx)
        if posture == "HOLD":
            return EnvSkip(spec.skip_reason, spec.id, posture=posture)
    return None
