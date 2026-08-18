"""Fire nested cursor-sdk dispatches and poll for terminal + bus closeout."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from services.git_integration_worker.admission import (
    Draining503,
    WorkAdmissionController,
)
from services.git_integration_worker.cursor_auto.closeout_outbox import get_outbox_store
from services.git_integration_worker.cursor_auto.closeout_outbox_events import (
    emit_closeout_persisted,
)
from services.git_integration_worker.cursor_auto.closeout_relay import (
    ledger_status_to_closeout,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    resolve_relay_status,
    strip_projected_closeout_envelope,
)
from services.git_integration_worker.cursor_auto.episode_residue import (
    compose_closeout_body,
    resolve_relay_residue,
)
from services.git_integration_worker.cursor_auto.job_ledger import (
    RELAY_PHASE_CLOSEOUT_POSTED,
    RELAY_PHASE_DISPATCHED,
    RELAY_PHASE_NONE,
    get_ledger,
)
from services.git_integration_worker.cursor_auto.lane_a_checkpoint import (
    extract_checkpoint_claim,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)

logger = get_logger(__name__)

_TERMINAL = frozenset({"completed", "failed"})
_WAKE_FORBIDDEN_TOKENS = frozenset(
    {"status:done", "status:failed", "status:needs-attended"}
)
_WAKE_SUBJECT_PREFIX = "WAKE — closeout relayed · "
_MAX_WAKE_SUBJECT_LEN = 80
_DEFAULT_DISPATCH_URL = "http://127.0.0.1:8091"
_POLL_INTERVAL_S = 2.0
_DEFAULT_TIMEOUT_S = 3600.0
_HEARTBEAT_FRESH_S = 60.0
_DEFAULT_MAX_POLL_REENTRIES = 10
_TREE_RESIDUE_RE = re.compile(r"(?im)^tree_residue:\s*(\d+)\b")


@dataclass(frozen=True, slots=True)
class CloseoutRelayContext:
    """Write-ahead outbox + admission context for one nested dispatch episode."""

    worker_id: str
    worker_started_at: str
    admission_controller: WorkAdmissionController | None = None
    skip_outbox: bool = False


def _relay_pause_s() -> float:
    raw = os.environ.get("CURSOR_AUTO_RELAY_PAUSE_S", "").strip()
    if not raw:
        return 0.0
    return max(0.0, float(raw))


def _dispatch_url() -> str:
    base = (
        os.environ.get("GIT_INTEGRATION_WORKER_URL", "").strip()
        or _DEFAULT_DISPATCH_URL
    )
    return f"{base.rstrip('/')}/api/v1/cursor/dispatch"


def _dispatch_timeout_s() -> float:
    raw = os.environ.get("CURSOR_AUTO_DISPATCH_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_S
    return max(30.0, float(raw))


def _heartbeat_fresh_s() -> float:
    raw = os.environ.get("CURSOR_AUTO_DISPATCH_HEARTBEAT_FRESH_S", "").strip()
    if not raw:
        return _HEARTBEAT_FRESH_S
    return max(10.0, float(raw))


def _max_poll_reentries() -> int:
    raw = os.environ.get("CURSOR_AUTO_DISPATCH_POLL_REENTRIES", "").strip()
    if not raw:
        return _DEFAULT_MAX_POLL_REENTRIES
    return max(1, int(raw))


def dispatch_row_liveness_fresh(
    row: dict[str, Any] | None,
    *,
    fresh_s: float | None = None,
    now: datetime | None = None,
) -> bool:
    """Return True when the nested dispatch row shows recent liveness.

    ``last_heartbeat_at`` is preferred; when absent (dispatch not heartbeating
    yet) ``started_at`` is used so a newly admitted row is not treated as dead.
    """
    if row is None:
        return False
    threshold = fresh_s if fresh_s is not None else _heartbeat_fresh_s()
    clock = now or datetime.now(UTC)
    cutoff = clock.timestamp() - threshold
    ts = row.get("last_heartbeat_at") or row.get("started_at")
    if ts is None:
        return False
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp() >= cutoff
    except ValueError:
        return False


def _release_failed_submission(
    job_id: str,
    relay_ctx: CloseoutRelayContext | None,
    *,
    dispatch_id: str,
    bind_job: bool = True,
) -> None:
    """Undo reservations taken before a nested submission that never ran.

    ``bind_dispatch`` and ``try_admit`` are claimed ahead of the POST when
    *bind_job* is true, so a submission the worker never accepted has to retire
    them here — nothing downstream will. Leaving the ticket wedges
    ``active_count`` against drain; leaving the binding at ``dispatched`` makes
    closeout replay wait on a dispatch that does not exist.

    *bind_job* false (reflex / advisory legs) never wrote the job row, so
    resetting ``relay_phase`` here would wipe the executor's binding. The
    failed-submit ``dispatch_id`` is left on the row when we *did* bind:
    last-write-wins ``bind_dispatch`` is what lets a later executor retry
    replace it. A first-wins COALESCE would pin the job to this dead id.
    """
    if bind_job:
        get_ledger().set_relay_phase(job_id, relay_phase=RELAY_PHASE_NONE)
    if relay_ctx is None or relay_ctx.admission_controller is None:
        return
    relay_ctx.admission_controller.close_ticket(dispatch_id, terminal_status="failed")


async def submit_nested_dispatch(
    job: AutoJob,
    *,
    model_id: str,
    handoff_contract: str,
    message: str,
    nest_under: str | None = None,
    model_knobs: dict[str, str] | None = None,
    read_only: bool | None = None,
    relay_ctx: CloseoutRelayContext | None = None,
    bind_job: bool = True,
) -> dict[str, Any]:
    """POST ``/api/v1/cursor/dispatch`` for one nested SDK run.

    *read_only* must be passed explicitly for lease-exempt legs: the route infers
    ``read_only=False`` for ``light-bounded``, so an advisory reader that never
    writes would otherwise contend for the write lease like an implement run.

    *bind_job* is the executor identity write. Reflex / second-read legs pass
    false so they cannot last-write-win the job's ``dispatch_id``. Executor
    retries keep the default true: a failed submit leaves ``dispatch_id`` on
    the row and the next successful bind overwrites it (not first-wins).
    """
    dispatch_id = f"auto-{uuid.uuid4().hex[:12]}"
    execution_id = f"exec-{dispatch_id}"
    if bind_job:
        get_ledger().bind_dispatch(
            job.job_id,
            dispatch_id=dispatch_id,
            relay_phase=RELAY_PHASE_DISPATCHED,
        )
    if relay_ctx is not None and relay_ctx.admission_controller is not None:
        try:
            relay_ctx.admission_controller.try_admit(
                "cursor-auto",
                op_id=dispatch_id,
                route="cursor-auto/nested",
            )
        except Draining503 as exc:
            _release_failed_submission(
                job.job_id, None, dispatch_id=dispatch_id, bind_job=bind_job
            )
            return {
                "ok": False,
                "dispatch_id": dispatch_id,
                "execution_id": execution_id,
                "error": str(exc),
                "reason": "worker_draining",
            }
    payload: dict[str, Any] = {
        "thread_id": job.thread_id,
        "model": model_id,
        "dispatch_id": dispatch_id,
        "execution_id": execution_id,
        "message": message,
        "handoff_contract": handoff_contract,
        "admitted_via": "cursor-auto",
        "close_contract": "auto",
    }
    if job.from_agent != "cursor-auto":
        payload["caller_agent"] = job.from_agent
    if job.request_id:
        payload["request_id"] = job.request_id
    # AC2 corner: omit caller_agent when from_agent is cursor-auto (no self-stamp).
    if nest_under:
        payload["nest_under"] = nest_under
    if model_knobs:
        payload["model_knobs"] = model_knobs
    if read_only is not None:
        payload["read_only"] = read_only
    if job.lane:
        payload["lane"] = job.lane
    url = _dispatch_url()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            _release_failed_submission(
                job.job_id, relay_ctx, dispatch_id=dispatch_id, bind_job=bind_job
            )
            return {
                "ok": False,
                "dispatch_id": dispatch_id,
                "execution_id": execution_id,
                "status_code": resp.status_code,
                "error": data,
            }
        return {
            "ok": True,
            "dispatch_id": dispatch_id,
            "execution_id": execution_id,
            "admitted": bool(data.get("admitted", True)),
            "response": data,
        }
    except (httpx.HTTPError, ValueError, OSError) as exc:
        _release_failed_submission(
            job.job_id, relay_ctx, dispatch_id=dispatch_id, bind_job=bind_job
        )
        logger.error("cursor-auto nested dispatch submit failed: %s", exc)
        return {
            "ok": False,
            "dispatch_id": dispatch_id,
            "execution_id": execution_id,
            "error": str(exc),
        }


async def poll_dispatch_terminal(
    *,
    thread_id: str,
    dispatch_id: str,
    timeout_s: float | None = None,
    superseded: Callable[[], bool] | None = None,
    on_tick: Callable[[dict[str, Any] | None], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Poll ledger until nested dispatch reaches a terminal status.

    *superseded* is checked each tick so a job displaced by a newer same-thread
    request abandons the poll immediately instead of burning the remaining
    dispatch budget on an episode the operator already backtracked.
    """
    ledger = CursorDispatchLedger.instance()
    budget = timeout_s if timeout_s is not None else _dispatch_timeout_s()
    deadline = time.monotonic() + budget
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        if superseded is not None and superseded():
            return {
                "ok": False,
                "terminal": False,
                "superseded": True,
                "dispatch_id": dispatch_id,
            }
        row = await asyncio.to_thread(
            ledger.dispatch_status_by_thread, thread_id=thread_id
        )
        if row is not None:
            last = row
            if on_tick is not None:
                await on_tick(row)
            if row.get("dispatch_id") == dispatch_id and row.get("status") in _TERMINAL:
                return {
                    "ok": True,
                    "terminal": True,
                    "status": row["status"],
                    "row": row,
                }
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {
        "ok": False,
        "terminal": False,
        "reason": "dispatch_poll_timeout",
        "last": last,
        "dispatch_id": dispatch_id,
    }


async def poll_dispatch_terminal_with_liveness(
    *,
    thread_id: str,
    dispatch_id: str,
    timeout_s: float | None = None,
    superseded: Callable[[], bool] | None = None,
    on_tick: Callable[[dict[str, Any] | None], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Poll until terminal, extending budget while nested dispatch liveness is fresh."""
    budget = timeout_s if timeout_s is not None else _dispatch_timeout_s()
    total_ceiling_s = budget * (_max_poll_reentries() + 1)
    poll_started = time.monotonic()
    reentries = 0
    polled: dict[str, Any] = {}
    while True:
        polled = await poll_dispatch_terminal(
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            timeout_s=budget,
            superseded=superseded,
            on_tick=on_tick,
        )
        if polled.get("terminal") or polled.get("superseded"):
            return polled
        if not dispatch_row_liveness_fresh(polled.get("last")):
            return polled
        if reentries >= _max_poll_reentries():
            logger.warning(
                "cursor-auto nested poll re-entry count ceiling dispatch_id=%s "
                "reentries=%s",
                dispatch_id,
                reentries,
            )
            return polled
        if time.monotonic() - poll_started >= total_ceiling_s:
            logger.warning(
                "cursor-auto nested poll total wall-clock ceiling dispatch_id=%s "
                "elapsed_s=%.1f ceiling_s=%.1f",
                dispatch_id,
                time.monotonic() - poll_started,
                total_ceiling_s,
            )
            return polled
        reentries += 1
        last = polled.get("last") or {}
        logger.info(
            "cursor-auto nested poll budget exhausted with fresh heartbeat; "
            "re-entering dispatch_id=%s reentry=%s last_heartbeat_at=%s",
            dispatch_id,
            reentries,
            last.get("last_heartbeat_at"),
        )


async def fetch_sdk_closeout_body(
    *,
    thread_id: str,
    dispatch_id: str,
    bus: CursorBusClient | None = None,
) -> str | None:
    """Return latest cursor-sdk bus turn body mentioning ``dispatch_id``."""
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=15.0) as client:
            resp = await client.get(
                "/turns",
                params={"thread": thread_id, "last": 8},
                headers=headers,
            )
        if resp.status_code >= 400:
            return None
        turns = (resp.json() or {}).get("turns") or []
    except (httpx.HTTPError, ValueError, OSError):
        return None
    needle = dispatch_id[:8]
    for turn in reversed(turns):
        if turn.get("from") != "cursor-sdk":
            continue
        subject = str(turn.get("subject") or "")
        body = str(turn.get("body") or "")
        if dispatch_id in subject or needle in subject or needle in body:
            return body or None
    return None


def closeout_status_from_terminal(terminal_status: str) -> str:
    """Map ledger terminal status → operator CLOSEOUT status line."""
    return ledger_status_to_closeout(terminal_status)


async def post_operator_confer(
    job: AutoJob,
    *,
    dispatch_id: str,
    model_id: str,
    status: str,
    closeout_body: str | None,
    bus: CursorBusClient | None = None,
) -> dict[str, Any]:
    """Post confer §2 body to operator under ``TYPE: CONFER`` with status line."""
    client = bus or CursorBusClient()
    payload = (closeout_body or "").strip() or "(no confer closeout captured)"
    lines = [
        "TYPE: CONFER",
        f"status: {status}",
        f"dispatch_id: {dispatch_id}",
        f"model: {model_id}",
        "model_plane: admit-resolved",
        f"request_turn: {job.turn_number}",
        "",
        payload,
    ]
    resp = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
        from_agent="cursor-auto",
        subject=f"status:done — {job.subject[:60]}",
        body="\n".join(lines),
        allow_long_body=True,
    )
    return {
        "ok": resp.status_code < 400,
        "status_code": resp.status_code,
        "body": resp.body,
    }


async def post_operator_closeout(
    job: AutoJob,
    *,
    status: str,
    dispatch_id: str,
    model_id: str,
    sdk_body: str | None,
    extra: dict[str, Any] | None = None,
    bus: CursorBusClient | None = None,
    closeout_body: str | None = None,
    closeout_source: str | None = None,
    relay_note: str | None = None,
    deployment_state: str | None = None,
    plane_line: str | None = None,
    relay_ctx: CloseoutRelayContext | None = None,
    skip_outbox_persist: bool = False,
    replay_mode: bool = False,
) -> dict[str, Any]:
    """Post ``TYPE: CLOSEOUT`` to the operator seat (``job.from_agent``).

    Prefer *closeout_body* when the caller already selected a §2 payload.
    Legacy path: fall back to *sdk_body* (wrapper) when selection was not run.
    ``plane:`` / ``plane-discrepancy:`` lines already in *closeout_body* are
    preserved verbatim (transport never upgrades or collapses plane states).
    """
    from services.git_integration_worker.cursor_auto.auth_gate_budget import (
        tag_gate_class_for_payload,
    )
    from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
        preserve_plane_lines,
    )
    from services.git_integration_worker.cursor_auto.status_token_register import (
        prose_closeout_register_header_lines,
        stamp_meta_terminal_status_status_of,
    )

    client = bus or CursorBusClient()
    meta = dict(extra or {})
    if closeout_source:
        meta["closeout_source"] = closeout_source
    payload = (closeout_body if closeout_body is not None else sdk_body) or ""
    if not payload.strip():
        payload = "(no cursor-sdk closeout body captured)"
    envelope_status = resolve_relay_status(payload, status)
    relay_body = strip_projected_closeout_envelope(payload.strip())
    gate_class = tag_gate_class_for_payload(payload)
    if gate_class:
        meta["gate_class"] = gate_class
    if getattr(job, "contract", None):
        meta.setdefault("contract", job.contract)
    envelope_execution_id = str(meta.get("execution_id") or f"exec-{dispatch_id}")
    meta.setdefault("execution_id", envelope_execution_id)
    meta = stamp_meta_terminal_status_status_of(meta)
    from services.git_integration_worker.cursor_auto.closeout_status_polarity import (
        measurement_incomplete_class,
    )

    lines = [
        "TYPE: CLOSEOUT",
        f"status: {envelope_status}",
    ]
    incomplete_class = measurement_incomplete_class(envelope_status)
    if incomplete_class is not None:
        lines.append(f"status_incomplete_class: {incomplete_class}")
    from services.git_integration_worker.cursor_auto.composed_commission import (
        compute_composed_commission,
        prose_composed_commission_line,
        resolve_composition_parent_id,
    )

    nest_under = (extra or {}).get("nest_under")
    parent_id = resolve_composition_parent_id(
        closing_dispatch_id=dispatch_id,
        nest_under=nest_under,
    )
    composed_value = compute_composed_commission(
        parent_dispatch_id=parent_id,
        ledger=CursorDispatchLedger.instance(),
    )
    lines.append(prose_composed_commission_line(composed_value))
    lines.extend(prose_closeout_register_header_lines())
    if relay_note:
        lines.append(f"relay_note: {relay_note}")
    if deployment_state:
        lines.append(f"deployment_state: {deployment_state}")
    # Envelope plane line only when body lacks one (body injection is primary).
    if plane_line and not preserve_plane_lines(relay_body):
        lines.append(
            plane_line if plane_line.startswith("plane:") else f"plane: {plane_line}"
        )
    lines.extend(
        [
            f"dispatch_id: {dispatch_id}",
            f"execution_id: {envelope_execution_id}",
            f"model: {model_id}",
        ]
    )
    if job.request_id:
        lines.append(f"request_id: {job.request_id}")
    lines.extend(
        [
            "model_plane: admit-resolved",
            f"request_turn: {job.turn_number}",
        ]
    )
    if meta:
        lines.append(f"meta: {json.dumps(meta, sort_keys=True)}")
    lines.append("")
    lines.append(relay_body)
    body = compose_closeout_body(
        "\n".join(lines),
        resolve_relay_residue(wrapper_body=sdk_body, relay_body=payload),
    )
    from claude_bundles.lane_a_closeout_checkpoint import (
        validate_lane_a_closeout_checkpoint,
    )

    envelope_verdict = validate_lane_a_closeout_checkpoint(body=body)
    if not envelope_verdict.ok:
        return {
            "ok": False,
            "status_code": 422,
            "body": envelope_verdict.reason or "lane_a_checkpoint_missing",
            "closeout_source": closeout_source,
            "reason": envelope_verdict.reason,
        }

    ctx = relay_ctx
    outbox_skip = skip_outbox_persist or (ctx.skip_outbox if ctx else False)
    if not outbox_skip and ctx is not None and not replay_mode:
        checkpoint_value = extract_checkpoint_claim(body)
        tree_match = _TREE_RESIDUE_RE.search(body)
        tree_residue = int(tree_match.group(1)) if tree_match else None
        subject = f"status:done — {job.subject[:60]}"
        row = get_outbox_store().persist_pending(
            dispatch_id=dispatch_id,
            job_id=job.job_id,
            thread_id=job.thread_id,
            to_agent=job.from_agent,
            from_agent="cursor-auto",
            subject=subject,
            envelope_body=body,
            closeout_status=envelope_status,
            request_turn=job.turn_number,
            worker_id=ctx.worker_id,
            worker_started_at=ctx.worker_started_at,
            closeout_source=closeout_source,
            request_id=job.request_id,
            checkpoint_value=checkpoint_value,
            tree_residue=tree_residue,
        )
        emit_closeout_persisted(
            dispatch_id=dispatch_id,
            job_id=job.job_id,
            thread_id=job.thread_id,
            envelope_sha256=row.envelope_sha256,
            closeout_status=envelope_status,
        )

    pause_s = _relay_pause_s()
    if pause_s > 0 and not replay_mode:
        await asyncio.sleep(pause_s)

    resp = await client.reply(
        thread_id=job.thread_id,
        to_agent=job.from_agent,
        from_agent="cursor-auto",
        subject=f"status:done — {job.subject[:60]}",
        body=body,
        allow_long_body=True,
    )
    ok = resp.status_code < 400
    if ok and not outbox_skip and ctx is not None and not replay_mode:
        get_outbox_store().mark_posted(dispatch_id)
        get_ledger().set_relay_phase(
            job.job_id,
            relay_phase=RELAY_PHASE_CLOSEOUT_POSTED,
        )
    if ok and envelope_status == "partial:work" and not replay_mode:
        from services.git_integration_worker.cursor_auto.partial_work_production_specimen_events import (
            emit_partial_work_production_specimen,
        )

        emit_partial_work_production_specimen(
            dispatch_id=dispatch_id,
            envelope_turn=job.turn_number,
            thread_id=job.thread_id,
            closeout_source=closeout_source,
            contract=getattr(job, "contract", None),
            replay_mode=replay_mode,
        )
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "body": resp.body,
        "closeout_source": closeout_source,
    }


def _normalize_closeout_status(closeout_status: str) -> str:
    """Strip a leading ``status:`` prefix from ingress closeout status."""
    text = closeout_status.strip()
    if text.lower().startswith("status:"):
        return text.split(":", 1)[1].strip()
    return text


def _wake_subject(dispatch_id: str) -> str:
    """Build a deterministic WAKE subject capped at 80 characters."""
    prefix = _WAKE_SUBJECT_PREFIX
    full = f"{prefix}{dispatch_id}"
    if len(full) <= _MAX_WAKE_SUBJECT_LEN:
        return full
    max_id = _MAX_WAKE_SUBJECT_LEN - len(prefix) - 1
    truncated_id = f"{dispatch_id[:max_id]}…"
    return f"{prefix}{truncated_id}"


def _contains_wake_forbidden_tokens(text: str) -> bool:
    return any(token in text for token in _WAKE_FORBIDDEN_TOKENS)


async def post_operator_wake(
    job: AutoJob,
    *,
    dispatch_id: str,
    request_turn: str,
    closeout_status: str,
    bus: CursorBusClient | None = None,
) -> dict[str, Any]:
    """Post ``TYPE: WAKE`` after successful CLOSEOUT relay for absent waiters.

    WAKE subject/body must not carry ``status:done`` / ``status:failed`` /
    ``status:needs-attended`` tokens so ``completion=status:done`` waiters
    complete on CLOSEOUT only, not on the wake ping.
    """
    normalized_status = _normalize_closeout_status(closeout_status)
    subject = _wake_subject(dispatch_id)
    body_lines = [
        "TYPE: WAKE",
        f"dispatch_id: {dispatch_id}",
        f"request_turn: {request_turn}",
        f"closeout_status: {normalized_status}",
        f"thread: {job.thread_id}",
        "",
        (
            "Unread ping for an absent operator waiter after CLOSEOUT relay; "
            "not a new DIRECTIVE. Healthy waiters already completed on CLOSEOUT."
        ),
    ]
    body = "\n".join(body_lines)
    if _contains_wake_forbidden_tokens(subject) or _contains_wake_forbidden_tokens(
        body
    ):
        logger.warning(
            "cursor-auto wake token guard blocked post dispatch_id=%s thread_id=%s",
            dispatch_id,
            job.thread_id,
        )
        return {"ok": False, "reason": "wake_token_guard"}

    client = bus or CursorBusClient()
    try:
        resp = await client.reply(
            thread_id=job.thread_id,
            to_agent=job.from_agent,
            from_agent="cursor-auto",
            subject=subject,
            body=body,
            allow_long_body=False,
        )
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning(
            "cursor-auto wake post failed dispatch_id=%s thread_id=%s to_agent=%s "
            "status_code=%s error=%s",
            dispatch_id,
            job.thread_id,
            job.from_agent,
            None,
            exc,
        )
        return {"ok": False, "reason": str(exc)}

    ok = resp.status_code < 400
    if not ok:
        logger.warning(
            "cursor-auto wake post failed dispatch_id=%s thread_id=%s to_agent=%s "
            "status_code=%s",
            dispatch_id,
            job.thread_id,
            job.from_agent,
            resp.status_code,
        )
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "body": resp.body,
    }
