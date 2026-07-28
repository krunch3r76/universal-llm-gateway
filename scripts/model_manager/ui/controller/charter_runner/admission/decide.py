"""Pure admission decide — ``decide(state, env, caps) → Transition`` (side-effect free)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..root_ledger import RootLedgerRow, RootStatus, Transition
from .caps import CapStore

Lane = Literal["mechanical", "judgment", "consult"]


@dataclass(frozen=True)
class CapsView:
    """Thin caps adapter for shadow/kernel — heal counters excluded."""

    allowed: bool
    skip_reason: str | None
    stopped_reason: str | None
    revise_ok: bool
    revise_reason: str | None

    @classmethod
    def from_cap_store(
        cls,
        caps: CapStore,
        root_id: str,
        *,
        next_pickup: list[str] | None = None,
    ) -> CapsView:
        allowed, skip = caps.check(root_id)
        state = caps._roots.get(root_id)  # noqa: SLF001 — shadow adapter only
        stopped = state.stopped_reason if state else None
        revise_ok, revise_reason = caps.check_revise_admit(root_id, next_pickup or [])
        return cls(
            allowed=allowed,
            skip_reason=skip,
            stopped_reason=stopped,
            revise_ok=revise_ok,
            revise_reason=revise_reason,
        )


@dataclass(frozen=True)
class EnvFacts:
    """Kernel env snapshot subset for admission.

    ``empty_hopper``: gated Next-pickup present, no WIP (``has_wip`` or
    ``wip_window_id`` on the ledger row), and tip ``executor=`` is explicitly
    empty or ``pending`` — **not** a missing ``executor=`` token (fail-open).
    When True, ``decide`` returns ``Transition.NOOP`` (empty_hopper fence);
    the root stays enrolled (legal marked standing wait).
    """

    substrate_up: bool
    has_wip: bool
    attendance: str
    propagation_residue: dict[str, Any] | None = None
    giw_holder_lease: dict[str, Any] | None = None
    restart_shaped: bool = False
    now: float | None = None
    empty_hopper: bool = False


def _propagation_defers(state: RootLedgerRow, env: EnvFacts) -> bool:
    """Holder-lease + sync_restart residue defer restart-shaped pickups only."""
    residue = env.propagation_residue or {}
    if residue.get("kind") != "git_integration_worker":
        return False
    if not env.restart_shaped:
        return False
    lease = env.giw_holder_lease or {}
    return bool(lease.get("held"))


def decide(
    state: RootLedgerRow,
    env: EnvFacts,
    caps: CapsView,
) -> Transition:
    """Side-effect-free transition for one enrolled root (spec §C.2)."""
    if state.status == RootStatus.BLOCKED:
        return Transition.NOOP
    if state.status == RootStatus.CLOSED:
        return Transition.NOOP
    if env.has_wip or state.wip_window_id:
        return Transition.NOOP
    if _propagation_defers(state, env):
        return Transition.DEFER_CONSULT
    if not caps.allowed:
        if caps.stopped_reason:
            return Transition.BLOCK
        return Transition.NOOP
    if not caps.revise_ok:
        return Transition.BLOCK
    if env.empty_hopper:
        return Transition.NOOP

    lane = (state.pickup_lane or "judgment").lower()
    attendance = (state.attendance or env.attendance or "attended").lower()

    if state.status in (RootStatus.CONSULT_QUEUED, RootStatus.CONSULT_DEFERRED):
        if not env.substrate_up:
            return Transition.DEFER_CONSULT
        now = env.now if env.now is not None else __import__("time").time()
        if state.consult_next_retry and now < state.consult_next_retry:
            return Transition.NOOP
        return Transition.ADMIT_CONSULT

    if lane in ("judgment", "consult") and attendance == "autonomous":
        if not env.substrate_up:
            return Transition.DEFER_CONSULT
        return Transition.QUEUE_CONSULT

    if lane == "mechanical" or attendance == "attended":
        if not env.substrate_up and lane == "consult":
            return Transition.DEFER_CONSULT
        return Transition.ADMIT_WORKER

    if not env.substrate_up:
        return Transition.DEFER_CONSULT
    return Transition.ADMIT_WORKER


def map_old_skip_to_kernel(
    old_reason: str,
    *,
    attendance: str,
    substrate_up: bool,
) -> Transition:
    """Shadow classifier: ``arc_lane_too_weak`` → consult queue/defer."""
    if old_reason != "arc_lane_too_weak":
        return Transition.NOOP
    if attendance == "autonomous":
        return (
            Transition.QUEUE_CONSULT
            if substrate_up
            else Transition.DEFER_CONSULT
        )
    return Transition.ADMIT_WORKER


def _old_admits(decision: str) -> bool:
    return decision in ("eligible", "admit")


def classify_shadow_diff(
    old_decision: str,
    kernel_transition: Transition,
) -> str:
    """Return ``agree`` | ``old-correct`` | ``kernel-correct``."""
    mapped = _normalize_old(old_decision)
    kernel = kernel_transition.value
    if mapped == kernel or (
        mapped == Transition.ADMIT_WORKER.value
        and kernel == Transition.ADMIT_WORKER.value
    ):
        return "agree"
    if _old_admits(old_decision) and kernel_transition == Transition.ADMIT_WORKER:
        return "agree"
    # Outcome-equivalent non-admit: old skip reason ↔ kernel NOOP (e.g. window_in_flight).
    if not _old_admits(old_decision) and kernel_transition == Transition.NOOP:
        return "agree"
    if old_decision == "arc_lane_too_weak" and kernel in (
        Transition.QUEUE_CONSULT.value,
        Transition.DEFER_CONSULT.value,
    ):
        return "kernel-correct"
    return "old-correct" if mapped else "kernel-correct"


def _normalize_old(decision: str) -> str:
    if decision in ("eligible", "admit"):
        return Transition.ADMIT_WORKER.value
    if decision == "arc_lane_too_weak":
        return "skip"
    if decision.startswith("stopped:"):
        return Transition.BLOCK.value
    return decision


__all__ = [
    "CapsView",
    "EnvFacts",
    "classify_shadow_diff",
    "decide",
    "map_old_skip_to_kernel",
]
