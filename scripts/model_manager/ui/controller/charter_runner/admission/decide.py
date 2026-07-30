"""Pure admission decide — ``decide(state, env, caps) → Transition`` (side-effect free)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from ..checkpoint_schema import ParsedCheckpoint
from ..root_ledger import RootLedgerRow, RootStatus, Transition
from .caps import CapStore

Lane = Literal["mechanical", "judgment", "consult"]

_G5_RE = re.compile(r"\bG5\b", re.I)
_ARCHITECTURE_DERIVED_RE = re.compile(
    r"derived_from.*architecture|consult_kind\s*=\s*architecture",
    re.IGNORECASE,
)
_CONSULT_PROVENANCE_RE = re.compile(
    r"^##\s+Consult provenance\b",
    re.IGNORECASE | re.MULTILINE,
)
_PROVENANCE_FIELD_RE = re.compile(
    r"^\s*[-*]\s*(consultant_substrate|consultant_family|gate_id)\s*:\s*(\S+)",
    re.IGNORECASE | re.MULTILINE,
)


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
    ``CONSULT_PENDING`` tips are never empty hoppers (``executor=pending`` is
    the consult seat bind). When True, ``decide`` returns ``Transition.NOOP``
    (empty_hopper fence); the root stays enrolled (legal marked standing wait).

    ``consult_pending`` / ``tip_executor``: tip-shaped Policy B inputs (a:27165).
    When tip is absent, ``decide`` falls back to ledger ``pickup_executor``.
    """

    substrate_up: bool
    has_wip: bool
    attendance: str
    propagation_residue: dict[str, Any] | None = None
    giw_holder_lease: dict[str, Any] | None = None
    restart_shaped: bool = False
    now: float | None = None
    empty_hopper: bool = False
    consult_pending: bool = False
    tip_executor: str | None = None
    arc_lane: str = "path_sim"
    layer_independence_block: bool = False


def _propagation_defers(state: RootLedgerRow, env: EnvFacts) -> bool:
    """Holder-lease + sync_restart residue defer restart-shaped pickups only."""
    residue = env.propagation_residue or {}
    if residue.get("kind") != "git_integration_worker":
        return False
    if not env.restart_shaped:
        return False
    lease = env.giw_holder_lease or {}
    return bool(lease.get("held"))


def _explicit_cursor_worker(executor: str | None) -> bool:
    """True only for an explicit ``cursor/*`` executor (not None/pending/empty).

    Distinct from ``worker_substrate_compatible``: that helper treats
    None/pending as open-for-worker. Policy B needs a positive cursor bind so
    autonomous judgment without an executor still QUEUE_CONSULT.
    """
    if executor is None:
        return False
    cleaned = str(executor).strip()
    if not cleaned or cleaned.lower() == "pending":
        return False
    return cleaned.startswith("cursor/")


def _step_done(parsed: ParsedCheckpoint, gate_id: str) -> bool:
    token = gate_id.upper()
    for step in parsed.steps:
        title = step.title.upper()
        if token in title and step.status == "done":
            return True
    return False


def _provenance_fields(body: str) -> dict[str, str]:
    if not _CONSULT_PROVENANCE_RE.search(body or ""):
        return {}
    fields: dict[str, str] = {}
    for match in _PROVENANCE_FIELD_RE.finditer(body or ""):
        fields[match.group(1).lower()] = match.group(2).strip()
    return fields


def _gate_family(body: str, gate_id: str) -> str | None:
    fields = _provenance_fields(body)
    tagged = fields.get("gate_id", "").upper()
    if tagged == gate_id.upper() and fields.get("consultant_family"):
        return fields["consultant_family"].lower()
    section_match = re.search(
        rf"{gate_id.upper()}[^\n]*consultant_family\s*[:=]\s*(\S+)",
        body or "",
        re.IGNORECASE,
    )
    if section_match:
        return section_match.group(1).lower()
    return None


def layer_independence_ok(
    *,
    parsed: ParsedCheckpoint | None,
    checkpoint_body: str,
) -> bool:
    """True when structural independence holds before layer G5 implement."""
    if parsed is None:
        return False
    body = checkpoint_body or ""
    branch_a = False
    if _step_done(parsed, "G1") or _step_done(parsed, "G2"):
        substrate = _provenance_fields(body).get("consultant_substrate", "").lower()
        if substrate == "web-anthropic":
            branch_a = True
    if not branch_a and _ARCHITECTURE_DERIVED_RE.search(body):
        branch_a = True
    g3_family = _gate_family(body, "G3") or "grok"
    g4_family = _gate_family(body, "G4")
    branch_b = g4_family is not None and g4_family != g3_family
    return branch_a and branch_b


def layer_implement_pickup(*, parsed: ParsedCheckpoint | None, pickup_lane: str) -> bool:
    """True when the *current* admit targets layer G5 implement.

    Contract (Fork 2): block only when pickup is G5 ``[implement]``, not when a
    future implement Step is merely open on the scoreboard (G1–G4 would otherwise
    false-positive ``layer_independence_unproven`` on every layer birth tip).
    ``pickup_lane`` retained for call-site compatibility; Next-pickup is authoritative.
    """
    del pickup_lane  # call-site compat — Next-pickup owns the target, not ledger lane
    if parsed is None:
        return False
    for item in parsed.next_pickup:
        if _G5_RE.search(item) and "implement" in item.lower():
            return True
    return False


def layer_independence_unproven(
    *,
    arc_lane: str,
    attendance: str,
    parsed: ParsedCheckpoint | None,
    checkpoint_body: str,
    pickup_lane: str,
) -> bool:
    """Fail-closed guard before layer G5 implement admission."""
    if arc_lane != "layer" or attendance != "autonomous":
        return False
    if not layer_implement_pickup(parsed=parsed, pickup_lane=pickup_lane):
        return False
    return not layer_independence_ok(parsed=parsed, checkpoint_body=checkpoint_body)


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
    if env.layer_independence_block:
        return Transition.BLOCK
    # Live consult queue must not be stranded by empty_hopper NOOP (6237 sibling
    # hole: CONSULT_QUEUED + executor=pending tip read as standing wait).
    if state.status in (RootStatus.CONSULT_QUEUED, RootStatus.CONSULT_DEFERRED):
        if not env.substrate_up:
            return Transition.DEFER_CONSULT
        now = env.now if env.now is not None else __import__("time").time()
        if state.consult_next_retry and now < state.consult_next_retry:
            return Transition.NOOP
        return Transition.ADMIT_CONSULT
    if env.empty_hopper:
        return Transition.NOOP

    lane = (state.pickup_lane or "judgment").lower()
    attendance = (state.attendance or env.attendance or "attended").lower()
    executor = env.tip_executor if env.tip_executor else state.pickup_executor

    # Tip CONSULT_PENDING always owns the consult path (even with cursor/*).
    if env.consult_pending and attendance == "autonomous":
        if not env.substrate_up:
            return Transition.DEFER_CONSULT
        return Transition.QUEUE_CONSULT

    if lane in ("judgment", "consult") and attendance == "autonomous":
        # Policy B (a:27165): explicit cursor/* tip/typed executor on judgment
        # ⇒ ADMIT_WORKER — else force-consult loops after harvest forever.
        if lane == "judgment" and _explicit_cursor_worker(executor):
            return Transition.ADMIT_WORKER
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
    "layer_implement_pickup",
    "layer_independence_ok",
    "layer_independence_unproven",
    "map_old_skip_to_kernel",
]
