"""Best-effort owed_wake producer 1 and lane-activity close hooks on bus writes."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def after_turn_created(
    *,
    thread_id: str,
    to_agent: str,
    from_agent: str,
    subject: str,
    turn_number: int,
    user_agent: str | None = None,
) -> dict[str, Any] | None:
    """Close on holder posts; record producer 1 milestones (dispatch is deferred)."""
    lane = (thread_id or "").strip()
    if not lane:
        return None
    try:
        from services.git_integration_worker.cse_session_holders import (
            close_owed_wake_on_lane_activity,
            on_house_milestone_to_maestro,
        )

        if (from_agent or "").strip().lower() != "manage":
            close_owed_wake_on_lane_activity(
                lane_thread_id=lane,
                seat_agent=from_agent,
            )
        return on_house_milestone_to_maestro(
            subject=subject,
            to_agent=to_agent,
            from_agent=from_agent,
            thread_id=lane,
            turn_number=turn_number,
            lane_thread_id=lane,
            user_agent=user_agent,
        )
    except Exception:
        logger.warning(
            "owed_wake after_turn_created failed thread=%s turn=%s",
            lane,
            turn_number,
            exc_info=True,
        )
        return None


def after_lane_mark_read(*, thread_id: str, seat_agent: str | None = None) -> None:
    """Close open owed_wake rows when the holder seat marks read on the lane thread."""
    lane = (thread_id or "").strip()
    if not lane:
        return
    try:
        from services.git_integration_worker.cse_session_holders import (
            close_owed_wake_on_lane_activity,
        )

        close_owed_wake_on_lane_activity(
            lane_thread_id=lane,
            seat_agent=seat_agent,
        )
    except Exception:
        logger.warning(
            "owed_wake after_lane_mark_read failed thread=%s",
            lane,
            exc_info=True,
        )


def schedule_producer1_dispatch_from_result(result: dict[str, Any] | None) -> None:
    """Fire-and-forget producer-1 CSE follow-up when a row was just created."""
    if not result or not result.get("needs_deferred_dispatch"):
        return
    row_id = result.get("row_id")
    if not isinstance(row_id, str) or not row_id.strip():
        return
    from services.git_integration_worker.cse_session_holders import (
        dispatch_owed_wake_producer1_row,
    )

    dispatch_owed_wake_producer1_row(row_id.strip())
