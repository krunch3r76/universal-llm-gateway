"""Per-enrollment skip side-effects for the manage-hosted charter runner."""

from __future__ import annotations

from typing import Any

from scripts.model_manager import observation_event as events

from ..admission import CapStore, CapsView, Decision
from ..root_health import FireAttemptOutcome, observe_root_health
from ..state_close import checkpoint_turn_number, emit_skip_and_maybe_state_close
from ..checkpoint_schema import item_is_gated
from ..pickup_advance import empty_hopper_row_rejections
from ..window_exec import parse_tip_checkpoint


async def apply_skip_side_effects(
    *,
    root_id: str,
    turns: list[dict[str, Any]],
    skipped_reason: str | None,
    old_decision_label: str,
    admitted: bool,
    state_closes_this_tick: int,
    skipped_by_reason: dict[str, int],
    caps: CapStore,
    fire_attempt_outcome: FireAttemptOutcome | None = None,
    fire_attempt_reason: str | None = None,
) -> int:
    """Emit skip events / state-close / SOS for one root; return new state_closes."""
    consult_pending = False
    if skipped_reason == "no_gated_pickup":
        tip = parse_tip_checkpoint(turns)
        decision = Decision(
            eligible=False,
            reason="no_gated_pickup",
            root_id=root_id,
            checkpoint=tip[0] if tip is not None else None,
            parsed=tip[1] if tip is not None else None,
        )
        state_closes_this_tick = await emit_skip_and_maybe_state_close(
            decision,
            state_closes_this_tick=state_closes_this_tick,
            skipped_by_reason=skipped_by_reason,
            caps=caps,
        )
    elif skipped_reason == "empty_hopper":
        tip = parse_tip_checkpoint(turns)
        ckpt = tip[0] if tip is not None else None
        parsed = tip[1] if tip is not None else None
        consult_pending = bool(parsed is not None and parsed.consult_pending)
        row_rejections = empty_hopper_row_rejections(parsed)
        rows_considered = 0
        if parsed is not None:
            rows_considered = sum(
                1 for row in parsed.next_pickup if item_is_gated(row)
            )
        skipped_by_reason["empty_hopper"] = (
            skipped_by_reason.get("empty_hopper", 0) + 1
        )
        await events.emit_manage_charter_tick_root_skipped(
            root=root_id,
            reason="empty_hopper",
            checkpoint_turn=checkpoint_turn_number(ckpt),
            row_rejections=row_rejections,
            rows_considered=rows_considered,
            predicate_id="tip_is_empty_hopper",
        )
    elif skipped_reason == "dormant":
        tip = parse_tip_checkpoint(turns)
        ckpt = tip[0] if tip is not None else None
        skipped_by_reason["dormant"] = skipped_by_reason.get("dormant", 0) + 1
        await events.emit_manage_charter_tick_root_skipped(
            root=root_id,
            reason="dormant",
            checkpoint_turn=checkpoint_turn_number(ckpt),
        )
    elif skipped_reason:
        skipped_by_reason[skipped_reason] = (
            skipped_by_reason.get(skipped_reason, 0) + 1
        )
    elif old_decision_label == "kernel_unseeded":
        skipped_by_reason["kernel_unseeded"] = (
            skipped_by_reason.get("kernel_unseeded", 0) + 1
        )
        await events.emit_manage_charter_tick_root_skipped(
            root=root_id,
            reason="kernel_unseeded",
            checkpoint_turn=None,
        )

    caps_view = CapsView.from_cap_store(caps, root_id)
    stopped_reason = caps_view.stopped_reason
    if (
        fire_attempt_outcome is None
        and not admitted
        and old_decision_label == "NOOP"
        and skipped_reason is None
    ):
        from ..root_ledger import load_root, open_default_ledger

        conn = open_default_ledger()
        try:
            row = load_root(conn, root_id)
            if row is not None and row.status.value in {
                "ADMITTED",
                "CONSULT_ADMITTED",
            }:
                from ..giw_live_hold import fetch_giw_active_work_payload
                from ..tick_sos_liveness import classify_sticky_backing

                try:
                    giw_payload = await fetch_giw_active_work_payload()
                except Exception:  # noqa: BLE001
                    giw_payload = None
                backing, _holder = classify_sticky_backing(giw_payload)
                if backing == "orphan":
                    fire_attempt_outcome = FireAttemptOutcome.INTEGRITY
                    fire_attempt_reason = "orphan_holder_no_live_backing"
                else:
                    fire_attempt_outcome = FireAttemptOutcome.WAITING_ON_WORKER
                    fire_attempt_reason = "sticky_admitted"
        finally:
            conn.close()

    await observe_root_health(
        root_id,
        fire_attempt_outcome=fire_attempt_outcome,
        fire_attempt_reason=fire_attempt_reason,
        skipped_reason=skipped_reason,
        consult_pending=consult_pending,
        stopped_reason=stopped_reason,
        admitted=admitted,
        old_decision_label=old_decision_label,
    )
    return state_closes_this_tick


__all__ = ["apply_skip_side_effects"]
