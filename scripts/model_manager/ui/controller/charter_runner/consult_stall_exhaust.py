"""Park + unenroll when consult-stall heal_cap is exhausted."""

from __future__ import annotations

from typing import Any

from cortex_store.dispatch_ops._friction_enqueue import file_charter_protocol_friction
from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client
from .caps import CapStore
from .consult_stall_build import (
    build_consult_stall_exhausted_checkpoint,
    discover_child_refs,
)
from .eligibility import Decision
from .harvest import harvest_completed_windows

logger = get_logger(__name__)


async def exhaust_consult_stall(
    decision: Decision,
    *,
    caps: CapStore,
    worker_thread: str,
    window_index: int,
    prior: Any,
    adm_n: int,
    root_turns: list[dict[str, Any]],
    heals: int,
) -> bool:
    """Fence + park CHECKPOINT + unenroll when heal_cap is exhausted.

    ``mark_failed`` alone left ``window_in_flight`` forever (WIP uncleared) and
    re-emitted ``window_failed`` every tick — SMS thrash. Clear WIP, unenroll
    (do not close the root thread — arc can re-enroll after wire fix).
    """
    friction_id = file_charter_protocol_friction(
        root_id=decision.root_id,
        window_index=window_index or 0,
        note="consult-stall heal_cap exhausted — park and unenroll",
        scoreboard_uri=prior.scoreboard_uri,
        actionable=False,
        actionable_false_reason="machine consult-stall heal_cap park",
    )
    subject, body = build_consult_stall_exhausted_checkpoint(
        prior=prior,
        window_index=window_index or 0,
        worker_thread=worker_thread,
        child_refs=discover_child_refs(root_turns, adm_n),
        generation=heals,
        friction_id=friction_id,
    )
    try:
        await bus_client.close_worker_thread(
            worker_thread,
            summary=(
                f"charter-runner consult-stall exhausted — "
                f"root {decision.root_id} · heal:consult_stall gen={heals}"
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner consult-stall exhaust fence failed %s",
            worker_thread,
        )
        return False
    try:
        await bus_client.post_root_checkpoint(
            decision.root_id, subject=subject, body=body
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner consult-stall exhaust CHECKPOINT failed root=%s",
            decision.root_id,
        )
        return False
    try:
        fresh = await bus_client.fetch_turns(decision.root_id)
        await harvest_completed_windows(decision.root_id, fresh)
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner consult-stall exhaust harvest failed root=%s",
            decision.root_id,
        )
    caps.mark_failed(decision.root_id, "no_progress:consult_stall")
    await events.emit_manage_charter_tick_window_failed(
        root=decision.root_id, reason="no_progress:consult_stall"
    )
    try:
        await bus_client.unenroll_root(decision.root_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "charter-runner consult-stall exhaust unenroll failed root=%s",
            decision.root_id,
        )
    emit = getattr(events, "emit_manage_charter_tick_consult_stall_recovered", None)
    if emit is not None:
        await emit(
            root=decision.root_id,
            reason="consult_stall_exhausted",
            window_index=window_index,
            worker_thread=worker_thread,
            heal_count=heals,
            harvested=True,
        )
    return True


__all__ = ["exhaust_consult_stall"]
