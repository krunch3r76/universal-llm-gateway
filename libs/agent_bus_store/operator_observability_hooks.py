"""Best-effort owed_wake producer 1 and lane-activity close hooks on bus writes."""

from __future__ import annotations

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
) -> None:
    """Close open owed_wake rows on lane posts; arm producer 1 for maestro milestones."""
    lane = (thread_id or "").strip()
    if not lane:
        return
    try:
        from services.git_integration_worker.cse_session_holders import (
            close_owed_wake_on_lane_activity,
            on_house_milestone_to_maestro,
        )

        if (from_agent or "").strip().lower() != "manage":
            close_owed_wake_on_lane_activity(lane_thread_id=lane)
        on_house_milestone_to_maestro(
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


def after_lane_mark_read(*, thread_id: str) -> None:
    """Close open owed_wake rows when a holder marks read on the lane thread."""
    lane = (thread_id or "").strip()
    if not lane:
        return
    try:
        from services.git_integration_worker.cse_session_holders import (
            close_owed_wake_on_lane_activity,
        )

        close_owed_wake_on_lane_activity(lane_thread_id=lane)
    except Exception:
        logger.warning(
            "owed_wake after_lane_mark_read failed thread=%s",
            lane,
            exc_info=True,
        )
