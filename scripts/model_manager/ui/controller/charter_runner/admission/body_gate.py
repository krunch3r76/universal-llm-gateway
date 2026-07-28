"""Discover enrolled roots and decide whether one may receive a fresh window.

Discovery is opt-in: only threads tagged ``charter-runner`` are considered
(bounds blast radius per operator safety bind). Eligibility encodes the stop
conditions: a root gets a window only when its latest CHECKPOINT passes the
BODY half (gated work, wip_is_none, no operator fork, not BLOCKED, no in-flight
window, within caps) **and** the ENV half (§5.1) when non-vacuous.

Substrate facts reach the gate only via ``EnvironmentSnapshot`` — this module
imports no GIW or intent-store adapters (anti-accretion bind).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from universal_logging import get_logger

from ..checkpoint_schema import ParsedCheckpoint
from ..env_predicates import (
    ADMIT_INTENT_ORPHAN_REASON,
    ENV_SNAPSHOT_STALE_REASON,
    GIW_DRAIN_BLOCKS_RESTART_REASON,
    GIW_HOLD_BLOCKS_RESTART_REASON,
    EnvironmentSnapshot,
)
from ..window_terminal_contract import CHECKPOINT_PREFIX, is_tip_class, is_window_terminal
from .caps import CapStore
from .restart_pickup import next_pickup_is_restart_from_holder

logger = get_logger(__name__)

ENROLLMENT_TAG = "charter-runner"
ADMISSION_SUBJECT_PREFIX = "WIP charter-runner"
WindowKind = Literal["worker", "consult"]
GateHalf = Literal["body", "env"]


@dataclass(frozen=True)
class Decision:
    eligible: bool
    reason: str
    root_id: str
    checkpoint: dict | None = None
    parsed: ParsedCheckpoint | None = None
    admission_turn: dict | None = None
    window_kind: WindowKind = "worker"
    half: GateHalf | None = None
    predicate_id: str | None = None
    residue_fingerprint: str | None = None


def find_enrolled_roots() -> list[dict]:
    """List active agent-bus root threads tagged for charter-runner admission."""
    from agent_bus_store.db import list_threads_v2

    return list_threads_v2(status="active", tags=[ENROLLMENT_TAG])


def load_turns(root_id: str) -> list[dict]:
    """Load a root thread's turns in ascending turn-number order for gating."""
    from agent_bus_store.db import get_thread_turns_asc

    return get_thread_turns_asc(root_id)


def _turn_number(turn: dict) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def _latest_matching(
    turns: list[dict],
    predicate: Callable[[str], bool],
    *,
    after: int = 0,
) -> dict | None:
    best: dict | None = None
    best_n = after
    for turn in turns:
        subject = str(turn.get("subject") or "")
        n = _turn_number(turn)
        if n > best_n and predicate(subject):
            best, best_n = turn, n
    return best


def _starts_with(prefix: str) -> Callable[[str], bool]:
    upper = prefix.upper()
    return lambda subject: subject.upper().startswith(upper)


def _count_admissions(turns: list[dict]) -> int:
    prefix = ADMISSION_SUBJECT_PREFIX.upper()
    return sum(
        1 for t in turns if str(t.get("subject") or "").upper().startswith(prefix)
    )


def next_window_index(turns: list[dict]) -> int:
    """Bus admission count + 1 — the window index ENV E7 and caps intent key on."""
    return _count_admissions(turns) + 1


def live_wip_for_window(turns: list[dict], window_index: int) -> bool:
    """True when an in-flight admission pointer matches ``window_index`` on the root."""
    from .. import window_log

    # Window-terminal tip only — pickup appends must not shadow in-flight WIP.
    checkpoint = _latest_matching(turns, is_window_terminal)
    if checkpoint is None:
        return False
    cp_n = _turn_number(checkpoint)
    prefix = ADMISSION_SUBJECT_PREFIX.upper()
    for turn in turns:
        if _turn_number(turn) <= cp_n:
            continue
        subj = str(turn.get("subject") or "").upper()
        if not subj.startswith(prefix):
            continue
        meta = window_log.parse_admission_meta(str(turn.get("body") or ""))
        try:
            window = int(meta.get("window") or 0)
        except (TypeError, ValueError):
            window = 0
        if window == window_index or window == 0:
            return True
    return False


def _body_skip(
    reason: str,
    root_id: str,
    *,
    checkpoint: dict | None = None,
    parsed: ParsedCheckpoint | None = None,
    admission_turn: dict | None = None,
    window_kind: WindowKind = "worker",
) -> Decision:
    return Decision(
        False,
        reason,
        root_id,
        checkpoint=checkpoint,
        parsed=parsed,
        admission_turn=admission_turn,
        window_kind=window_kind,
        half="body",
    )


def evaluate_root(
    root_id: str,
    turns: list[dict],
    caps: CapStore,
    *,
    env_snapshot: EnvironmentSnapshot | None = None,
    admission_mode: str = "generate",
    now: datetime | None = None,
) -> Decision:
    """Evaluate BODY then ENV admission gates over a root's turns."""
    from .env_half import check_env_or_eligible

    # In-flight fence keys off window terminals only (not conveyor pickup appends).
    terminal = _latest_matching(turns, is_window_terminal)
    if terminal is None:
        # No worker/seat terminal yet — fall back to any tip-class (bootstrap).
        terminal = _latest_matching(turns, is_tip_class)
    if terminal is None:
        return _body_skip("no_checkpoint", root_id)

    cp_n = _turn_number(terminal)
    admission = _latest_matching(
        turns, _starts_with(ADMISSION_SUBJECT_PREFIX), after=cp_n
    )
    if admission is not None:
        return _body_skip(
            "window_in_flight",
            root_id,
            checkpoint=terminal,
            admission_turn=admission,
        )

    # Content tip may be a newer pickup append (Next-pickup merge authority).
    checkpoint = _latest_matching(turns, is_tip_class) or terminal

    # Soft-spill / sidecar-first stubs: follow Sidecar: before schema gate.
    from ..checkpoint_schema import materialize_checkpoint_turn

    checkpoint = materialize_checkpoint_turn(checkpoint)

    from ..checkpoint_admit_gate import (
        validate_arc_for_admit,
        validate_checkpoint_for_admit,
    )
    from ..executor_routing import resolve_charter_executor

    verdict = validate_checkpoint_for_admit(str(checkpoint.get("body") or ""))
    if not verdict.ok:
        return _body_skip(
            verdict.reason,
            root_id,
            checkpoint=checkpoint,
            parsed=verdict.parsed,
        )
    parsed = verdict.parsed
    assert parsed is not None

    from ..gate_lane_classifier import resolve_admit_lane

    window_kind, admission_mode, consult_role, parsed, lane_refuse = resolve_admit_lane(
        parsed,
        default_admission_mode=admission_mode,
        root_id=root_id,
        log=logger,
    )
    if lane_refuse:
        return _body_skip(lane_refuse, root_id, checkpoint=checkpoint, parsed=parsed)
    bind = resolve_charter_executor(
        parsed=parsed,
        admission_mode=admission_mode,
        consult_role=consult_role,
    )
    arc_verdict = validate_arc_for_admit(
        parsed,
        window_kind=window_kind,
        admission_mode=admission_mode,
        consult_role=consult_role,
        executor_lane=bind.lane,
        checkpoint_body=str(checkpoint.get("body") or ""),
    )
    if arc_verdict is not None and not arc_verdict.ok:
        return _body_skip(
            arc_verdict.reason,
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
        )

    if window_kind == "consult":
        allowed, cap_reason = caps.check(root_id)
        if not allowed:
            return _body_skip(
                cap_reason or "cap_reached",
                root_id,
                checkpoint=checkpoint,
                parsed=parsed,
                window_kind="consult",
            )
        return check_env_or_eligible(
            root_id,
            turns,
            caps,
            checkpoint,
            parsed,
            env_snapshot,
            admission_mode,
            now=now,
            window_kind="consult",
        )

    # next_pickup_gated already enforced inside validate_checkpoint_for_admit

    allowed, cap_reason = caps.check(root_id)
    if not allowed:
        return _body_skip(
            cap_reason or "cap_reached",
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
        )

    revise_ok, revise_reason = caps.check_revise_admit(root_id, parsed.next_pickup)
    if not revise_ok:
        return _body_skip(
            revise_reason or "revise_cap_exhausted",
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
        )

    return check_env_or_eligible(
        root_id,
        turns,
        caps,
        checkpoint,
        parsed,
        env_snapshot,
        admission_mode,
        now=now,
    )


# Re-export skip-reason constants for tests and event one-liners.
__all__ = [
    "ADMISSION_SUBJECT_PREFIX",
    "CHECKPOINT_PREFIX",
    "Decision",
    "ENROLLMENT_TAG",
    "GIW_DRAIN_BLOCKS_RESTART_REASON",
    "GIW_HOLD_BLOCKS_RESTART_REASON",
    "ADMIT_INTENT_ORPHAN_REASON",
    "ENV_SNAPSHOT_STALE_REASON",
    "evaluate_root",
    "find_enrolled_roots",
    "live_wip_for_window",
    "load_turns",
    "next_pickup_is_restart_from_holder",
    "next_window_index",
]
