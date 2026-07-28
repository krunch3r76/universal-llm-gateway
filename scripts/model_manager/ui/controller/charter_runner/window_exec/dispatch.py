"""Materialize + dispatch admitted windows (spec §B row 6 — Phase 3 cutover)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from .. import bus_client, dispatch_client, window_log
from ..admission import ADMISSION_SUBJECT_PREFIX, CapStore
from ..checkpoint_schema import (
    ParsedCheckpoint,
    parse_checkpoint,
    resolve_checkpoint_body,
)
from ..executor_routing import resolve_charter_executor
from ..r_corpus_sha import (
    clear_r_corpus_refusals,
    refuse_stale_r_admit,
    verify_r_corpus_sha,
)
from ..root_ledger import RootLedgerRow
from ..window_terminal_contract import implement_ready_declared
from .materializer_autonomous import select_packet
from .materializer_consult import consult_subject, materialize_consult_packet

logger = get_logger(__name__)


def count_admissions(turns: list[dict]) -> int:
    prefix = ADMISSION_SUBJECT_PREFIX.upper()
    return sum(
        1 for t in turns if str(t.get("subject") or "").upper().startswith(prefix)
    )


def latest_checkpoint(turns: list[dict]) -> dict | None:
    """Newest CHECKPOINT turn (fetch_turns is newest-first)."""
    return max(
        (
            t
            for t in turns
            if str(t.get("subject") or "").upper().startswith("CHECKPOINT")
        ),
        key=lambda t: int(t.get("turn_number") or 0),
        default=None,
    )


def parse_tip_checkpoint(turns: list[dict]) -> tuple[dict, ParsedCheckpoint] | None:
    checkpoint = latest_checkpoint(turns)
    if checkpoint is None:
        return None
    body = resolve_checkpoint_body(
        str(checkpoint.get("body") or ""),
        sidecar_uri=(
            checkpoint.get("sidecar_uri")
            if isinstance(checkpoint.get("sidecar_uri"), str)
            else None
        ),
    )
    return checkpoint, parse_checkpoint(body)


async def admit_worker_window(
    *,
    root_id: str,
    turns: list[dict],
    caps: CapStore,
    workspace_root: Path,
    admission_mode: str,
    window_index: int | None = None,
    on_admit: Callable[[str], None] | None = None,
) -> bool:
    """Fire one mechanical/attended worker window from the tip CHECKPOINT.

    ``window_index`` comes from the kernel, which reconciles the bus pointers with
    the ledger and transcript; the bus-only fallback here restarts numbering
    whenever a turn fetch comes back short (a:26628) and exists only for callers
    with no ledger.
    """
    tip = parse_tip_checkpoint(turns)
    if tip is None:
        return False
    checkpoint, parsed = tip
    try:
        await bus_client.ensure_root_so_what(root_id)
    except Exception:  # noqa: BLE001
        logger.debug(
            "charter-runner so-what ensure failed root=%s", root_id, exc_info=True
        )
    if window_index is None:
        window_index = count_admissions(turns) + 1
    consult_role: str | None = None
    if parsed.consult_pending:
        admission_mode = "consult"
        consult_role = parsed.consult_role
        if parsed.executor_lane == "implement" and not implement_ready_declared(parsed):
            logger.warning(
                "classifier_consult_overrides_implement_lane root=%s",
                root_id,
            )
    if consult_role == "r_admit":
        sha_check = verify_r_corpus_sha(str(checkpoint.get("body") or ""))
        if not sha_check.ok:
            await refuse_stale_r_admit(
                root_id=root_id,
                checkpoint=checkpoint,
                result=sha_check,
                events_module=events,
                log=logger,
            )
            return False
        clear_r_corpus_refusals(root_id)
    bind = resolve_charter_executor(
        parsed=parsed,
        admission_mode=admission_mode,
        consult_role=consult_role,
    )
    packet, subject = select_packet(
        root_id,
        parsed,
        scoreboard_uri=parsed.scoreboard_uri,
        window_index=window_index,
        admission_mode=admission_mode,
        consult_role=consult_role,
        source_ref=bind.source_ref,
    )
    return await _fire_and_pointer(
        root_id=root_id,
        window_index=window_index,
        packet=packet,
        subject=subject,
        caps=caps,
        workspace_root=workspace_root,
        admission_mode=admission_mode,
        consult_role=consult_role,
        implement_source_ref=bind.source_ref,
        on_admit=on_admit,
        is_implement=bind.is_implement,
    )


async def admit_consult_window(
    *,
    row: RootLedgerRow,
    turns: list[dict],
    caps: CapStore,
    workspace_root: Path,
    consult_role: str,
    window_index: int | None = None,
    on_admit: Callable[[str], None] | None = None,
) -> bool:
    """Fire a depth-1 consult seat window for a ledger root."""
    tip = parse_tip_checkpoint(turns)
    if tip is None:
        return False
    _checkpoint, parsed = tip
    if window_index is None:
        window_index = count_admissions(turns) + 1
    packet = materialize_consult_packet(
        row.root_id,
        parsed,
        scoreboard_uri=row.scoreboard_uri,
        window_index=window_index,
    )
    subject = consult_subject(row.root_id, window_index, consult_role=consult_role)
    return await _fire_and_pointer(
        root_id=row.root_id,
        window_index=window_index,
        packet=packet,
        subject=subject,
        caps=caps,
        workspace_root=workspace_root,
        admission_mode="consult",
        consult_role=consult_role,
        implement_source_ref=None,
        on_admit=on_admit,
        is_implement=False,
    )


async def _fire_and_pointer(
    *,
    root_id: str,
    window_index: int,
    packet: str,
    subject: str,
    caps: CapStore,
    workspace_root: Path,
    admission_mode: str,
    consult_role: str | None,
    implement_source_ref: str | None,
    on_admit: Callable[[str], None] | None,
    is_implement: bool,
) -> bool:
    caps.mark_admit_intent(root_id, window_index)
    try:
        result = await dispatch_client.fire_window(
            root_id,
            packet,
            workspace_root=workspace_root,
            window_index=window_index,
            subject=subject,
            admission_mode=admission_mode,
            consult_role=consult_role,
            implement_source_ref=implement_source_ref,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body_snippet = (exc.response.text or "")[:500]
        if 400 <= status < 500:
            caps.clear_admit_intent(root_id, window_index)
            caps.mark_failed(root_id, "admission_rejected")
            await events.emit_manage_charter_tick_window_failed(
                root=root_id, reason="admission_rejected"
            )
            logger.error(
                "charter-runner admission rejected root=%s status=%s body=%s",
                root_id,
                status,
                body_snippet,
            )
            return False
        caps.mark_failed(root_id, "admission_transport_error")
        await events.emit_manage_charter_tick_window_failed(
            root=root_id, reason="admission_transport_error"
        )
        logger.error(
            "charter-runner admission transport error root=%s status=%s body=%s",
            root_id,
            status,
            body_snippet,
        )
        return False
    except Exception as exc:
        caps.mark_failed(root_id, "admission_exception")
        await events.emit_manage_charter_tick_window_failed(
            root=root_id, reason="admission_exception"
        )
        logger.exception(
            "charter-runner admission exception root=%s: %s",
            root_id,
            exc,
        )
        return False
    caps.record_admit(root_id)
    worker_thread = str(result.get("thread_id") or "")
    caps.bind_intent_worker(root_id, window_index, worker_thread)
    packet_path = str(result.get("packet_path") or "")
    now_iso = datetime.now(UTC).isoformat()
    try:
        await bus_client.post_admission_pointer(
            root_id,
            window_index=window_index,
            posted_at_iso=now_iso,
            worker_thread=worker_thread,
            packet_path=packet_path,
            admission_mode=admission_mode,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "charter-runner pointer post failed for root %s after fire: %s",
            root_id,
            exc,
        )
        caps.mark_failed(root_id, "pointer_post_failed")
        await events.emit_manage_charter_tick_window_failed(
            root=root_id, reason="pointer_post_failed"
        )
        return False
    await events.emit_manage_charter_tick_admitted(
        root=root_id,
        dispatch_id=str(result.get("dispatch_id") or worker_thread),
        worker_thread=worker_thread,
    )
    try:
        window_log.append_admit(
            root_id=root_id,
            window_index=window_index,
            worker_thread=worker_thread,
            packet_path=packet_path,
            packet_text=packet,
            push_reminder=str(result.get("push_reminder") or ""),
            dispatch_id=str(result.get("dispatch_id") or ""),
        )
        window_log.append_executor_note(worker_thread, result.get("executor") or {})
    except Exception:  # noqa: BLE001
        logger.exception("charter-runner window_log append_admit failed")
    executor = result.get("executor") or {}
    fired_model = str(executor.get("model") or executor.get("role") or "")
    if admission_mode == "consult":
        mode_note = (
            " (CONSULT_PENDING — R-admit host → cdp/opus-5)"
            if consult_role == "r_admit"
            else " (CONSULT_PENDING — judgment_gap host → cdp/opus-5)"
        )
    elif admission_mode == "autonomous":
        lane = "implement" if is_implement else "background lead"
        mode_note = f" (autonomous {lane} — {fired_model})"
    else:
        mode_note = f" ({fired_model})"
    msg = f"charter-runner: admitted {worker_thread} for root {root_id}" + mode_note
    if on_admit is not None:
        try:
            on_admit(msg)
        except Exception:  # noqa: BLE001
            logger.exception("charter-runner on_admit notify failed")
    return True


__all__ = [
    "admit_consult_window",
    "admit_worker_window",
    "count_admissions",
    "latest_checkpoint",
    "parse_tip_checkpoint",
]
