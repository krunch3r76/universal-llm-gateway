"""Journal, session-close, and deadline ops."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..db import cortex_conn, execute, json_encode, query
from ..routes.assertions import _create_assertion_impl
from ..routes.deadlines import _RESOLVED_OUTCOMES, _list_deadlines_impl
from ..routes.session_journals import (
    _close_session_impl,
    _create_session_journal_impl,
    _emit_rejected,
    _list_session_journals_impl,
)
from ..transcript_assembly import (
    TranscriptPathError,
    assemble_verbatim_md,
    compose_full_transcript,
    resolve_jsonl_path,
)
from ._shared import _FILES_ROOT, _SESSION_ID_RE, _derive_session_id_local, record
from .ops_audit_detectors import run_detectors
from .ops_review_gate import _run_session_audit_or_block

logger = get_logger(__name__)

_ACTION_LOG_RE = re.compile(
    r"^I (then |also )?(read|posted|dispatched|pulled|ran|wrote|called)",
    re.MULTILINE,
)
_USER_VOICE_RE = re.compile(r"\*\*User:\*\*|\bUser:\s|^#{1,4}\s+User\b", re.MULTILINE)
_ASSISTANT_VOICE_RE = re.compile(
    r"\*\*Assistant:\*\*|\bAssistant:\s|^#{1,4}\s+Assistant\b", re.MULTILINE
)


def _validate_transcript_structure(
    transcript_md: str, summary_len: int = 0
) -> list[str]:
    """Return a list of structural warning strings (empty = clean).

    ∀ warnings: advisory only — callers must not block the close.
    The `user_blocks == 0` check has been promoted to a hard 422 in
    `routes/session_journals.py:close_session` (and mirrored in the dispatch
    handler's pre-write validation block). This advisory layer covers the
    secondary failure modes that don't gate the close: missing assistant voice,
    action-log pattern density, Canary 4 (transcript shorter than its summary).
    ∀ summary_len > 0: Canary 4 checked.
    """
    violations: list[str] = []

    user_blocks = len(_USER_VOICE_RE.findall(transcript_md))
    assistant_blocks = len(_ASSISTANT_VOICE_RE.findall(transcript_md))
    action_log_matches = len(_ACTION_LOG_RE.findall(transcript_md))

    # NOTE: user_blocks == 0 is now a hard 422 upstream (transcript.hollow);
    # we no longer surface it here to avoid double-reporting.
    if assistant_blocks == 0:
        violations.append("No assistant-voice blocks found")
    if action_log_matches >= 3 and user_blocks == 0:
        violations.append(
            f"Action-log pattern detected ({action_log_matches} matches, no user turns)"
        )
    if summary_len > 0 and len(transcript_md) <= summary_len:
        violations.append(
            f"Transcript length ({len(transcript_md)}) is not longer than "
            f"summary length ({summary_len}) — Canary 4"
        )

    return violations


def _append_session_close_warnings(
    result: dict[str, Any],
    *,
    session_id: str,
    agent: str,
) -> None:
    """Attach post-close warning findings for session continuity provenance gaps."""
    findings = run_detectors(
        kinds=["prior_session_id_omitted"],
        subject=f"transcript:{session_id}",
        include_filesystem=False,
    )
    if not findings:
        return
    warning_block = result.setdefault("_warning", {})
    if not isinstance(warning_block, dict):
        warning_block = {"upstream": warning_block}
        result["_warning"] = warning_block
    existing = warning_block.setdefault("post_close_findings", [])
    if isinstance(existing, list):
        existing.extend(findings)
    else:
        warning_block["post_close_findings"] = findings
    record(
        "cortex.session.audit.gaps.observed",
        session_id=session_id,
        agent=agent,
        gap_count=len(findings),
        criticals=[],
        deferred=[],
        mode="post_close_warning",
    )


def _op_deadlines(**_: object) -> dict[str, Any]:
    return _list_deadlines_impl()


def _op_deadline_resolve(
    deadline_id: str | None = None,
    resolution_note: str | None = None,
    resolved_at: str | None = None,
    evidence: str | None = None,
    fulfilling_assertion_id: int | None = None,
    outcome: str = "met",
    **_: object,
) -> dict[str, Any]:
    """Atomically close a deadline entity: write confirmed assertion + set outcome.

    ∀ deadline entity: two writes are required to stop it surfacing in
    deadlines() — a confirmed RESOLVED assertion on the deadline entity AND
    outcome in its attributes JSON. Agents historically forget the
    second write; this op performs both reliably.
    """
    if not deadline_id:
        return {"error": "deadline_id is required"}
    if not resolution_note:
        return {"error": "resolution_note is required"}
    if not resolved_at:
        return {"error": "resolved_at is required"}
    if outcome not in _RESOLVED_OUTCOMES:
        return {"error": f"outcome must be one of {sorted(_RESOLVED_OUTCOMES)}"}

    # 1. Read current deadline entity and its attributes.
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, type, attributes FROM entities WHERE id = ? AND type = 'deadline'",
            (deadline_id,),
        )
        if not rows:
            return {
                "error": f"Deadline entity not found or not type='deadline': {deadline_id}"
            }

        attrs_raw = rows[0]["attributes"]
        current_attrs: dict[str, Any] = (
            json.loads(attrs_raw) if isinstance(attrs_raw, str) and attrs_raw else {}
        )

    # 2. Write confirmed RESOLVED assertion on the deadline entity.
    assertion_body: dict[str, Any] = {
        "entity_id": deadline_id,
        "claim": f"RESOLVED — {resolution_note}",
        "confidence": "confirmed",
        "evidence": evidence or f"deadline_resolve called; resolved_at={resolved_at}",
        "derivation_type": "agent_observation",
        "observed_at": datetime.now(UTC).isoformat(),
        "confidence_score": 1.0,
    }
    if fulfilling_assertion_id is not None:
        assertion_body["fulfillment_assertion_id"] = fulfilling_assertion_id

    try:
        assertion_result = _create_assertion_impl(assertion_body)
    except HTTPException as exc:
        return {"error": f"Assertion write failed: {exc.detail}", "step": "assert"}

    resolution_assertion_id = (assertion_result.get("item") or {}).get("id")

    # 3. Merge outcome into current attributes (non-destructive merge).
    merged_attrs = {**current_attrs, "outcome": outcome}
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    outcome_set = False
    try:
        with cortex_conn() as conn:
            execute(
                conn,
                "UPDATE entities SET attributes = ?, updated_at = ? WHERE id = ?",
                (json_encode(merged_attrs), now, deadline_id),
            )
        outcome_set = True
    except sqlite3.Error as exc:
        logger.warning("deadline_resolve outcome update failed: %s", exc)
        record(
            "mcp.cortex.deadline.outcome.failed",
            deadline_id=deadline_id,
            error=str(exc),
        )

    logger.info(
        "deadline_resolve: %s — assertion=%s outcome=%s outcome_set=%s",
        deadline_id,
        resolution_assertion_id,
        outcome,
        outcome_set,
    )
    record("mcp.cortex.deadline.resolved", deadline_id=deadline_id)

    return {
        "deadline_id": deadline_id,
        "resolution_assertion_id": resolution_assertion_id,
        "outcome": outcome,
        "outcome_set": outcome_set,
    }


def _op_journal_read(
    limit: int | None = None, agent: str | None = None, **_: object
) -> dict[str, Any]:
    return _list_session_journals_impl(limit=limit or 3, agent=agent)


def _op_journal_write(
    timestamp: str | None = None,
    agent: str | None = None,
    summary: str | None = None,
    domains: list[str] | None = None,
    decisions: list[str] | None = None,
    open_items: list[str] | None = None,
    entity_ids: list[str] | None = None,
    file_path: str | None = None,
    session_id: str | None = None,
    prior_session_id: str | None = None,
    markdown_content: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required_fields = {"timestamp": timestamp, "agent": agent, "summary": summary}
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    assert agent is not None and timestamp is not None

    derived_id = session_id or _derive_session_id_local(agent, timestamp)

    if markdown_content is not None:
        journal_path = _FILES_ROOT / "notes" / "system" / "journal" / f"{derived_id}.md"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(markdown_content, encoding="utf-8")
        logger.info("journal_write: wrote markdown to %s", journal_path)

    body: dict[str, Any] = {
        "timestamp": timestamp,
        "agent": agent,
        "summary": summary,
        **({} if domains is None else {"domains": domains}),
        **({} if decisions is None else {"decisions": decisions}),
        **({} if open_items is None else {"open_items": open_items}),
        **({} if entity_ids is None else {"entity_ids": entity_ids}),
        **({} if file_path is None else {"file_path": file_path}),
        **({} if session_id is None else {"session_id": session_id}),
        **({} if prior_session_id is None else {"prior_session_id": prior_session_id}),
    }
    result = _create_session_journal_impl(body)
    if "error" not in result:
        transcript_entity_id = result.get("transcript_entity_id", "")
        logger.info(
            "cortex journal_write: %s agent=%s transcript=%s",
            timestamp,
            agent,
            transcript_entity_id,
        )
    return result


def _validate_session_close_args(
    *,
    session_id: str | None,
    agent: str | None,
    transcript_jsonl_path: str | None,
    session_summary_md: str | None,
    summary: str | None,
    emit_rejected: bool = True,
) -> dict[str, Any] | None:
    """Lightweight arg-presence + session_id pattern + summary length gate.

    Runs the cheap checks that don't require touching the filesystem or
    parsing the JSONL.  Deep validation (path sandbox, JSONL parse,
    composed-transcript structure) is owned by the route handler so the
    atomic boundary stays in one place.  ``emit_rejected=False``
    suppresses ``mcp.session.close.rejected`` for preflight/dry_run
    probing.
    """
    required = {
        "session_id": session_id,
        "agent": agent,
        "transcript_jsonl_path": transcript_jsonl_path,
        "session_summary_md": session_summary_md,
        "summary": summary,
    }
    for field, val in required.items():
        if not val:
            return {"error": f"{field} is required"}
    assert (
        session_id
        and agent
        and transcript_jsonl_path
        and session_summary_md
        and summary
    )

    def _reject(reason: str, detail: str) -> dict[str, Any]:
        if emit_rejected:
            _emit_rejected(reason, session_id=session_id, agent=agent, detail=detail)
        return {"error": detail, "reason": reason}

    if not _SESSION_ID_RE.match(session_id):
        return _reject(
            "session_id.invalid",
            f"session_id {session_id!r} does not match "
            "pattern {{agent}}-YYYY-MM-DD-HHMM",
        )
    if len(summary) < 20:
        return _reject(
            "summary.too_short",
            f"summary must be >= 20 characters (got {len(summary)})",
        )
    if "## Session Summary" not in session_summary_md:
        return _reject(
            "session_summary.invalid",
            "session_summary_md must contain a '## Session Summary' heading "
            "(structural layer the agent composes — decisions, files modified, "
            "continuation state).",
        )
    return None


def _safe_run_audit(
    *,
    session_id: str,
    agent: str,
    entity_ids: list[str],
    defer_gaps: dict[str, str] | None,
) -> dict[str, Any]:
    """Run audit gate with hard non-blocking guarantee (todo P4).

    Broad ``except Exception`` is required to guarantee the audit never
    blocks a session close; all paths log before degrading to a warning.
    """
    try:
        return _run_session_audit_or_block(
            session_id=session_id,
            agent=agent,
            entity_ids=entity_ids,
            defer_gaps=defer_gaps,
        )
    except Exception as exc:
        logger.warning(
            "session audit gate raised — degrading to warning (session=%s agent=%s)",
            session_id,
            agent,
            exc_info=True,
        )
        return {
            "warning": {
                "audit_degraded": True,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        }


def _check_transcript_hollow_guards(composed: str) -> dict[str, Any] | None:
    """Return a rejection payload when composed transcript fails a hard guard.

    ∀ guards: exact thresholds mirrored from routes/session_journals.py close_session.
    ∀ return None: all guards pass.
    """
    if len(composed) < 200:
        return {
            "reason": "transcript.missing_structure",
            "error": (
                f"composed transcript is {len(composed)} chars "
                "(< 200) — JSONL may be empty or session_summary_md too thin."
            ),
            "hollow": True,
        }
    if "## Turn" not in composed and "## Session Summary" not in composed:
        return {
            "reason": "transcript.missing_structure",
            "error": (
                "composed transcript missing structural headings — "
                "'## Turn' blocks and '## Session Summary' both absent."
            ),
            "hollow": True,
        }
    if len(_USER_VOICE_RE.findall(composed)) == 0:
        return {
            "reason": "transcript.hollow",
            "error": (
                "composed transcript has zero User-voice blocks — "
                "JSONL contained no user messages."
            ),
            "hollow": True,
        }
    return None


def _op_session_close_preflight(
    session_id: str | None = None,
    agent: str | None = None,
    transcript_jsonl_path: str | None = None,
    session_summary_md: str | None = None,
    summary: str | None = None,
    entity_ids: list[str] | None = None,
    defer_gaps: dict[str, str] | None = None,
    **_: object,
) -> dict[str, Any]:
    """Validate args + path sandbox + audit-gate health WITHOUT writing.

    Returns ``{"ok": True, "audit": {...}, "turn_count": int}`` on a path
    that would succeed at close time, or ``{"ok": False, "error", "reason"}``
    otherwise.  Verbatim assembly is performed in-memory (no file
    written, no DB row) so the agent learns about a bad JSONL before
    paying for the audit and DB tx.
    """
    arg_error = _validate_session_close_args(
        session_id=session_id,
        agent=agent,
        transcript_jsonl_path=transcript_jsonl_path,
        session_summary_md=session_summary_md,
        summary=summary,
        emit_rejected=False,
    )
    if arg_error is not None:
        return {"ok": False, **arg_error}
    assert (
        session_id
        and agent
        and transcript_jsonl_path
        and session_summary_md
        and summary
    )

    try:
        resolved = resolve_jsonl_path(transcript_jsonl_path)
        verbatim_md, turn_count = assemble_verbatim_md(
            jsonl_path=resolved, session_id=session_id
        )
    except TranscriptPathError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "reason": "transcript_jsonl.invalid",
        }
    except ValueError as exc:
        return {
            "ok": False,
            "error": f"JSONL parse error: {exc}",
            "reason": "transcript_jsonl.invalid",
        }

    composed = compose_full_transcript(verbatim_md, session_summary_md)

    guard_error = _check_transcript_hollow_guards(composed)
    if guard_error is not None:
        return {"ok": False, **guard_error}

    audit_outcome = _safe_run_audit(
        session_id=session_id,
        agent=agent,
        entity_ids=entity_ids or [],
        defer_gaps=defer_gaps,
    )
    if audit_outcome.get("blocked"):
        return {"ok": False, "reason": "session_audit_blocked", **audit_outcome}

    structural_warnings = _validate_transcript_structure(
        composed, summary_len=len(summary)
    )
    return {
        "ok": True,
        "audit": audit_outcome,
        "turn_count": turn_count,
        "byte_count": len(composed.encode("utf-8")),
        "warnings": structural_warnings,
    }


def _op_session_close(
    session_id: str | None = None,
    agent: str | None = None,
    transcript_jsonl_path: str | None = None,
    session_summary_md: str | None = None,
    summary: str | None = None,
    domains: list[str] | None = None,
    decisions: list[str] | None = None,
    open_items: list[str] | None = None,
    entity_ids: list[str] | None = None,
    prior_session_id: str | None = None,
    handoff_prompt: str | None = None,
    assistant_label: str | None = None,
    defer_gaps: dict[str, str] | None = None,
    dry_run: bool = False,
    **_: object,
) -> dict[str, Any]:
    """Atomic session close (server-side transcript derivation).

    Flow:
      1. Cheap arg + session_id + summary validation.
      2. Audit gate — may BLOCK before any file/DB write.
      3. If ``dry_run``: assemble in-memory, validate, return preview.
      4. Hand off to the route handler (`_close_session_impl`) which
         owns the atomic boundary: resolve path → assemble verbatim →
         compose → write file → DB tx → content_hash.
      5. Append audit warnings + post-close detectors + structural
         warnings to the response.

    See session-close-server-side-transcript Phase 2 for the architecture
    rewrite; the route handler in `routes/session_journals.py` is the
    single atomic boundary.
    """
    arg_error = _validate_session_close_args(
        session_id=session_id,
        agent=agent,
        transcript_jsonl_path=transcript_jsonl_path,
        session_summary_md=session_summary_md,
        summary=summary,
        emit_rejected=not dry_run,
    )
    if arg_error is not None:
        if dry_run:
            return {"dry_run": True, "would_fail": True, **arg_error}
        return {k: v for k, v in arg_error.items() if k != "reason"}
    assert (
        session_id
        and agent
        and transcript_jsonl_path
        and session_summary_md
        and summary
    )

    audit_outcome = _safe_run_audit(
        session_id=session_id,
        agent=agent,
        entity_ids=entity_ids or [],
        defer_gaps=defer_gaps,
    )
    if audit_outcome.get("blocked"):
        if dry_run:
            return {"dry_run": True, "would_fail": True, **audit_outcome}
        return audit_outcome

    if dry_run:
        try:
            resolved = resolve_jsonl_path(transcript_jsonl_path)
            verbatim_md, turn_count = assemble_verbatim_md(
                jsonl_path=resolved,
                session_id=session_id,
                assistant_label=assistant_label,
            )
        except TranscriptPathError as exc:
            return {
                "dry_run": True,
                "would_fail": True,
                "error": str(exc),
                "reason": "transcript_jsonl.invalid",
            }
        except ValueError as exc:
            return {
                "dry_run": True,
                "would_fail": True,
                "error": f"JSONL parse error: {exc}",
                "reason": "transcript_jsonl.invalid",
            }
        composed = compose_full_transcript(verbatim_md, session_summary_md)
        guard_error = _check_transcript_hollow_guards(composed)
        if guard_error is not None:
            return {"dry_run": True, "would_fail": True, **guard_error}
        structural_warnings = _validate_transcript_structure(
            composed, summary_len=len(summary)
        )
        return {
            "dry_run": True,
            "would_succeed": True,
            "warnings": structural_warnings,
            "audit": audit_outcome,
            "turn_count": turn_count,
            "byte_count": len(composed.encode("utf-8")),
        }

    body: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "transcript_jsonl_path": transcript_jsonl_path,
        "session_summary_md": session_summary_md,
        "summary": summary,
    }
    for key, val in [
        ("domains", domains),
        ("decisions", decisions),
        ("open_items", open_items),
        ("entity_ids", entity_ids),
        ("prior_session_id", prior_session_id),
        ("handoff_prompt", handoff_prompt),
        ("assistant_label", assistant_label),
    ]:
        if val is not None:
            body[key] = val

    try:
        result = _close_session_impl(body)
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            return {
                "error": detail.get("message") or "session_close rejected",
                **detail,
            }
        return {"error": str(detail), "reason": "session_close.rejected"}
    except Exception as exc:
        logger.error(
            "session_close: route handler raised for %s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        return {"error": f"Session close failed: {exc}"}

    if "error" in result:
        return result

    if audit_outcome.get("warning"):
        result["_warning"] = audit_outcome["warning"]
    _append_session_close_warnings(result, session_id=session_id, agent=agent)

    transcript_path = result.get("transcript_path", "")
    abs_path = _FILES_ROOT / transcript_path if transcript_path else None
    transcript_warnings: list[str] = []
    if abs_path and abs_path.is_file():
        try:
            transcript_warnings = _validate_transcript_structure(
                abs_path.read_text(encoding="utf-8"), summary_len=len(summary)
            )
        except OSError as exc:
            logger.warning(
                "session_close advisory re-read failed for %s: %s",
                transcript_path,
                exc,
            )
    if transcript_warnings:
        logger.warning(
            "session_close: transcript structure warnings for %s: %s",
            session_id,
            transcript_warnings,
        )
    result["transcript_warnings"] = transcript_warnings

    return result
