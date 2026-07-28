"""Residue persist + gate-bypass emit helpers for harvest closeout."""

from __future__ import annotations

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import gate_bypass_detect
from .checkpoint_parse import parse_checkpoint

logger = get_logger(__name__)


def persist_residue_after_harvest(
    *,
    root_id: str,
    consumed_checkpoint_body: str,
    admission_meta: dict,
    admission_mode: str | None = None,
) -> None:
    """Record the residue the closed window CONSUMED, not the one it produced.

    The gate compares the current tip against this record, so storing the
    post-window CHECKPOINT would compare the tip against itself: no witness can
    fire against an identical witness, so every root would take an
    ``unchanged_residue`` skip per tick and stop at the threshold with no way to
    produce a newer CHECKPOINT. Thrash detection needs the pair to straddle a
    window boundary.
    """
    from .residue_fingerprint import (
        load_residue_record,
        record_from_harvest,
        save_residue_record,
    )

    parsed = parse_checkpoint(consumed_checkpoint_body)
    resolved_mode = str(
        admission_meta.get("admission_mode") or admission_mode or "generate"
    )
    window_kind = "consult" if resolved_mode == "consult" else "worker"
    prior = load_residue_record(root_id)
    w10_consumed = prior.w10_consumed if prior is not None else False
    record = record_from_harvest(
        checkpoint_body=consumed_checkpoint_body,
        parsed=parsed,
        admission_mode=resolved_mode,
        window_kind=window_kind,
        w10_consumed=w10_consumed,
    )
    save_residue_record(root_id, record)


async def flag_gate_bypass(
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


__all__ = ["flag_gate_bypass", "persist_residue_after_harvest"]
