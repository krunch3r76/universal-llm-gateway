"""Best-effort owed_wake producer 1 and lane-activity close hooks on bus writes."""

from __future__ import annotations

import threading
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_active_producer1_dispatch_threads: set[threading.Thread] = set()
_producer1_dispatch_lock = threading.Lock()


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


def _run_deferred_producer1_dispatch(row_id: str) -> None:
    from services.git_integration_worker.cse_session_holders import (
        dispatch_owed_wake_producer1_row,
    )

    dispatch_owed_wake_producer1_row(row_id)


def _unregister_producer1_dispatch_thread(thread: threading.Thread) -> None:
    with _producer1_dispatch_lock:
        _active_producer1_dispatch_threads.discard(thread)


def flush_scheduled_producer1_dispatches(timeout: float = 5.0) -> None:
    """Join in-flight producer-1 dispatch threads spawned by ``schedule_producer1_dispatch_from_result``.

    Tests call this before asserting on dispatch side effects. Production callers normally
    rely on fire-and-forget; joining waits for CSE / HTTP I/O started on background threads.
    """
    with _producer1_dispatch_lock:
        threads = list(_active_producer1_dispatch_threads)
    for thread in threads:
        thread.join(timeout=timeout)


def schedule_producer1_dispatch_from_result(result: dict[str, Any] | None) -> None:
    """Enqueue producer-1 CSE follow-up on a daemon thread when a row was just created.

    When ``needs_deferred_dispatch`` is set, starts ``dispatch_owed_wake_producer1_row`` on a
    background thread and returns immediately without blocking on CSE or HTTP I/O. No-op when
    the result is missing, lacks the flag, or has an invalid ``row_id``. Use
    ``flush_scheduled_producer1_dispatches`` to wait for enqueued work (mainly in tests).
    """
    if not result or not result.get("needs_deferred_dispatch"):
        return
    row_id = result.get("row_id")
    if not isinstance(row_id, str) or not row_id.strip():
        return

    def _target() -> None:
        try:
            _run_deferred_producer1_dispatch(row_id.strip())
        finally:
            _unregister_producer1_dispatch_thread(threading.current_thread())

    thread = threading.Thread(target=_target, daemon=True)
    with _producer1_dispatch_lock:
        _active_producer1_dispatch_threads.add(thread)
    thread.start()
