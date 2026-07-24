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

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..restart_intent_store import Intent
from .caps import CapStore
from .checkpoint_parse import ParsedCheckpoint, parse_checkpoint

ENROLLMENT_TAG = "charter-runner"
ADMISSION_SUBJECT_PREFIX = "WIP charter-runner"
CHECKPOINT_PREFIX = "CHECKPOINT"
GIW_SERVICE = "git_integration_worker"
GIW_DRAIN_BLOCKS_RESTART_REASON = "giw_drain_blocks_restart_pickup"
WindowKind = Literal["worker", "consult"]

_GIW = re.escape(GIW_SERVICE)
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


def next_pickup_is_restart_from_holder(item: str) -> bool:
    """True when a Next-pickup row would re-hold GIW for manage restart/drain.

    Matches restart-from-holder phrasing (``sync_restart``, manage restart GIW,
    in-window ``wait_healthy`` tied to GIW restart). Post-healthy probe-only rows
    are excluded so drain can admit verification windows after the holder exits.
    """
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


def giw_drain_blocks_restart_pickup(
    next_pickup: list[str],
    giw_intent_probe: Callable[[], Intent | None],
) -> bool:
    """Return True when live GIW drain intent must refuse restart-shaped admission."""
    if not any(next_pickup_is_restart_from_holder(item) for item in next_pickup):
        return False
    intent = giw_intent_probe()
    return intent is not None and not intent.is_terminal


def _default_giw_intent_probe() -> Intent | None:
    from ..restart_intent_store import RestartIntentStore

    return RestartIntentStore.instance().active_for_service(GIW_SERVICE)


def evaluate_root(
    root_id: str,
    turns: list[dict],
    caps: CapStore,
    *,
    giw_intent_probe: Callable[[], Intent | None] | None = None,
) -> Decision:
    """Evaluate admission gates over a root's turns and optional GIW intent probe."""
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

    if parsed.consult_pending:
        allowed, cap_reason = caps.check(root_id)
        if not allowed:
            return Decision(
                False,
                cap_reason or "cap_reached",
                root_id,
                checkpoint=checkpoint,
                parsed=parsed,
            )
        return Decision(
            True,
            "eligible_consult",
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
            window_kind="consult",
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

    probe = giw_intent_probe or _default_giw_intent_probe
    if giw_drain_blocks_restart_pickup(parsed.next_pickup, probe):
        return Decision(
            False,
            GIW_DRAIN_BLOCKS_RESTART_REASON,
            root_id,
            checkpoint=checkpoint,
            parsed=parsed,
        )

    return Decision(True, "eligible", root_id, checkpoint=checkpoint, parsed=parsed)
