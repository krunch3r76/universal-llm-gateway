"""Resume plane for timed-out cursor-sdk dispatches (``resume_of`` lineage).

Eligibility checks, timeout-retain prune gates, worktree registry transfer, and
SDK ``resume_agent`` orchestration live here so ``routes/cursor_sdk.py`` stays a
thin call site.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from claude_bundles.conductor_stop import parse_stop_tokens
from cursor_sdk import Client
from cursor_sdk.types import AgentOptions, LocalSendOptions, SendOptions
from fastapi.responses import JSONResponse
from universal_logging import get_logger
from universal_protocol import error_envelope

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_store_locus import (
    load_parent_row,
    load_row_columns,
    resolve_sdk_store_dir,
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
    "thread_mismatch",
    "bridge_death_not_resume_eligible",
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


def _record_data(record_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(record_json or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _bridge_death_resume_eligible(record_json: str | None) -> bool:
    rec = _record_data(record_json)
    if not rec.get("resume_eligible"):
        return False
    reason = str(rec.get("bridge_death_degraded_reason") or "")
    if reason == "bridge_read_timeout":
        return True
    forensics = rec.get("forensics")
    if isinstance(forensics, dict):
        cause = str(forensics.get("cause") or "")
        signal = str(forensics.get("signal_name") or "")
        if "bridge_read_timeout" in cause or signal == "SIGTERM":
            return True
    return reason == "bridge_read_timeout"


def _parked_for_restart(ledger: CursorDispatchLedger, *, parent_id: str) -> bool:
    """True only for ``park_for_restart`` (not ``await_cdp_reply`` — F4)."""
    data = load_row_columns(ledger, dispatch_id=parent_id, columns="park_kind")
    return bool(data and data.get("park_kind") == "park_for_restart")


def resume_eligibility_reason(
    ledger: CursorDispatchLedger, *, parent_id: str
) -> ResumeIneligibleReason | None:
    """Return ineligibility reason, or ``None`` when parent may be resumed.

    A ``park_for_restart`` parent is terminal ``cancelled`` by design with a
    consistent store, so only the identity and on-disk store checks apply to
    it — named here so the path is a contract, not incidental. Other park
    kinds (e.g. ``await_cdp_reply``) do not inherit that exemption (F4).
    """
    row = load_parent_row(ledger, parent_id=parent_id)
    if row is None:
        return "parent_missing"
    parked = _parked_for_restart(ledger, parent_id=parent_id)
    if not parked and row.status in _LIVE_STATUSES:
        if _bridge_death_resume_eligible(row.record_json):
            return None
        return "parent_still_live"
    if (
        not parked
        and row.terminal_status == "failed"
        and not _bridge_death_resume_eligible(row.record_json)
    ):
        rec = _record_data(row.record_json)
        if rec.get("bridge_death_partial_uri") and not rec.get("resume_eligible"):
            return "bridge_death_not_resume_eligible"
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
    parent_row = load_parent_row(
        CursorDispatchLedger.instance(), parent_id=req.resume_of
    )
    if parent_row is not None and req.thread_id != parent_row.thread_id:
        return _resume_ineligible_response(
            req,
            reason="thread_mismatch",
            detail=(
                "resume_of requires reuse_thread matching the parent worker thread"
            ),
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
    data = load_row_columns(
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
    data = load_row_columns(
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
    child = load_row_columns(
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


_DEFAULT_RESUME_NO_TOOLCALL_S = 300.0
_MCP_HANDSHAKE_NOT_OBSERVED = "not_observed"
# Bridge discovery (`cursor-sdk-bridge ready`) is not an MCP handshake.
# These substrings are the only stderr shapes GIW treats as a failed connect.
_MCP_HANDSHAKE_FAIL_MARKERS: tuple[tuple[str, str], ...] = (
    ("mcp server exited", "mcp_server_exited"),
    ("mcp handshake failed", "mcp_handshake_failed"),
    ("failed to connect to mcp", "mcp_connect_failed"),
)


class ResumeMcpHandshakeError(RuntimeError):
    """Park-resume MCP connect failed before the continuation ``send``.

    Raised only when the caller sets ``fail_on_mcp_handshake`` and
    ``observe_resume_mcp_handshake`` returns ``failed:<reason>``. The
    exception text and ``handshake_state`` are that same token so the
    failure envelope can quote it without parsing the traceback.
    """

    def __init__(self, handshake_state: str) -> None:
        self.handshake_state = handshake_state
        super().__init__(handshake_state)


def resume_no_toolcall_budget_s() -> float:
    """Seconds a park-resumed run may sit with zero successful tool calls.

    Reads ``CURSOR_SDK_RESUME_NO_TOOLCALL_S`` when it is a positive number.
    The default is 300. Non-numeric or non-positive values fall back to 300
    so a bad environment cannot disable the fast-fail or raise inside the
    idle wait loop. Callers should take ``min`` with the outer idle budget.
    """
    raw = os.environ.get("CURSOR_SDK_RESUME_NO_TOOLCALL_S", "").strip()
    if not raw:
        return _DEFAULT_RESUME_NO_TOOLCALL_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_RESUME_NO_TOOLCALL_S
    if value <= 0:
        return _DEFAULT_RESUME_NO_TOOLCALL_S
    return value


def classify_mcp_handshake_text(text: str | None) -> str:
    """Classify drained bridge stderr into an MCP handshake state.

    Success is not inferred: the bridge has no MCP-ready line for GIW.
    A known failure marker returns ``failed:<reason>``. Empty text and the
    discovery line ``cursor-sdk-bridge ready`` return ``not_observed``.
    Does not read the process table or spawn a server.
    """
    if not text or not text.strip():
        return _MCP_HANDSHAKE_NOT_OBSERVED
    lowered = text.lower()
    for marker, reason in _MCP_HANDSHAKE_FAIL_MARKERS:
        if marker in lowered:
            return f"failed:{reason}"
    return _MCP_HANDSHAKE_NOT_OBSERVED


def observe_resume_mcp_handshake(*, stderr_text: str | None = None) -> str:
    """Observe MCP connect state immediately after ``resume_agent`` returns.

    ``Client.resume_agent`` forwards ``mcpServers`` on the ResumeAgent RPC
    and returns only agent id and model, so a successful resume does not
    prove the stdio server came up. This reads the optional stderr snapshot
    the caller already drained. It does not open a second MCP process and
    does not mutate the client. ``ok`` is not produced: the bridge has no
    handshake success line for GIW to read.
    """
    return classify_mcp_handshake_text(stderr_text)


def run_progress_fields(
    *,
    first_toolcall_s: float | None,
    stream_tool_call_count: int,
    admitted_via: str | None = None,
    mcp_handshake: str | None = None,
) -> dict[str, Any]:
    """Progress fields for the terminal record and the failure envelope.

    ``first_toolcall_s`` is seconds from run start, or from resume start when
    the counter noted one, to the first successful tool call. ``None`` means
    none succeeded. ``stream_tool_call_count`` counts stream observations,
    including calls that never completed. Park-resume rows also carry
    ``mcp_handshake`` and ``admitted_via``. Fresh runs omit those two keys.
    """
    fields: dict[str, Any] = {
        "first_toolcall_s": first_toolcall_s,
        "stream_tool_call_count": stream_tool_call_count,
    }
    if admitted_via == "giw_park_resume":
        fields["admitted_via"] = admitted_via
        fields["mcp_handshake"] = mcp_handshake or _MCP_HANDSHAKE_NOT_OBSERVED
    return fields


def format_park_resume_failure_message(
    *,
    kind: str,
    since_last_progress_s: float,
    budget_s: float,
    first_toolcall_s: float | None,
    mcp_handshake: str,
    admitted_via: str,
) -> str:
    """Failure sentence with first_toolcall_s, handshake state, and admitted_via.

    ``kind='mcp_handshake'`` leads with the handshake token. Any other kind
    is the no-successful-tool-call fast-fail, including the idle budget.
    The three diagnostic tokens are always in the sentence so a bus reply
    is readable without opening the envelope data object.
    """
    first = "null" if first_toolcall_s is None else f"{first_toolcall_s:.3f}"
    if kind == "mcp_handshake":
        lead = f"cursor-sdk park-resume MCP handshake {mcp_handshake}"
    else:
        lead = (
            "cursor-sdk park-resume idle: no successful tool call "
            f"for {since_last_progress_s:.0f}s (budget {budget_s:.0f}s)"
        )
    return (
        f"{lead}; first_toolcall_s={first} "
        f"mcp_handshake={mcp_handshake} admitted_via={admitted_via}"
    )


def persist_run_progress(dispatch_id: str, fields: dict[str, Any]) -> None:
    """Merge progress fields into the dispatch ``record_json`` terminal blob.

    A missing ledger row is a no-op inside the merge. Any other failure is
    logged and swallowed so a telemetry write cannot replace the terminal
    status the caller is about to mark.
    """
    try:
        CursorDispatchLedger.instance().merge_record_json(
            dispatch_id=dispatch_id,
            patch=fields,
        )
    except Exception:
        logger.exception("run progress merge failed dispatch_id=%s", dispatch_id)


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
    on_handshake: Callable[[str], None] | None = None,
    stderr_text: str | None = None,
    fail_on_mcp_handshake: bool = False,
) -> tuple[Any, Any]:
    """Create or resume an SDK agent, then send the continuation turn.

    On resume, ``client.resume_agent`` receives ``agent_options`` unchanged,
    which is the rebuilt ``mcpServers`` map from ``build_agent_options``.
    The SDK applies that map on the ResumeAgent RPC and does not return
    connect state. Handshake observation runs before ``send``. When
    ``fail_on_mcp_handshake`` is set and the observation is ``failed:*``,
    this raises ``ResumeMcpHandshakeError`` and does not send. Fresh
    creates skip the handshake. ``on_handshake`` is invoked for every
    resume observation, including ``not_observed``.
    """
    if resume_ctx is not None:
        agent = client.resume_agent(resume_ctx.sdk_agent_id, agent_options)
        handshake = observe_resume_mcp_handshake(stderr_text=stderr_text)
        logger.info(
            "cursor sdk resume mcp handshake: agent_id=%s state=%s fail_closed=%s",
            resume_ctx.sdk_agent_id,
            handshake,
            fail_on_mcp_handshake,
        )
        if on_handshake is not None:
            on_handshake(handshake)
        if fail_on_mcp_handshake and handshake.startswith("failed:"):
            raise ResumeMcpHandshakeError(handshake)
        # Resume children exist only when the parent ledger row is terminal, so
        # unconditional force on resume is the deliberate superset of that done-when.
        run = agent.send(prompt, SendOptions(local=LocalSendOptions(force=True)))
    else:
        agent = client.create_agent(agent_options)
        run = agent.send(prompt)
    return agent, run
