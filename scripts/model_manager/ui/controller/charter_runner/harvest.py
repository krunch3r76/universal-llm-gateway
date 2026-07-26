"""Harvest completed charter-runner windows into transcripts + closed events."""

from __future__ import annotations

from cortex_store.dispatch_ops._friction_enqueue import (
    mint_friction_followon,
    mint_repair_todo,
    reconcile_charter_frictions,
    todo_exists_for_friction,
)
from cortex_store.dispatch_ops.ops_assertions import _op_frictions
from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get
from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import (
    bus_client,
    conveyor,
    frictions_window_audit,
    gate_bypass_detect,
    window_log,
)
from .checkpoint_body import resolve_checkpoint_body
from .eligibility import ADMISSION_SUBJECT_PREFIX, CHECKPOINT_PREFIX

logger = get_logger(__name__)


def _persist_residue_after_harvest(
    *,
    root_id: str,
    consumed_checkpoint_body: str,
    admission_meta: dict,
) -> None:
    """Record the residue the closed window CONSUMED, not the one it produced.

    The gate compares the current tip against this record, so storing the
    post-window CHECKPOINT would compare the tip against itself: no witness can
    fire against an identical witness, so every root would take an
    ``unchanged_residue`` skip per tick and stop at the threshold with no way to
    produce a newer CHECKPOINT. Thrash detection needs the pair to straddle a
    window boundary.
    """
    from .checkpoint_parse import parse_checkpoint
    from .residue_fingerprint import (
        load_residue_record,
        record_from_harvest,
        save_residue_record,
    )
    from .tick_loop import _admission_mode

    parsed = parse_checkpoint(consumed_checkpoint_body)
    admission_mode = str(admission_meta.get("admission_mode") or _admission_mode())
    window_kind = "consult" if admission_mode == "consult" else "worker"
    prior = load_residue_record(root_id)
    w10_consumed = prior.w10_consumed if prior is not None else False
    record = record_from_harvest(
        checkpoint_body=consumed_checkpoint_body,
        parsed=parsed,
        admission_mode=admission_mode,
        window_kind=window_kind,
        w10_consumed=w10_consumed,
    )
    save_residue_record(root_id, record)


async def _flag_gate_bypass(
    *,
    root_id: str,
    window_index: int,
    worker_thread: str,
    worker_turns: list[dict],
) -> None:
    """Emit the second detector's signal for any ungated implement closeout."""
    for finding in gate_bypass_detect.detect_gate_bypass(worker_turns):
        logger.error(
            "charter-runner window %s (root=%s) closed out ungated: worker closeout "
            "t%s reported %s dispatch=%s source_ref=%s — require_implement_ready "
            "no-opped; treat this window's output as unreviewed",
            window_index,
            root_id,
            finding.turn_number,
            gate_bypass_detect.GATE_BYPASS_DEVIATION,
            finding.dispatch_id,
            finding.source_ref,
        )
        await events.emit_manage_charter_implement_gate_bypassed(
            root=root_id,
            window_index=window_index,
            worker_thread=worker_thread,
            dispatch_id=finding.dispatch_id,
            source_ref=finding.source_ref,
            turn_number=finding.turn_number,
        )


async def _audit_and_enqueue_frictions(
    *,
    root_id: str,
    window_index: int,
    checkpoint_subject: str,
    checkpoint_body: str,
    worker_turns: list[dict],
    worker_closed: bool | None,
    gate_bypass_count: int,
) -> None:
    """Post-window Frictions audit, row-scoped enqueue, repair todo, sweep."""
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
            if isinstance(item, dict)
            and todo_exists_for_friction(int(item["id"]))
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
            # Repair todo (not a new actionable friction) — avoids enroll cascade.
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


def turn_number(turn: dict) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def completed_windows(turns: list[dict]) -> list[tuple[dict, dict]]:
    """Pairs of (admission, following CHECKPOINT) for closed windows."""
    ordered = sorted(turns, key=turn_number)
    pairs: list[tuple[dict, dict]] = []
    adm_prefix = ADMISSION_SUBJECT_PREFIX.upper()
    cp_prefix = CHECKPOINT_PREFIX.upper()
    for i, turn in enumerate(ordered):
        subj = str(turn.get("subject") or "").upper()
        if not subj.startswith(adm_prefix):
            continue
        n = turn_number(turn)
        following_cp = None
        for later in ordered[i + 1 :]:
            if turn_number(later) <= n:
                continue
            later_subj = str(later.get("subject") or "").upper()
            if later_subj.startswith(cp_prefix):
                following_cp = later
                break
        if following_cp is not None:
            pairs.append((turn, following_cp))
    return pairs


def consumed_checkpoint(turns: list[dict], admission: dict) -> dict | None:
    """Latest CHECKPOINT preceding an admission — the residue that window ran on."""
    adm_n = turn_number(admission)
    cp_prefix = CHECKPOINT_PREFIX.upper()
    best: dict | None = None
    for turn in turns:
        n = turn_number(turn)
        if n >= adm_n or n <= 0:
            continue
        if not str(turn.get("subject") or "").upper().startswith(cp_prefix):
            continue
        if best is None or n > turn_number(best):
            best = turn
    return best


async def harvest_completed_windows(root_id: str, turns: list[dict]) -> None:
    """Append worker turns + CHECKPOINT for windows that closed since last tick."""
    for admission, checkpoint in completed_windows(turns):
        meta = window_log.parse_admission_meta(str(admission.get("body") or ""))
        try:
            window_index = int(meta.get("window") or 0)
        except (TypeError, ValueError):
            window_index = 0
        # Durable harvested markers (outside /tmp) make this restart-safe (A-R3-3).
        if window_index <= 0 or window_log.already_harvested(root_id, window_index):
            continue
        worker_thread = str(meta.get("worker_thread") or "")
        worker_turns: list[dict] = []
        if worker_thread:
            try:
                worker_turns = await bus_client.fetch_turns(worker_thread)
            except Exception:  # noqa: BLE001 — closeout still records CHECKPOINT
                logger.exception(
                    "charter-runner failed fetching worker %s", worker_thread
                )
        try:
            await _flag_gate_bypass(
                root_id=root_id,
                window_index=window_index,
                worker_thread=worker_thread,
                worker_turns=worker_turns,
            )
        except Exception:  # noqa: BLE001 — a detector must never abort the tick
            logger.exception("charter-runner gate-bypass detection failed")
        gate_bypass_count = len(gate_bypass_detect.detect_gate_bypass(worker_turns))
        worker_closed: bool | None = None
        if worker_thread:
            try:
                await bus_client.close_worker_thread(
                    worker_thread,
                    summary=(
                        f"charter-runner window {window_index} complete — "
                        f"root {root_id} CHECKPOINT "
                        f"{checkpoint.get('subject') or ''}"
                    ),
                )
                worker_closed = True
            except Exception:  # noqa: BLE001 — transcript still records failure
                worker_closed = False
                logger.exception(
                    "charter-runner failed closing worker %s", worker_thread
                )
        try:
            await _audit_and_enqueue_frictions(
                root_id=root_id,
                window_index=window_index,
                checkpoint_subject=str(checkpoint.get("subject") or ""),
                checkpoint_body=resolve_checkpoint_body(
                    str(checkpoint.get("body") or ""),
                    sidecar_uri=(
                        checkpoint.get("sidecar_uri")
                        if isinstance(checkpoint.get("sidecar_uri"), str)
                        else None
                    ),
                ),
                worker_turns=worker_turns,
                worker_closed=worker_closed,
                gate_bypass_count=gate_bypass_count,
            )
        except Exception:  # noqa: BLE001 — audit must never abort harvest
            logger.exception("charter-runner frictions audit failed")
        try:
            window_log.append_closeout(
                root_id=root_id,
                window_index=window_index,
                worker_thread=worker_thread,
                checkpoint_subject=str(checkpoint.get("subject") or ""),
                checkpoint_body=str(checkpoint.get("body") or ""),
                worker_turns=worker_turns,
                worker_closed=worker_closed,
            )
            consumed = consumed_checkpoint(turns, admission)
            if consumed is None:
                # Leaving the store untouched is the safe branch: writing this
                # window's own CHECKPOINT would deadlock the next tick.
                logger.warning(
                    "charter-runner window %s (root=%s) has no preceding "
                    "CHECKPOINT — last-residue store left unchanged",
                    window_index,
                    root_id,
                )
            else:
                _persist_residue_after_harvest(
                    root_id=root_id,
                    consumed_checkpoint_body=resolve_checkpoint_body(
                        str(consumed.get("body") or ""),
                        sidecar_uri=(
                            consumed.get("sidecar_uri")
                            if isinstance(consumed.get("sidecar_uri"), str)
                            else None
                        ),
                    ),
                    admission_meta=meta,
                )
            await events.emit_manage_charter_tick_closed(
                root=root_id,
                window_index=window_index,
                worker_thread=worker_thread,
                checkpoint_turn=turn_number(checkpoint),
                worker_closed=worker_closed,
            )
        except Exception:  # noqa: BLE001 — transcript must not kill the tick
            logger.exception("charter-runner window_log append_closeout failed")
