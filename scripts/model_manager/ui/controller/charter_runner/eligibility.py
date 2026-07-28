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

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from universal_logging import get_logger

from .caps import CapStore
from .checkpoint_parse import ParsedCheckpoint
from .env_predicates import (
    ADMIT_INTENT_ORPHAN_REASON,
    ENV_SNAPSHOT_STALE_REASON,
    GIW_DRAIN_BLOCKS_RESTART_REASON,
    GIW_HOLD_BLOCKS_RESTART_REASON,
    EnvEvalContext,
    EnvironmentSnapshot,
    evaluate_env_half,
)
from .window_terminal_contract import CHECKPOINT_PREFIX, is_tip_class

logger = get_logger(__name__)

ENROLLMENT_TAG = "charter-runner"
ADMISSION_SUBJECT_PREFIX = "WIP charter-runner"
WindowKind = Literal["worker", "consult"]
GateHalf = Literal["body", "env"]

_GIW = re.escape("git_integration_worker")
_SYNC_RESTART_GIW_RE = re.compile(
    rf"sync_restart[^;\n]*{_GIW}|{_GIW}[^;\n]*sync_restart",
    re.IGNORECASE,
)
_MANAGE_GIW_RESTART_RE = re.compile(
    rf"manage\s*\([^)]*(?:restart|sync_restart|stop)[^)]*{_GIW}|"
    rf"manage\s*\([^)]*{_GIW}[^)]*(?:restart|sync_restart|stop)",
    re.IGNORECASE,
)
_WAIT_HEALTHY_GIW_RESTART_RE = re.compile(
    rf"wait_healthy[^;\n]*(?:sync_restart|restart)[^;\n]*{_GIW}|"
    rf"(?:sync_restart|restart)[^;\n]*wait_healthy[^;\n]*{_GIW}|"
    rf"wait_healthy[^;\n]*{_GIW}[^;\n]*(?:sync_restart|restart)",
    re.IGNORECASE,
)
_BARE_GIW_RESTART_RE = re.compile(
    rf"\brestart\b[^;\n]*{_GIW}|{_GIW}[^;\n]*\brestart\b",
    re.IGNORECASE,
)
_PROBE_ONLY_PICKUP_RE = re.compile(
    r"\b(?:live\s+)?probe\b(?:\s+only|\s+after\s+(?:healthy|restart))",
    re.IGNORECASE,
)


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
    from . import window_log

    checkpoint = _latest_matching(turns, is_tip_class)
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


def next_pickup_is_restart_from_holder(item: str) -> bool:
    """True when a Next-pickup row would re-hold GIW for manage restart/drain."""
    text = item.strip()
    if not text:
        return False
    restart_shaped = any(
        pattern.search(text)
        for pattern in (
            _SYNC_RESTART_GIW_RE,
            _MANAGE_GIW_RESTART_RE,
            _WAIT_HEALTHY_GIW_RESTART_RE,
            _BARE_GIW_RESTART_RE,
        )
    )
    if not restart_shaped:
        return False
    if _PROBE_ONLY_PICKUP_RE.search(text) and not (
        _SYNC_RESTART_GIW_RE.search(text) or _MANAGE_GIW_RESTART_RE.search(text)
    ):
        return False
    return True


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


def _env_skip(
    reason: str,
    root_id: str,
    *,
    predicate_id: str,
    checkpoint: dict,
    parsed: ParsedCheckpoint,
    window_kind: WindowKind = "worker",
) -> Decision:
    return Decision(
        False,
        reason,
        root_id,
        checkpoint=checkpoint,
        parsed=parsed,
        window_kind=window_kind,
        half="env",
        predicate_id=predicate_id,
    )


def evaluate_root(
    root_id: str,
    turns: list[dict],
    caps: CapStore,
    *,
    env_snapshot: EnvironmentSnapshot | None = None,
    now: datetime | None = None,
) -> Decision:
    """Evaluate BODY then ENV admission gates over a root's turns."""
    checkpoint = _latest_matching(turns, is_tip_class)
    if checkpoint is None:
        return _body_skip("no_checkpoint", root_id)

    # Soft-spill / sidecar-first stubs: follow Sidecar: before schema gate.
    from .checkpoint_body import materialize_checkpoint_turn

    checkpoint = materialize_checkpoint_turn(checkpoint)

    cp_n = _turn_number(checkpoint)
    admission = _latest_matching(
        turns, _starts_with(ADMISSION_SUBJECT_PREFIX), after=cp_n
    )
    if admission is not None:
        return _body_skip(
            "window_in_flight",
            root_id,
            checkpoint=checkpoint,
            admission_turn=admission,
        )

    from .attendance import admission_mode_for_root
    from .checkpoint_admit_gate import (
        validate_arc_for_admit,
        validate_checkpoint_for_admit,
    )
    from .executor_routing import resolve_charter_executor

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

    admission_mode = admission_mode_for_root(root_id)
    from .gate_lane_classifier import resolve_admit_lane

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
        body_ok = _check_env_or_eligible(
            root_id,
            turns,
            caps,
            checkpoint,
            parsed,
            env_snapshot,
            now=now,
            window_kind="consult",
        )
        return body_ok

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

    return _check_env_or_eligible(
        root_id,
        turns,
        caps,
        checkpoint,
        parsed,
        env_snapshot,
        now=now,
    )


def _residue_skip(
    reason: str,
    root_id: str,
    *,
    checkpoint: dict,
    parsed: ParsedCheckpoint,
    window_kind: WindowKind,
    fingerprint: str,
) -> Decision:
    return Decision(
        False,
        reason,
        root_id,
        checkpoint=checkpoint,
        parsed=parsed,
        window_kind=window_kind,
        half="body",
        residue_fingerprint=fingerprint,
    )


def _check_env_or_eligible(
    root_id: str,
    turns: list[dict],
    caps: CapStore,
    checkpoint: dict,
    parsed: ParsedCheckpoint,
    env_snapshot: EnvironmentSnapshot | None,
    *,
    now: datetime | None = None,
    window_kind: WindowKind = "worker",
) -> Decision:
    next_window = next_window_index(turns)
    restart_shaped = any(
        next_pickup_is_restart_from_holder(item) for item in parsed.next_pickup
    )
    ctx = EnvEvalContext(
        restart_shaped=restart_shaped,
        admit_intent_orphan=caps.has_admit_intent(root_id, next_window),
    )
    env_skip = evaluate_env_half(env_snapshot, ctx, now=now)
    if env_skip is not None:
        return _env_skip(
            env_skip.reason,
            root_id,
            predicate_id=env_skip.predicate_id,
            checkpoint=checkpoint,
            parsed=parsed,
            window_kind=window_kind,
        )
    from .attendance import admission_mode_for_root
    from .residue_fingerprint import (
        ResidueRecord,
        evaluate_residue_gate,
        load_residue_record,
        save_residue_record,
    )

    admission_mode = admission_mode_for_root(root_id)
    if parsed.consult_pending:
        admission_mode = "consult"
    cp_body = str(checkpoint.get("body") or "")
    last = load_residue_record(root_id)
    gate = evaluate_residue_gate(
        checkpoint_body=cp_body,
        parsed=parsed,
        admission_mode=admission_mode,
        window_kind=window_kind,
        last=last,
        window_index=next_window,
    )
    if not gate.admit:
        save_residue_record(
            root_id,
            ResidueRecord(
                fingerprint=gate.fingerprint,
                witness=gate.witness,
                consecutive_skip_count=gate.consecutive_skip_count,
                w10_consumed=gate.w10_consumed,
                last_window_index=gate.last_window_index,
            ),
        )
        if gate.stop_root:
            caps.mark_failed(root_id, gate.reason)
        return _residue_skip(
            gate.reason,
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
            window_kind=window_kind,
            fingerprint=gate.fingerprint,
        )
    if gate.w10_consumed and last is not None:
        save_residue_record(
            root_id,
            ResidueRecord(
                fingerprint=gate.fingerprint,
                witness=gate.witness,
                consecutive_skip_count=0,
                w10_consumed=True,
                last_window_index=gate.last_window_index,
            ),
        )
    reason = "eligible_consult" if window_kind == "consult" else "eligible"
    return Decision(
        True,
        reason,
        root_id,
        checkpoint=checkpoint,
        parsed=parsed,
        window_kind=window_kind,
        residue_fingerprint=gate.fingerprint,
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
