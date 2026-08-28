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

from claude_bundles.conductor_stop import parse_stop_tokens
from cursor_sdk import Client
from cursor_sdk.types import AgentOptions
from fastapi.responses import JSONResponse
from universal_logging import get_logger
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

_DISPATCH_ROUTE = "/api/v1/cursor/dispatch"

logger = get_logger(__name__)

ResumeIneligibleReason = Literal[
    "parent_missing",
    "parent_still_live",
    "state_root_missing",
    "sdk_agent_id_missing",
    "state_root_absent_on_disk",
    "dispatch_id_equals_parent",
    "nest_under_conflict",
]

_LIVE_STATUSES = frozenset({"queued", "admitted", "running", "parked_waiting"})

_DESIGNED_STOP_RETAIN_TOKENS = frozenset(
    {
        "ROW_PINNED",
        "HOLD_MERGE",
        "OPERATOR_GATE",
        "PARKED_TRANSPORT",
    }
)

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
            "last_heartbeat_at, source_repo, contract, read_only, record_json, "
            "terminal_status"
        ),
    )
    if data is None:
        return None
    return LedgerRow(**data)


def _find_sdk_store_under_home(home: Path) -> Path | None:
    """Return the sdk-agent-store directory under a dispatch HOME, if present."""
    projects = home / ".cursor" / "projects"
    if not projects.is_dir():
        return None
    for candidate in projects.rglob("sdk-agent-store"):
        if candidate.is_dir():
            return candidate
    return None


def resolve_sdk_store_dir(
    *,
    parent_id: str,
    state_root: str | None,
) -> Path | None:
    """Locate the on-disk SDK sqlite store for a resume parent.

    Prefers a non-empty ``state_root`` directory; falls back to the store under
    the parent dispatch HOME (store-A — HOME-bound per 9675 observation).
    """
    if state_root:
        root_path = Path(state_root)
        if root_path.is_dir():
            if any(root_path.iterdir()):
                return root_path
            store_in_root = root_path / "sdk-agent-store"
            if store_in_root.is_dir():
                return store_in_root
    from services.git_integration_worker.cursor_home import dispatch_home_path

    parent_home = dispatch_home_path(parent_id)
    return _find_sdk_store_under_home(parent_home)


def resume_eligibility_reason(
    ledger: CursorDispatchLedger, *, parent_id: str
) -> ResumeIneligibleReason | None:
    """Return ineligibility reason, or ``None`` when parent may be resumed."""
    row = load_parent_row(ledger, parent_id=parent_id)
    if row is None:
        return "parent_missing"
    if row.status in _LIVE_STATUSES:
        return "parent_still_live"
    if not row.sdk_agent_id:
        return "sdk_agent_id_missing"
    store_dir = resolve_sdk_store_dir(
        parent_id=parent_id,
        state_root=row.state_root,
    )
    if store_dir is None:
        if not row.state_root:
            return "state_root_missing"
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
        patch={"timeout_retain": True, "resume_retain": True},
    )


def persist_resume_retain(*, dispatch_id: str) -> None:
    """Mark designed-stop / conductor terminals for worktree+HOME retention."""
    CursorDispatchLedger.instance().merge_record_json(
        dispatch_id=dispatch_id,
        patch={"resume_retain": True},
    )


def _retain_ttl_elapsed(*, terminal_at: datetime | None, retain_s: int) -> bool:
    if terminal_at is None or retain_s <= 0:
        return True
    now = datetime.now(UTC)
    if terminal_at.tzinfo is None:
        terminal_at = terminal_at.replace(tzinfo=UTC)
    return (now - terminal_at).total_seconds() >= retain_s


def resume_retain_active(*, dispatch_id: str) -> bool:
    """True while a resume-retained dispatch is inside the retain TTL window."""
    ledger = CursorDispatchLedger.instance()
    data = _load_row_columns(
        ledger,
        dispatch_id=dispatch_id,
        columns="record_json, terminal_at, terminal_status, sdk_agent_id, status",
    )
    if data is None:
        return False
    if data["status"] in _LIVE_STATUSES:
        return False
    if not data.get("sdk_agent_id"):
        return False
    try:
        record = json.loads(data["record_json"] or "{}")
    except json.JSONDecodeError:
        record = {}
    if not isinstance(record, dict) or not record.get("resume_retain"):
        return False
    terminal_at = _parse_iso(data["terminal_at"])
    retain_s = cursor_sdk_timeout_retain_s()
    return not _retain_ttl_elapsed(terminal_at=terminal_at, retain_s=retain_s)


def dispatch_retain_active(*, dispatch_id: str) -> bool:
    """True when timeout or designed-stop retain blocks worktree prune."""
    return timeout_retain_active(dispatch_id=dispatch_id) or resume_retain_active(
        dispatch_id=dispatch_id
    )


def closeout_qualifies_for_resume_retain(
    *,
    closeout_body: str,
    packet_kind: str | None = None,
) -> bool:
    """Return True when a terminal closeout should retain store + worktree."""
    if packet_kind == "conductor":
        return True
    parsed = parse_stop_tokens(closeout_body or "")
    return bool(parsed.tokens & _DESIGNED_STOP_RETAIN_TOKENS)


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


def record_resolved_store_roots(
    *,
    parent_id: str,
    child_id: str,
    parent_state_root: str | None = None,
) -> str | None:
    """Persist the real SDK store path on parent and child ledger rows.

    Replaces a lying ``state_root`` (empty ``bridge-state``) with the path
    ``resolve_sdk_store_dir`` finds — typically the HOME-bound
    ``sdk-agent-store`` (store-A).
    """
    store_dir = resolve_sdk_store_dir(
        parent_id=parent_id,
        state_root=parent_state_root,
    )
    if store_dir is None:
        return None
    store_path = str(store_dir)
    ledger = CursorDispatchLedger.instance()
    ledger.record_state_root(dispatch_id=parent_id, state_root=store_path)
    ledger.record_state_root(dispatch_id=child_id, state_root=store_path)
    return store_path


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
    if parent is None or not parent.sdk_agent_id:
        return None
    store_dir = resolve_sdk_store_dir(
        parent_id=parent_id,
        state_root=parent.state_root,
    )
    if store_dir is None:
        return None
    return ResumeRunContext(
        resume_of=parent_id,
        state_root=str(store_dir),
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
