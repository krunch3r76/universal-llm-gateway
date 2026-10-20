"""Hook producer 2 owed_wake on restart-intent terminal CAS."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def maybe_producer2_after_terminal(
    store: Any,
    intent: Any,
    *,
    deps: Any | None = None,
) -> None:
    """Best-effort drain-completion owed_wake; failures leave dispatched_at null."""
    if intent is None:
        return
    if getattr(intent, "status", None) != "completed":
        return
    lanes = list(getattr(intent, "arming_lanes", None) or [])
    caller_lane = getattr(intent, "caller_thread_id", None)
    if not lanes and not caller_lane:
        return
    try:
        from services.git_integration_worker.cse_session_holders import (
            build_owed_wake_deps,
            resolve_maestro_holder_id_for_lane,
        )

        from .owed_wake_ledger import producer2_on_restart_terminal

        lane = caller_lane or (lanes[0] if lanes else None)
        holder_id = resolve_maestro_holder_id_for_lane(lane)
        if not holder_id:
            return
        wake_deps = deps or build_owed_wake_deps()
        producer2_on_restart_terminal(
            store, intent, holder_id=holder_id, deps=wake_deps
        )
    except Exception:
        logger.exception(
            "producer2 owed_wake failed intent_id=%s", getattr(intent, "intent_id", None)
        )
