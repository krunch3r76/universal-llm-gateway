"""Release superseded admission on hop-cadence succession confirm (arc 7119 R7).

On ``giw.cursor_auto.hop_cadence_succession_confirmed`` with
``incumbent_recorded``, terminalize the recorded predecessor execution via the
cdp-ask satellite so ``admission_count`` decrements. Non-incumbent verdicts and
mid-turn predecessors (streaming / tool_pause) are skipped or deferred — never
silently dropped.
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


def predecessor_mid_turn(poll: dict[str, Any]) -> tuple[bool, str | None]:
    """Return ``(mid_turn, reason)`` when a live predecessor must not be torn down."""
    status = str(poll.get("status") or "")
    if status not in _ACTIVE_STATUSES:
        return False, None
    if poll.get("streaming"):
        return True, "predecessor_streaming"
    if poll.get("tool_pause"):
        return True, "predecessor_tool_pause"
    return False, None


def release_superseded_on_confirm(
    handle: PredecessorHandle,
    *,
    client: CdpAskClient | None = None,
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

    mid_turn, defer_reason = predecessor_mid_turn(poll)
    if mid_turn:
        logger.info(
            "hop_cadence succession release deferred exec=%s reason=%s",
            exec_id,
            defer_reason,
        )
        return {
            "action": "deferred",
            "execution_id": exec_id,
            "reason": defer_reason,
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

    logger.info(
        "hop_cadence succession release terminalized exec=%s status=%s",
        exec_id,
        abort_result.get("status"),
    )
    return {
        "action": "terminalized",
        "execution_id": exec_id,
        "abort": abort_result,
    }


__all__ = [
    "predecessor_mid_turn",
    "release_superseded_on_confirm",
]
