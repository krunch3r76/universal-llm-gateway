"""Harvest completed charter-runner windows into transcripts + closed events."""

from __future__ import annotations

from pager_notify.tick import ClosedAttribution
from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import (
    bus_client,
    gate_bypass_detect,
    window_log,
)
from .admission import ADMISSION_SUBJECT_PREFIX
from .checkpoint_schema import resolve_checkpoint_body
from .harvest_attribution import attribution_for_harvested_window
from .harvest_cdp import maybe_harvest_cdp_consult_provenance
from .harvest_footer_gate import (
    footer_field_path,
    is_machine_authored_checkpoint,
    reject_harvest_without_footer,
)
from .harvest_side_effects import flag_gate_bypass, persist_residue_after_harvest
from .window_sequence import release_window_on_harvest
from .window_terminal_contract import after_window_terminal_harvested, is_tip_class

# Test/compat alias — body lives in harvest_side_effects.
_persist_residue_after_harvest = persist_residue_after_harvest

logger = get_logger(__name__)


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


async def harvest_completed_windows(
    root_id: str,
    turns: list[dict],
    *,
    admission_mode: str | None = None,
) -> list[ClosedAttribution]:
    """Append worker turns + CHECKPOINT for windows that closed since last tick.

    Returns SMS close attributions for windows newly harvested in this call.
    """
    attributions: list[ClosedAttribution] = []
    thread_slug = ""
    so_what = ""
    try:
        detail = await bus_client.fetch_thread(root_id)
        thread_slug = str(detail.get("slug") or "")
        so_what = str(detail.get("summary") or "")
        if isinstance(detail.get("thread"), dict):
            nested = detail.get("thread") or {}
            if not thread_slug:
                thread_slug = str(nested.get("slug") or "")
            if not so_what:
                so_what = str(nested.get("summary") or "")
    except Exception:  # noqa: BLE001 — slug/so-what are optional SMS garnish
        logger.debug("charter-runner harvest slug fetch failed root=%s", root_id)
    for admission, checkpoint in completed_windows(turns):
        meta = window_log.parse_admission_meta(str(admission.get("body") or ""))
        try:
            window_index = int(meta.get("window") or 0)
        except (TypeError, ValueError):
            window_index = 0
        # Durable harvested markers (outside /tmp) make this restart-safe (A-R3-3).
        if window_index <= 0 or window_log.already_harvested(root_id, window_index):
            continue
        checkpoint_subject = str(checkpoint.get("subject") or "")
        resolved_body = resolve_checkpoint_body(
            str(checkpoint.get("body") or ""),
            sidecar_uri=(
                checkpoint.get("sidecar_uri")
                if isinstance(checkpoint.get("sidecar_uri"), str)
                else None
            ),
        )
        if reject_harvest_without_footer(
            root_id=root_id,
            window_index=window_index,
            checkpoint_subject=checkpoint_subject,
            checkpoint_body=resolved_body,
        ):
            # A rejected window stays rejected until its body changes, so the reject
            # event fires once per bad CHECKPOINT rather than once per tick (a:26601).
            body_sha = window_log.checkpoint_body_sha(resolved_body)
            if window_log.already_marked(
                root_id, window_index, kind="rejected", token=body_sha
            ):
                continue
            window_log.mark(root_id, window_index, kind="rejected", token=body_sha)
            _, field_path = footer_field_path(resolved_body)
            try:
                await events.emit_manage_charter_tick_harvest_rejected(
                    root=root_id,
                    window_index=window_index,
                    field_path=field_path or "charter-state invalid",
                    checkpoint_subject=checkpoint_subject or None,
                )
            except Exception:  # noqa: BLE001 — emit must not unblock reject
                logger.exception(
                    "charter-runner harvest_rejected emit failed root=%s",
                    root_id,
                )
            continue
        # C2 / P3-AC3 instrument: machine self-heal + consult-stall subjects bypass
        # the footer gate — emit so the carve-out cannot silently vacate AC3.
        if is_machine_authored_checkpoint(checkpoint_subject):
            carve_sha = window_log.checkpoint_body_sha(resolved_body)
            if not window_log.already_marked(
                root_id, window_index, kind="footer_carveout", token=carve_sha
            ):
                window_log.mark(
                    root_id, window_index, kind="footer_carveout", token=carve_sha
                )
                try:
                    await events.emit_manage_charter_tick_harvest_footer_carveout(
                        root=root_id,
                        window_index=window_index,
                        checkpoint_subject=checkpoint_subject,
                    )
                except Exception:  # noqa: BLE001 — emit must not block harvest
                    logger.exception(
                        "charter-runner harvest_footer_carveout emit failed root=%s",
                        root_id,
                    )
        # Claim the window before side effects. Mid-block raises after this cannot
        # re-enter harvest and re-emit closed / re-release ledger WIP (a:26596).
        # append_closeout re-marks idempotently.
        window_log.mark_harvested(root_id, window_index)
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
            await flag_gate_bypass(
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
                from pager_notify.so_what import compose_done_summary

                worker_prior = ""
                try:
                    wdetail = await bus_client.fetch_thread(worker_thread)
                    worker_prior = str(wdetail.get("summary") or "")
                    if not worker_prior and isinstance(wdetail.get("thread"), dict):
                        worker_prior = str(
                            (wdetail.get("thread") or {}).get("summary") or ""
                        )
                except Exception:  # noqa: BLE001 — close still proceeds
                    worker_prior = ""
                await bus_client.close_worker_thread(
                    worker_thread,
                    summary=compose_done_summary(
                        worker_prior or so_what,
                        reason=(f"window {window_index} complete — root {root_id}"),
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
                checkpoint_subject=checkpoint_subject,
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
            provenance: dict[str, str] | None = None
            provenance = await maybe_harvest_cdp_consult_provenance(
                root_id=root_id,
                window_index=window_index,
                worker_thread=worker_thread,
                worker_turns=worker_turns,
                admission_meta=meta,
            )
            window_log.append_closeout(
                root_id=root_id,
                window_index=window_index,
                worker_thread=worker_thread,
                checkpoint_subject=checkpoint_subject,
                checkpoint_body=str(checkpoint.get("body") or ""),
                worker_turns=worker_turns,
                worker_closed=worker_closed,
            )
            # HARVEST_OK: the closed window becomes the ledger's last and its WIP is
            # released. The pickup is the kernel's to advance, from the tip.
            release_window_on_harvest(root_id, window_index)
            consumed = consumed_checkpoint(turns, admission)
            if consumed is None:
                logger.warning(
                    "charter-runner window %s (root=%s) has no preceding "
                    "CHECKPOINT — last-residue store left unchanged",
                    window_index,
                    root_id,
                )
            else:
                consumed_body = resolve_checkpoint_body(
                    str(consumed.get("body") or ""),
                    sidecar_uri=(
                        consumed.get("sidecar_uri")
                        if isinstance(consumed.get("sidecar_uri"), str)
                        else None
                    ),
                )
                persist_residue_after_harvest(
                    root_id=root_id,
                    consumed_checkpoint_body=consumed_body,
                    admission_meta=meta,
                    admission_mode=admission_mode,
                )
                attr = attribution_for_harvested_window(
                    root_id=root_id,
                    consumed_checkpoint_body=consumed_body,
                    admission_mode=str(meta.get("admission_mode") or "generate"),
                    thread_slug=thread_slug,
                    completing_subject=checkpoint_subject,
                    window_index=window_index,
                    so_what=so_what,
                )
                if attr is not None:
                    attributions.append(attr)
            # Durable per-(window, worker) marker: the harvested marker above can be
            # missed when an earlier step in this block raises, and the next tick
            # would then re-announce a close that already happened (a:26592 class).
            closed_token = worker_thread or f"w{window_index}"
            if not window_log.already_marked(
                root_id, window_index, kind="closed", token=closed_token
            ):
                window_log.mark(root_id, window_index, kind="closed", token=closed_token)
                await events.emit_manage_charter_tick_closed(
                    root=root_id,
                    window_index=window_index,
                    worker_thread=worker_thread,
                    checkpoint_turn=turn_number(checkpoint),
                    worker_closed=worker_closed,
                )
            if provenance is not None:
                await events.emit_manage_charter_tick_consult_harvested(
                    root=root_id, window_index=window_index, **provenance
                )
        except Exception:  # noqa: BLE001 — transcript must not kill the tick
            logger.exception("charter-runner window_log append_closeout failed")
    return attributions
