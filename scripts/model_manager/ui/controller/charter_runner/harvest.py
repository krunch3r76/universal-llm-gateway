"""Harvest completed charter-runner windows into transcripts + closed events."""

from __future__ import annotations

import re

from pager_notify.tick import ClosedAttribution, task_hint_from_next_pickup
from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import (
    bus_client,
    gate_bypass_detect,
    window_log,
)
from .checkpoint_body import resolve_checkpoint_body
from .checkpoint_parse import parse_checkpoint
from .eligibility import ADMISSION_SUBJECT_PREFIX
from .harvest_footer_gate import reject_harvest_without_footer
from .window_terminal_contract import after_window_terminal_harvested, is_tip_class

logger = get_logger(__name__)


async def _maybe_harvest_cdp_consult_provenance(
    *,
    root_id: str,
    window_index: int,
    worker_thread: str,
    worker_turns: list[dict],
    admission_meta: dict,
) -> dict[str, str] | None:
    """B8 — parse ``cdp/opus-*`` harvest and write consult provenance."""
    mode = str(admission_meta.get("admission_mode") or "").strip().lower()
    if mode != "consult":
        return None
    from .consult_lane import (
        parse_cdp_consult_harvest,
        provenance_from_cdp_harvest,
        write_consult_provenance,
    )
    from .window_log import worker_transcript_path

    executor: dict = {}
    transcript = worker_transcript_path(worker_thread)
    if transcript.is_file():
        text = transcript.read_text(encoding="utf-8", errors="replace")
        if "reviewer_model=" in text or "model=cdp/" in text:
            for line in text.splitlines():
                if line.startswith("seat=") or line.startswith("model="):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        executor[parts[0].strip()] = parts[1].strip()
    executor.setdefault("reviewer_model", "cdp/opus-5")
    root_turns: list[dict] = []
    try:
        root_turns = await bus_client.fetch_turns(root_id)
    except Exception:  # noqa: BLE001 — root fetch must not abort B8
        logger.exception(
            "charter-runner cdp harvest root fetch failed root=%s", root_id
        )
    parsed = parse_cdp_consult_harvest(
        worker_turns,
        executor=executor,
        worker_thread=worker_thread,
        delivery_turns=root_turns,
        root_id=root_id,
    )
    if parsed is None or parsed.escape_path:
        return None
    record = provenance_from_cdp_harvest(parsed)
    if record is None:
        return None
    uri = write_consult_provenance(record, root_id=root_id)
    logger.info(
        "charter-runner cdp consult provenance root=%s window=%s model=%s verdict=%s",
        root_id,
        window_index,
        record.consultant_model,
        record.verdict,
    )
    return {
        "consult_thread": record.consult_thread,
        "verdict": record.verdict,
        "consultant_family": record.consultant_family,
        "consultant_substrate": record.consultant_substrate,
        "cortex_mirror": uri,
        "consultant_model": record.consultant_model,
    }


_GATED_ID_RE = re.compile(r"\b([GR]\d+[a-z]?)\b")


def _executor_slug_for_sms(
    admission_mode: str,
    *,
    executor_lane: str | None,
    consult_role: str | None,
) -> str:
    """Map admit mode + checkpoint lane to the SMS executor slug."""
    del consult_role  # both consult roles host cdp/opus-5 as reviewer
    mode = (admission_mode or "generate").strip().lower()
    if mode == "consult":
        # Both roles host CDP Opus as the cross-family reviewer (a:26476).
        return "cdp/opus-5"
    if mode == "handoff":
        return "cursor"
    if (executor_lane or "").strip().lower() == "implement":
        return "cursor/composer-2.5"
    return "cursor/grok-4.5"


_CONSULT_ROLE_SNIFF_RE = re.compile(
    r"consult_role:\s*(r_admit|judgment_gap)\b", re.IGNORECASE
)


def _consult_role_from_pickup(next_pickup: list[str]) -> str | None:
    """Sniff consult_role from Next-pickup when parse leaves it unset."""
    for item in next_pickup:
        m = _CONSULT_ROLE_SNIFF_RE.search(item)
        if m:
            return m.group(1).lower()
        if re.search(r"\bR-admit\b", item, re.IGNORECASE):
            return "r_admit"
    return None


def attribution_for_harvested_window(
    *,
    root_id: str,
    consumed_checkpoint_body: str,
    admission_mode: str,
    thread_slug: str = "",
    completing_subject: str = "",
    window_index: int = 0,
    so_what: str = "",
) -> ClosedAttribution | None:
    """Build harvest-close provenance from the CHECKPOINT the window consumed."""
    parsed = parse_checkpoint(consumed_checkpoint_body)
    gid: str | None = None
    for item in parsed.next_pickup:
        m = _GATED_ID_RE.search(item)
        if m:
            gid = m.group(1)
            break
    if not gid:
        return None
    consult_role = parsed.consult_role or _consult_role_from_pickup(parsed.next_pickup)
    executor_slug = _executor_slug_for_sms(
        admission_mode,
        executor_lane=parsed.executor_lane,
        consult_role=consult_role,
    )
    return ClosedAttribution(
        gid=gid,
        executor_slug=executor_slug,
        root_id=root_id,
        thread_slug=thread_slug,
        task_hint=task_hint_from_next_pickup(
            parsed.next_pickup,
            gid,
            source_ref=parsed.source_ref,
        ),
        source_ref=parsed.source_ref or "",
        checkpoint_subject=completing_subject,
        window_index=window_index,
        so_what=so_what,
    )


def _persist_residue_after_harvest(
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
            provenance = await _maybe_harvest_cdp_consult_provenance(
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
                _persist_residue_after_harvest(
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
