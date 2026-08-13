"""Resume plane for timed-out cursor-sdk dispatches (``resume_of`` lineage).

Eligibility checks, timeout-retain prune gates, worktree registry transfer, and
SDK ``resume_agent`` orchestration live here so ``routes/cursor_sdk.py`` stays a
thin call site.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from cursor_sdk import Client
from cursor_sdk.types import AgentOptions
from fastapi.responses import JSONResponse
from universal_protocol import error_envelope

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    LedgerRow,
)
from services.git_integration_worker.git_worker_lifecycle_events import (
    build_dispatch_error_envelope,
    emit_git_worker_dispatch_rejected,
    log_dispatch_rejection,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest
from universal_logging import get_logger

_DISPATCH_ROUTE = "/api/v1/cursor/dispatch"

logger = get_logger(__name__)

ResumeIneligibleReason = Literal[
    "parent_missing",
    "parent_not_failed",
    "state_root_missing",
    "sdk_agent_id_missing",
    "state_root_absent_on_disk",
    "dispatch_id_equals_parent",
    "nest_under_conflict",
]

_DEFAULT_HOME_RETENTION_DAYS = 14


def cursor_sdk_timeout_retain_s() -> int:
    """Return seconds to retain timeout-failed Lane-B worktrees before prune/reap.

    Honors ``CURSOR_SDK_TIMEOUT_RETAIN_S`` when set; otherwise co-expires with
    dispatch HOME retention via ``CURSOR_DISPATCH_HOME_RETENTION_DAYS``.
    """
    raw = os.environ.get("CURSOR_SDK_TIMEOUT_RETAIN_S")
    if raw is not None and raw.strip():
        return max(0, int(raw))
    days = int(os.environ.get("CURSOR_DISPATCH_HOME_RETENTION_DAYS", "14"))
    return days * 86400


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_row_columns(
    ledger: CursorDispatchLedger, *, dispatch_id: str, columns: str
) -> dict[str, Any] | None:
    with ledger._connect() as conn:
        row = conn.execute(
            f"SELECT {columns} FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def load_parent_row(
    ledger: CursorDispatchLedger, *, parent_id: str
) -> LedgerRow | None:
    """Load parent ledger projection for resume eligibility and child binding."""
    data = _load_row_columns(
        ledger,
        dispatch_id=parent_id,
        columns=(
            "dispatch_id, thread_id, execution_id, caller_agent, resolved_model, "
            "state_root, sdk_agent_id, sdk_run_id, status, started_at, "
            "last_heartbeat_at, source_repo, contract, read_only, record_json"
        ),
    )
    if data is None:
        return None
    return LedgerRow(**data)


def resume_eligibility_reason(
    ledger: CursorDispatchLedger, *, parent_id: str
) -> ResumeIneligibleReason | None:
    """Return ineligibility reason, or ``None`` when parent may be resumed."""
    row = load_parent_row(ledger, parent_id=parent_id)
    if row is None:
        return "parent_missing"
    if row.status != "failed":
        return "parent_not_failed"
    if not row.state_root:
        return "state_root_missing"
    if not row.sdk_agent_id:
        return "sdk_agent_id_missing"
    if not Path(row.state_root).is_dir():
        return "state_root_absent_on_disk"
    return None


def reject_resume_if_ineligible(
    req: CursorDispatchRequest,
) -> JSONResponse | None:
    """Return a 422 envelope when ``resume_of`` fails parent eligibility checks.

    Checks parent existence, terminal ``failed`` status, persisted ``state_root``
    and ``sdk_agent_id``, on-disk state directory, and mutual exclusion with
    ``nest_under``. Returns ``None`` when admission may proceed.
    """
    if req.resume_of is None:
        return None
    if req.nest_under is not None:
        return _resume_ineligible_response(
            req,
            reason="nest_under_conflict",
            detail="resume_of and nest_under are mutually exclusive",
        )
    if req.resume_of == req.dispatch_id:
        return _resume_ineligible_response(
            req,
            reason="dispatch_id_equals_parent",
            detail="resume_of must not equal dispatch_id",
        )
    reason = resume_eligibility_reason(
        CursorDispatchLedger.instance(), parent_id=req.resume_of
    )
    if reason is None:
        return None
    return _resume_ineligible_response(req, reason=reason)


def _resume_ineligible_response(
    req: CursorDispatchRequest,
    *,
    reason: ResumeIneligibleReason,
    detail: str | None = None,
) -> JSONResponse:
    summary = detail or f"resume_of ineligible: {reason}"
    envelope = build_dispatch_error_envelope(
        execution_id=req.execution_id,
        thread_id=req.thread_id,
        dispatch_id=req.dispatch_id,
        failure_layer="validation",
        http_status=422,
        worker_error_code="CURSOR_RESUME_INELIGIBLE",
        route=_DISPATCH_ROUTE,
        method="POST",
        detail_summary=summary,
        invalid_fields=["resume_of"],
        retryable=False,
        validation_stage="resume_eligibility",
    )
    emit_git_worker_dispatch_rejected(envelope)
    log_dispatch_rejection(envelope)
    return JSONResponse(
        status_code=422,
        content=error_envelope(
            code="CURSOR_RESUME_INELIGIBLE",
            message=summary,
            source="gateway",
            retryable=False,
            data={"resume_of": req.resume_of, "reason": reason},
        ),
    )


def persist_timeout_retain(*, dispatch_id: str) -> None:
    """Mark timeout-terminal parent rows so prune/reap skip until TTL expires."""
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id,
        patch={"timeout_retain": True},
    )


def timeout_retain_active(*, dispatch_id: str) -> bool:
    """True while a timeout-failed dispatch is inside the retain TTL window."""
    ledger = CursorDispatchLedger.instance()
    data = _load_row_columns(
        ledger,
        dispatch_id=dispatch_id,
        columns="record_json, terminal_at, terminal_status",
    )
    if data is None:
        return False
    try:
        record = json.loads(data["record_json"] or "{}")
    except json.JSONDecodeError:
        record = {}
    if not isinstance(record, dict) or not record.get("timeout_retain"):
        return False
    if data["terminal_status"] != "failed":
        return False
    terminal_at = _parse_iso(data["terminal_at"])
    if terminal_at is None:
        return False
    retain_s = cursor_sdk_timeout_retain_s()
    if retain_s <= 0:
        return False
    now = datetime.now(UTC)
    if terminal_at.tzinfo is None:
        terminal_at = terminal_at.replace(tzinfo=UTC)
    elapsed = (now - terminal_at).total_seconds()
    return elapsed < retain_s


@dataclass(frozen=True, slots=True)
class ResumeRunContext:
    """Parent identity substrate for a child ``resume_of`` dispatch."""

    resume_of: str
    state_root: str
    sdk_agent_id: str


def load_resume_run_context(*, dispatch_id: str) -> ResumeRunContext | None:
    """Return parent resume context when ``dispatch_id`` is a resume child row."""
    ledger = CursorDispatchLedger.instance()
    child = _load_row_columns(
        ledger,
        dispatch_id=dispatch_id,
        columns="resume_of",
    )
    if child is None or not child["resume_of"]:
        return None
    parent_id = str(child["resume_of"])
    parent = load_parent_row(ledger, parent_id=parent_id)
    if parent is None or not parent.state_root or not parent.sdk_agent_id:
        return None
    return ResumeRunContext(
        resume_of=parent_id,
        state_root=parent.state_root,
        sdk_agent_id=parent.sdk_agent_id,
    )


def sdk_agent_id_from_agent(agent: Any) -> str | None:
    """Capture SDK agent identity — ``agent_id`` is the stable surface on 1.0.26."""
    value = getattr(agent, "agent_id", None)
    if value:
        return str(value)
    legacy = getattr(agent, "id", None)
    return str(legacy) if legacy else None


def start_or_resume_agent(
    *,
    client: Client,
    agent_options: AgentOptions,
    prompt: str,
    resume_ctx: ResumeRunContext | None,
) -> tuple[Any, Any]:
    """Create or resume an SDK agent, then send the continuation turn."""
    if resume_ctx is not None:
        agent = client.resume_agent(resume_ctx.sdk_agent_id, agent_options)
    else:
        agent = client.create_agent(agent_options)
    run = agent.send(prompt)
    return agent, run
