"""Discover enrolled roots and decide whether one may receive a fresh window.

Discovery is opt-in: only threads tagged ``charter-runner`` are considered
(bounds blast radius per operator safety bind). Eligibility encodes the stop
conditions: a root gets a window only when its latest CHECKPOINT has gated work
pending, no active WIP, no open operator fork, is not BLOCKED, has no window
already in flight, and is within caps.

The in-flight guard is bus-derived and restart-safe: the runner posts an
*admission pointer* turn (``WIP charter-runner …``) on the root when it admits a
window; a root is in-flight while such a pointer exists with a higher turn number
than the latest CHECKPOINT. A fresh CHECKPOINT past the pointer clears it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .caps import CapStore
from .checkpoint_parse import ParsedCheckpoint, parse_checkpoint

ENROLLMENT_TAG = "charter-runner"
ADMISSION_SUBJECT_PREFIX = "WIP charter-runner"
CHECKPOINT_PREFIX = "CHECKPOINT"


@dataclass(frozen=True)
class Decision:
    eligible: bool
    reason: str
    root_id: str
    checkpoint: dict | None = None
    parsed: ParsedCheckpoint | None = None
    admission_turn: dict | None = None


def find_enrolled_roots() -> list[dict]:
    from agent_bus_store.db import list_threads_v2

    return list_threads_v2(status="active", tags=[ENROLLMENT_TAG])


def load_turns(root_id: str) -> list[dict]:
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


def evaluate_root(root_id: str, turns: list[dict], caps: CapStore) -> Decision:
    """Pure gate evaluation over a root's turns (no I/O)."""
    checkpoint = _latest_matching(turns, _starts_with(CHECKPOINT_PREFIX))
    if checkpoint is None:
        return Decision(False, "no_checkpoint", root_id)

    cp_n = _turn_number(checkpoint)
    admission = _latest_matching(
        turns, _starts_with(ADMISSION_SUBJECT_PREFIX), after=cp_n
    )
    if admission is not None:
        return Decision(
            False,
            "window_in_flight",
            root_id,
            checkpoint=checkpoint,
            admission_turn=admission,
        )

    try:
        parsed = parse_checkpoint(str(checkpoint.get("body") or ""))
    except Exception:  # noqa: BLE001 — parser is lenient; any failure => skip
        return Decision(False, "parse_failed", root_id, checkpoint=checkpoint)

    if parsed.blocked:
        return Decision(False, "blocked", root_id, checkpoint=checkpoint, parsed=parsed)
    if not parsed.wip_is_none:
        return Decision(
            False, "wip_active", root_id, checkpoint=checkpoint, parsed=parsed
        )
    if parsed.open_operator_fork:
        return Decision(
            False, "operator_fork", root_id, checkpoint=checkpoint, parsed=parsed
        )
    if not parsed.next_pickup_gated:
        return Decision(
            False, "no_gated_pickup", root_id, checkpoint=checkpoint, parsed=parsed
        )

    allowed, cap_reason = caps.check(root_id)
    if not allowed:
        return Decision(
            False,
            cap_reason or "cap_reached",
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
        )

    revise_ok, revise_reason = caps.check_revise_admit(root_id, parsed.next_pickup)
    if not revise_ok:
        return Decision(
            False,
            revise_reason or "revise_cap_exhausted",
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
        )

    return Decision(True, "eligible", root_id, checkpoint=checkpoint, parsed=parsed)
