"""Per-root charter window admission (fire dispatch + admission pointer)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client, dispatch_client, window_log
from .caps import CapStore
from .eligibility import ADMISSION_SUBJECT_PREFIX, Decision
from .executor_routing import resolve_charter_executor
from .materializer_autonomous import select_packet
from .r_corpus_sha import (
    clear_r_corpus_refusals,
    refuse_stale_r_admit,
    verify_r_corpus_sha,
)
from .window_terminal_contract import implement_ready_declared

logger = get_logger(__name__)


def _count_admissions(turns: list[dict]) -> int:
    prefix = ADMISSION_SUBJECT_PREFIX.upper()
    return sum(
        1 for t in turns if str(t.get("subject") or "").upper().startswith(prefix)
    )


async def admit_window(
    *,
    decision: Decision,
    turns: list[dict],
    caps: CapStore,
    workspace_root: Path,
    on_admit: Callable[[str], None] | None,
) -> bool:
    """Fire one charter window and post the admission pointer on the root."""
    root_id = decision.root_id
    try:
        await bus_client.ensure_root_so_what(root_id)
    except Exception:  # noqa: BLE001 — title fill must not block admit
        logger.debug(
            "charter-runner so-what ensure failed root=%s", root_id, exc_info=True
        )
    assert decision.parsed is not None and decision.checkpoint is not None
    from .attendance import admission_mode_for_root

    window_index = _count_admissions(turns) + 1
    admission_mode = admission_mode_for_root(root_id)
    consult_role: str | None = None
    if decision.window_kind == "consult":
        admission_mode = "consult"
        consult_role = decision.parsed.consult_role
        if (
            decision.parsed.executor_lane == "implement"
            and not implement_ready_declared(decision.parsed)
        ):
            logger.warning(
                "classifier_consult_overrides_implement_lane root=%s",
                root_id,
            )
    if consult_role == "r_admit":
        cp_body = str((decision.checkpoint or {}).get("body") or "")
        sha_check = verify_r_corpus_sha(cp_body)
        if not sha_check.ok:
            await refuse_stale_r_admit(
                root_id=root_id,
                checkpoint=decision.checkpoint,
                result=sha_check,
                events_module=events,
                log=logger,
            )
            return False
        clear_r_corpus_refusals(root_id)
    # Router sits *after* the consult branch: a CONSULT_PENDING pickup naming G4
    # must stay on the consult seat (R-independence), never re-route to implement.
    bind = resolve_charter_executor(
        parsed=decision.parsed,
        admission_mode=admission_mode,
        consult_role=consult_role,
    )
    logger.info(
        "charter-runner executor lane root=%s window=%s lane=%s reason=%s",
        root_id,
        window_index,
        bind.lane,
        bind.reason,
    )
    packet, subject = select_packet(
        root_id,
        decision.parsed,
        scoreboard_uri=decision.parsed.scoreboard_uri,
        window_index=window_index,
        admission_mode=admission_mode,
        consult_role=consult_role,
        source_ref=bind.source_ref,
    )
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
            implement_source_ref=bind.source_ref,
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body_snippet = (exc.response.text or "")[:500]
        if 400 <= status < 500:
            # Client reject: safe to clear intent and stop (no retry of same fire).
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
        # 5xx: keep intent (A-R3-4) + stop root. Clearing intent then re-raising
        # let the tick re-admit every interval with no WIP pointer (a:26168 thrash
        # on agent-bus:5777 — Stargate 503 while generate-admitted side effects).
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
        # Keep intent; stop root — ¬ clear+raise into tick continue (a:26168).
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
    push = str(result.get("push_reminder") or "")
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
    except Exception as exc:  # noqa: BLE001 — stop root; do not re-fire
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
            push_reminder=push,
            dispatch_id=str(result.get("dispatch_id") or ""),
        )
        window_log.append_executor_note(worker_thread, result.get("executor") or {})
    except Exception:  # noqa: BLE001 — transcript must not kill the tick
        logger.exception("charter-runner window_log append_admit failed")
    # Derived from the fired body, never restated: a hardcoded model name in the
    # notification silently disagrees with the wire the moment routing changes.
    executor = result.get("executor") or {}
    fired_model = str(executor.get("model") or executor.get("role") or "")
    if admission_mode == "handoff":
        mode_note = " (attended IDE — open worker thread)"
    elif admission_mode == "consult":
        if consult_role == "r_admit":
            mode_note = (
                " (CONSULT_PENDING — R-admit host → cdp/opus-5)"
            )
        else:
            mode_note = (
                " (CONSULT_PENDING — judgment_gap host → cdp/opus-5)"
            )
    elif admission_mode == "autonomous":
        lane = "implement" if bind.is_implement else "background lead"
        mode_note = f" (autonomous {lane} — {fired_model})"
    elif admission_mode == "operator_proxy":
        mode_note = " (operator-proxy host — polls CDP lane)"
    else:
        mode_note = f" ({fired_model})"
    msg = f"charter-runner: admitted {worker_thread} for root {root_id}" + mode_note
    if push:
        msg += f" — {push}"
    if on_admit is not None:
        try:
            on_admit(msg)
        except Exception:  # noqa: BLE001 — notify must not kill the tick
            logger.exception("charter-runner on_admit notify failed")
    return True
