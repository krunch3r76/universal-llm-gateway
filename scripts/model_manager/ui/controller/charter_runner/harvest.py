"""Harvest completed charter-runner windows into transcripts + closed events."""

from __future__ import annotations

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client, gate_bypass_detect, window_log
from .eligibility import ADMISSION_SUBJECT_PREFIX, CHECKPOINT_PREFIX

logger = get_logger(__name__)


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
            window_log.append_closeout(
                root_id=root_id,
                window_index=window_index,
                worker_thread=worker_thread,
                checkpoint_subject=str(checkpoint.get("subject") or ""),
                checkpoint_body=str(checkpoint.get("body") or ""),
                worker_turns=worker_turns,
                worker_closed=worker_closed,
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
