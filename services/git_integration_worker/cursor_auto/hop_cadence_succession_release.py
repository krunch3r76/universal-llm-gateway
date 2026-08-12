"""Release superseded admission on hop-cadence succession confirm (arc 7119 R7).

On ``giw.cursor_auto.hop_cadence_succession_confirmed`` with
``incumbent_recorded``, terminalize the recorded predecessor execution via the
cdp-ask satellite so ``admission_count`` decrements. Non-incumbent verdicts and
mid-turn predecessors (streaming / stop / tool_pause) are skipped or deferred —
never silently dropped.

Interim widen gate (until running split lands): refuse on the full page-liveness
in-flight OR ``streaming ∨ stop ∨ tool_pause``, and require a sustained idle
streak across consecutive reconcile samples before calling abort.
"""

from __future__ import annotations

from typing import Any

from cdp_ask.client import CdpAskClient, CdpAskClientError
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
    PredecessorHandle,
    PredecessorVerdict,
)

logger = get_logger(__name__)

_ACTIVE_STATUSES = frozenset({"pending", "running"})

# Interim idle-streak gate — running split is the real closer; until then require
# consecutive idle samples (~2s harvest cadence × 3 samples ≈ 6s window).
RELEASE_IDLE_STREAK_REQUIRED = 3
RELEASE_IDLE_SAMPLE_WINDOW_S = 2.0


def predecessor_in_flight(poll: dict[str, Any]) -> tuple[bool, str | None]:
    """Return ``(in_flight, reason)`` when poll shows an active mid-turn predecessor."""
    status = str(poll.get("status") or "")
    if status not in _ACTIVE_STATUSES:
        return False, None
    if poll.get("streaming"):
        return True, "predecessor_streaming"
    if poll.get("stop"):
        return True, "predecessor_stop"
    if poll.get("tool_pause"):
        return True, "predecessor_tool_pause"
    return False, None


def predecessor_mid_turn(poll: dict[str, Any]) -> tuple[bool, str | None]:
    """Backward-compatible alias for the widened in-flight gate."""
    return predecessor_in_flight(poll)


def release_superseded_on_confirm(
    handle: PredecessorHandle,
    *,
    client: CdpAskClient | None = None,
    idle_streak: int = 0,
) -> dict[str, Any]:
    """Terminalize the recorded predecessor execution when confirm is incumbent-backed."""
    exec_id = handle.execution_id.strip()
    if handle.verdict != PredecessorVerdict.INCUMBENT_RECORDED:
        return {
            "action": "skipped",
            "execution_id": exec_id,
            "reason": f"verdict_{handle.verdict.value}",
        }
    if not exec_id:
        return {"action": "skipped", "execution_id": exec_id, "reason": "empty_execution_id"}

    http = client or CdpAskClient()
    try:
        poll = http.poll(exec_id)
    except CdpAskClientError as exc:
        logger.warning(
            "hop_cadence succession release poll failed exec=%s err=%s",
            exec_id,
            exc,
        )
        return {"action": "error", "execution_id": exec_id, "error": str(exc)}

    status = str(poll.get("status") or "")
    if status not in _ACTIVE_STATUSES:
        return {
            "action": "already_terminal",
            "execution_id": exec_id,
            "status": status,
        }

    in_flight, defer_reason = predecessor_in_flight(poll)
    if in_flight:
        logger.info(
            "hop_cadence succession release deferred exec=%s reason=%s",
            exec_id,
            defer_reason,
        )
        return {
            "action": "deferred",
            "execution_id": exec_id,
            "reason": defer_reason,
            "idle_streak": 0,
            "idle_streak_required": RELEASE_IDLE_STREAK_REQUIRED,
        }

    new_streak = idle_streak + 1
    if new_streak < RELEASE_IDLE_STREAK_REQUIRED:
        logger.info(
            "hop_cadence succession release deferred exec=%s reason=predecessor_idle_streak_unsatisfied streak=%d/%d",
            exec_id,
            new_streak,
            RELEASE_IDLE_STREAK_REQUIRED,
        )
        return {
            "action": "deferred",
            "execution_id": exec_id,
            "reason": "predecessor_idle_streak_unsatisfied",
            "idle_streak": new_streak,
            "idle_streak_required": RELEASE_IDLE_STREAK_REQUIRED,
            "idle_sample_window_s": RELEASE_IDLE_SAMPLE_WINDOW_S,
        }

    try:
        abort_result = http.abort(exec_id)
    except CdpAskClientError as exc:
        logger.warning(
            "hop_cadence succession release abort failed exec=%s err=%s",
            exec_id,
            exc,
        )
        return {"action": "error", "execution_id": exec_id, "error": str(exc)}

    aborted = abort_result.get("aborted") is True
    abort_outcome = abort_result.get("abort_outcome")
    if not aborted:
        logger.warning(
            "hop_cadence succession release abort not terminal exec=%s outcome=%s status=%s",
            exec_id,
            abort_outcome,
            abort_result.get("status"),
        )
        return {
            "action": "error",
            "execution_id": exec_id,
            "abort_outcome": abort_outcome,
            "abort": abort_result,
        }

    logger.info(
        "hop_cadence succession release terminalized exec=%s status=%s outcome=%s",
        exec_id,
        abort_result.get("status"),
        abort_outcome,
    )
    return {
        "action": "terminalized",
        "execution_id": exec_id,
        "abort_outcome": abort_outcome,
        "abort": abort_result,
    }


__all__ = [
    "RELEASE_IDLE_SAMPLE_WINDOW_S",
    "RELEASE_IDLE_STREAK_REQUIRED",
    "predecessor_in_flight",
    "predecessor_mid_turn",
    "release_superseded_on_confirm",
]
