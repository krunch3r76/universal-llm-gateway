"""Post-harvest friction audit / conveyor enroll for window terminals."""

from __future__ import annotations

from universal_logging import get_logger

logger = get_logger(__name__)


async def after_window_terminal_harvested(
    *,
    root_id: str,
    window_index: int,
    checkpoint_turn: int,
    checkpoint_subject: str,
    checkpoint_body: str,
    worker_turns: list[dict],
    worker_closed: bool | None,
    gate_bypass_count: int,
) -> None:
    """Post-close hook: friction audit → G3 mint → reconcile → conveyor enroll."""
    from cortex_store.dispatch_ops._friction_enqueue import (
        mint_friction_followon,
        mint_repair_todo,
        reconcile_charter_frictions,
        todo_exists_for_friction,
    )
    from cortex_store.dispatch_ops.ops_assertions import _op_frictions
    from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get

    from scripts.model_manager import observation_event as events

    from . import bus_client, conveyor, frictions_window_audit

    _ = checkpoint_turn  # bound in contract signature for closeout correlation

    closeout_status = bus_client.closeout_status_from_turns(worker_turns)
    audit = frictions_window_audit.audit_window_frictions(
        checkpoint_body=checkpoint_body,
        root_id=root_id,
        window_index=window_index,
        assertion_get=lambda aid: _op_assertion_get(assertion_id=aid),
        frictions=_op_frictions,
        worker_closeout_status=closeout_status,
        checkpoint_subject=checkpoint_subject,
        worker_closed=worker_closed,
        gate_bypass_count=gate_bypass_count,
        worker_turns=worker_turns,
    )
    if not audit.applicable:
        await events.emit_manage_charter_tick_frictions_audit_not_applicable(
            root=root_id,
            window_index=window_index,
            reason=audit.not_applicable_reason or "not_applicable",
        )
        try:
            reconcile_charter_frictions(root_id)
        except Exception:  # noqa: BLE001
            logger.exception("charter-runner friction reconcile sweep failed")
        return

    if audit.audit_failed:
        await events.emit_manage_charter_tick_frictions_audit_failed(
            root=root_id,
            window_index=window_index,
            failure_class=audit.audit_failure_class or "unknown",
            non_actionable_rate=audit.non_actionable_rate,
        )
        try:
            mint_repair_todo(
                root_id=root_id,
                window_index=window_index,
                audit_failure_class=audit.audit_failure_class or "unknown",
            )
        except Exception:  # noqa: BLE001
            logger.exception("charter-runner repair todo mint failed")
    else:
        await events.emit_manage_charter_tick_frictions_audit_passed(
            root=root_id,
            window_index=window_index,
            non_actionable_rate=audit.non_actionable_rate,
        )

    if audit.uncited_ids:
        await events.emit_manage_charter_tick_frictions_filed_uncited(
            root=root_id,
            window_index=window_index,
            uncited_ids=sorted(audit.uncited_ids),
        )

    if audit.ceremonial_suspected:
        await events.emit_manage_charter_tick_frictions_ceremonial_suspected(
            root=root_id,
            window_index=window_index,
            non_actionable_rate=audit.non_actionable_rate,
        )

    for row in audit.resolved_actionable_rows:
        try:
            got = _op_assertion_get(assertion_id=row.assertion_id)
            if "error" not in got:
                slug = mint_friction_followon(got, root_id=root_id)
                if slug:
                    audit.enqueued_ids.add(row.assertion_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "charter-runner friction enqueue failed id=%s", row.assertion_id
            )

    for fid in audit.uncited_ids:
        try:
            got = _op_assertion_get(assertion_id=fid)
            if "error" not in got:
                slug = mint_friction_followon(got, root_id=root_id)
                if slug:
                    audit.enqueued_ids.add(fid)
        except Exception:  # noqa: BLE001
            logger.exception("charter-runner uncited friction enqueue failed id=%s", fid)

    try:
        reconcile_charter_frictions(root_id)
    except Exception:  # noqa: BLE001
        logger.exception("charter-runner friction reconcile sweep failed")

    try:
        detail = await bus_client.fetch_thread(root_id)
        tags = list(detail.get("tags") or [])
        friction_resp = _op_frictions(
            charter_root=root_id,
            superseded=False,
            limit=200,
            intent="full",
        )
        friction_items = [
            item
            for item in friction_resp.get("items") or []
            if isinstance(item, dict) and todo_exists_for_friction(int(item["id"]))
        ]
        await conveyor.enroll_rows(
            root_id=root_id,
            root_tags=tags,
            friction_rows=friction_items,
        )
    except Exception as exc:  # noqa: BLE001 — surface; do not abort harvest closeout
        logger.exception("charter-runner conveyor enroll failed root=%s", root_id)
        try:
            from scripts.model_manager import observation_event_conveyor as conv_events

            await conv_events.emit_manage_charter_conveyor_enroll_failed(
                root=root_id,
                window_index=window_index,
                error=f"{type(exc).__name__}: {exc}",
                minted_count=len(audit.enqueued_ids),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "charter-runner failed emitting conveyor enroll_failed root=%s",
                root_id,
            )
        try:
            mint_repair_todo(
                root_id=root_id,
                window_index=window_index,
                audit_failure_class="conveyor_enroll_failed",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "charter-runner conveyor enroll repair-todo mint failed root=%s",
                root_id,
            )

__all__ = ["after_window_terminal_harvested"]
