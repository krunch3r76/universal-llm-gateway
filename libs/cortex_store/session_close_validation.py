"""Session-close validation constants and helpers.

This module centralizes shared validation logic extracted from
dispatch_ops/ops_journals.py and routes/session_journals.py. It eliminates
the _USER_VOICE_RE regex duplication and removes the cross-layer import of
the private _emit_rejected helper (previously imported from routes into
dispatch_ops).
"""

from __future__ import annotations

import re
from typing import Any

from universal_logging import get_logger

from .dispatch_ops._shared import _SESSION_ID_RE, record

logger = get_logger(__name__)

_ACTION_LOG_RE = re.compile(
    r"^I (then |also )?(read|posted|dispatched|pulled|ran|wrote|called)",
    re.MULTILINE,
)
_USER_VOICE_RE = re.compile(r"\*\*User:\*\*|\bUser:\s|^#{1,4}\s+User\b", re.MULTILINE)
_ASSISTANT_VOICE_RE = re.compile(
    r"\*\*Assistant:\*\*|\bAssistant:\s|^#{1,4}\s+Assistant\b", re.MULTILINE
)


# Reason enum for mcp.session.close.rejected — see docs/event-contracts.md
_REJECT_REASONS = frozenset(
    {
        "transcript.hollow",
        "transcript.missing_structure",
        "summary.too_short",
        "session_id.invalid",
        "session.already_closed",
        "transcript_jsonl.invalid",
        "session_summary.invalid",
        "transcript_source.missing",
    }
)


def _emit_rejected(reason: str, *, session_id: str, agent: str, detail: str) -> None:
    """Emit mcp.session.close.rejected on every 422 reject path.

    Reason MUST be one of _REJECT_REASONS (enforced via assertion in dev).
    """
    assert reason in _REJECT_REASONS, f"unknown reject reason {reason!r}"
    record(
        "mcp.session.close.rejected",
        reason=reason,
        session_id=session_id,
        agent=agent,
        detail=detail,
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


def _validate_session_close_args(
    *,
    session_id: str | None,
    agent: str | None,
    transcript_jsonl_path: str | None,
    session_summary_md: str | None,
    summary: str | None,
    transcript_md: str | None = None,
    emit_rejected: bool = True,
) -> dict[str, Any] | None:
    """Lightweight arg-presence + session_id pattern + summary length gate.

    Runs the cheap checks that don't require touching the filesystem or
    parsing the JSONL.  Deep validation (path sandbox, JSONL parse,
    composed-transcript structure) is owned by the route handler so the
    atomic boundary stays in one place.  ``emit_rejected=False``
    suppresses ``mcp.session.close.rejected`` for preflight/dry_run
    probing.

    The verbatim source is an **either-of** constraint:
    ``{transcript_jsonl_path, transcript_md}`` — at least one MUST be
    present.  Cursor passes the path; web passes the markdown directly.
    """
    required = {
        "session_id": session_id,
        "agent": agent,
        "session_summary_md": session_summary_md,
        "summary": summary,
    }
    for field, val in required.items():
        if not val:
            return {"error": f"{field} is required"}
    if not transcript_jsonl_path and not transcript_md:
        detail = (
            "either transcript_jsonl_path (cursor) or transcript_md (web) "
            "is required — neither was supplied"
        )
        if emit_rejected and session_id and agent:
            _emit_rejected(
                "transcript_source.missing",
                session_id=session_id,
                agent=agent,
                detail=detail,
            )
        return {"error": detail, "reason": "transcript_source.missing"}
    assert session_id and agent and session_summary_md and summary

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
