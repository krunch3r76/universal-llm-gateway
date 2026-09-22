"""CSE seating hook — occupy holder row on continuity hop (R-7 G5 path)."""

from __future__ import annotations

from typing import Any

from hop_handoff import parse_occupy_target, parse_superseded_registration_id
from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.hop_cadence_home_lane import (
    watch_thread_for_job,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

__all__ = ["run_cse_seating_hook"]


def _resolve_successor_identity(
    job: AutoJob,
    execution_id: str,
) -> tuple[str | None, str | None]:
    """Best-effort registration/chat_url for the commissioned successor."""
    lane = watch_thread_for_job(job)
    exec_id = (execution_id or "").strip()
    if not lane or not exec_id:
        return None, None
    try:
        from claude_bundles.cdp_registry.session_address import (
            chat_url_for_registration,
        )

        from services.git_integration_worker.cursor_auto.cdp_escalation import (
            read_cdp_lane_snapshot,
        )
        from services.git_integration_worker.cursor_auto.hop_cadence_predecessor import (
            op_row_for_execution_on_lane,
        )

        snap = read_cdp_lane_snapshot()
        if not isinstance(snap, dict):
            return None, None
        aw_row = op_row_for_execution_on_lane(snap, lane, exec_id)
        if aw_row is None:
            return None, None
        reg = str(aw_row.get("registration_id") or "").strip() or None
        chat = str(aw_row.get("chat_url") or "").strip() or None
        if not chat and reg:
            chat = (chat_url_for_registration(reg) or "").strip() or None
        return reg, chat
    except Exception as exc:  # noqa: BLE001 — hook must not crash hop
        logger.warning(
            "cse_seating_hook identity resolve failed job=%s: %s",
            job.job_id,
            exc,
        )
        return None, None


def run_cse_seating_hook(
    job: AutoJob,
    *,
    execution_id: str,
) -> dict[str, Any]:
    """Upsert the occupy-target holder row and supersede predecessor on hop.

    Returns a payload with ``path`` naming which branch ran. Emits
    ``giw.cursor_auto.cse_seating_hook`` for path-scoped regression tests.
    """
    lane = watch_thread_for_job(job)
    occupy = parse_occupy_target(job.body) or (job.cse_chat_url or "").strip() or None
    superseded = (
        parse_superseded_registration_id(job.body)
        or (job.cse_registration_id or "").strip()
        or None
    )
    if not occupy:
        outcome: dict[str, Any] = {
            "ok": False,
            "path": "skipped_no_occupy_target",
            "reason": "no_occupy_target",
            "thread_id": str(job.thread_id),
        }
        _emit_hook(outcome)
        return outcome

    new_reg, _new_chat = _resolve_successor_identity(job, execution_id)
    # No successor CSE yet (IDE hop). The wire id is the admission identity;
    # leaving the holder null makes the next census miss the seat we just named.
    if new_reg:
        occupy_reg: str | None = new_reg
        prior = superseded
    else:
        occupy_reg = superseded
        prior = None
    from services.git_integration_worker.cse_session_holders import (
        ensure_schema,
        occupy_holder_on_hop,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        ensure_schema(conn)
        outcome = occupy_holder_on_hop(
            conn,
            occupy_target=occupy,
            lane_thread_id=lane,
            superseded_registration_id=prior,
            new_registration_id=occupy_reg,
            new_execution_id=(execution_id or "").strip() or None,
        )
        conn.commit()
    outcome["thread_id"] = str(job.thread_id)
    outcome["occupy_target"] = occupy
    outcome["superseded_registration_id"] = superseded
    _emit_hook(outcome)
    return outcome


def _emit_hook(payload: dict[str, Any]) -> None:
    from services.git_integration_worker.events import publish_lib_signal

    try:
        publish_lib_signal("giw.cursor_auto.cse_seating_hook", payload)
    except Exception as exc:  # noqa: BLE001 — observability must not block hop
        logger.debug("cse_seating_hook signal failed: %s", exc)
