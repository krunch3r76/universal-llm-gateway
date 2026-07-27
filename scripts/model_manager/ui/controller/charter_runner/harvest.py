"""Harvest completed charter-runner windows into transcripts + closed events."""

from __future__ import annotations

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import (
    bus_client,
    gate_bypass_detect,
    window_log,
)
from .checkpoint_body import resolve_checkpoint_body
from .eligibility import ADMISSION_SUBJECT_PREFIX
from .window_terminal_contract import after_window_terminal_harvested, is_tip_class

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
    from .attendance import admission_mode_for_root

    parsed = parse_checkpoint(consumed_checkpoint_body)
    admission_mode = str(
        admission_meta.get("admission_mode") or admission_mode_for_root(root_id)
    )
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


def turn_number(turn: dict) -> int:
    try:
        return int(turn.get("turn_number") or 0)
    except (TypeError, ValueError):
        return 0


def completed_windows(turns: list[dict]) -> list[tuple[dict, dict]]:
    """Pairs of (admission, following tip-class terminal) for closed windows."""
    ordered = sorted(turns, key=turn_number)
    pairs: list[tuple[dict, dict]] = []
    adm_prefix = ADMISSION_SUBJECT_PREFIX.upper()
    for i, turn in enumerate(ordered):
        subj = str(turn.get("subject") or "").upper()
        if not subj.startswith(adm_prefix):
            continue
        n = turn_number(turn)
        following_cp = None
        for later in ordered[i + 1 :]:
            if turn_number(later) <= n:
                continue
            subj_later = str(later.get("subject") or "")
            body_later = str(later.get("body") or "")
            if is_tip_class(subj_later, body=body_later):
                following_cp = later
                break
        if following_cp is not None:
            pairs.append((turn, following_cp))
    return pairs


def consumed_checkpoint(turns: list[dict], admission: dict) -> dict | None:
    """Latest tip-class terminal preceding an admission — residue that window ran on."""
    adm_n = turn_number(admission)
    best: dict | None = None
    for turn in turns:
        n = turn_number(turn)
        if n >= adm_n or n <= 0:
            continue
        subj = str(turn.get("subject") or "")
        body = str(turn.get("body") or "")
        if not is_tip_class(subj, body=body):
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
        resolved_body = resolve_checkpoint_body(
            str(checkpoint.get("body") or ""),
            sidecar_uri=(
                checkpoint.get("sidecar_uri")
                if isinstance(checkpoint.get("sidecar_uri"), str)
                else None
            ),
        )
        try:
            await after_window_terminal_harvested(
                root_id=root_id,
                window_index=window_index,
                checkpoint_turn=turn_number(checkpoint),
                checkpoint_subject=str(checkpoint.get("subject") or ""),
                checkpoint_body=resolved_body,
                worker_turns=worker_turns,
                worker_closed=worker_closed,
                gate_bypass_count=gate_bypass_count,
            )
        except Exception:  # noqa: BLE001 — audit must never abort harvest
            logger.exception("charter-runner frictions audit failed")
        try:
            from .propagation_execute import maybe_execute_window_propagation

            await maybe_execute_window_propagation(
                root_id=root_id,
                window_index=window_index,
                worker_turns=worker_turns,
            )
        except Exception:  # noqa: BLE001 — propagation must not abort harvest
            logger.exception("charter-runner propagation execute failed")
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
