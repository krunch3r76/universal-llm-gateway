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

from .db import query
from .dispatch_ops._detectors._shared import _finding
from .dispatch_ops._shared import (
    _AGENT_SLUG_EXAMPLES,
    _AGENT_SLUG_RE,
    _AGENT_SLUG_RE_SOURCE,
    _SESSION_ID_EXAMPLES,
    _SESSION_ID_RE,
    _SESSION_ID_RE_SOURCE,
    record,
)

logger = get_logger(__name__)

_ACTION_LOG_RE = re.compile(
    r"^I (then |also )?(read|posted|dispatched|pulled|ran|wrote|called)",
    re.MULTILINE,
)
_USER_VOICE_RE = re.compile(r"\*\*User:\*\*|\bUser:\s|^#{1,4}\s+User\b", re.MULTILINE)
_ASSISTANT_VOICE_RE = re.compile(
    r"\*\*Assistant:\*\*|\bAssistant:\s|^#{1,4}\s+Assistant\b", re.MULTILINE
)


def handoff_close_requested(
    *,
    handoff_prompt: str | None = None,
    handoff_source_path: str | None = None,
) -> bool:
    """True when session_close will persist or derive a handoff."""
    if handoff_prompt and handoff_prompt.strip():
        return True
    return bool(handoff_source_path and handoff_source_path.strip())


def reject_handoff_at_none_depth(
    *,
    transcript_depth: str,
    handoff_prompt: str | None = None,
    handoff_source_path: str | None = None,
) -> dict[str, Any] | None:
    """Reject depth=none when a handoff is supplied (no transcript entity to mirror)."""
    if transcript_depth != "none":
        return None
    if not handoff_close_requested(
        handoff_prompt=handoff_prompt,
        handoff_source_path=handoff_source_path,
    ):
        return None
    return build_validation_error(
        reason="handoff.requires_transcript_entity",
        field="transcript_depth",
        received=transcript_depth,
        expected=(
            'transcript_depth in {"light", "verbatim"} when handoff_prompt or '
            "handoff_source_path is set"
        ),
        examples=["light", "verbatim"],
        hint=(
            "Handoff mirrors to transcript:{session_id} entity attributes; "
            'depth="none" creates no transcript entity. Use at least depth="light" '
            "(session_summary_md alone is the on-disk file for web/API seats)."
        ),
        detail=(
            'transcript_depth="none" is incompatible with handoff_prompt or '
            "handoff_source_path — use light (minimum) or verbatim."
        ),
    )


# Canonical structural-layer heading. The contract requires this literal H2;
# `normalize_session_summary_heading` liberalizes near-misses to it.
_SUMMARY_HEADING_LITERAL = "## Session Summary"
# Line-anchored canonical check. A bare substring test gives a false positive
# for `### Session Summary` (the H2 literal is a substring of the H3 line), so
# the no-op short-circuit must match the literal as its own heading line.
_SUMMARY_HEADING_CANONICAL_RE = re.compile(r"^## Session Summary[ \t]*$", re.MULTILINE)
# A near-miss heading line: any heading level (`#`..`######`), optional
# surrounding whitespace, "session summary" case-insensitive, optional
# trailing colon. Matches `# Session Summary`, `### session summary:`,
# `##  Session   Summary`, etc. — but NOT a bare `## Summary` (no "session").
_SUMMARY_HEADING_VARIANT_RE = re.compile(
    r"^[ \t]*#{1,6}[ \t]*session[ \t]+summary[ \t]*:?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def normalize_session_summary_heading(
    session_summary_md: str,
) -> tuple[str, dict[str, Any] | None]:
    """Liberalize the ``## Session Summary`` heading (Postel's law).

    The structural-layer contract requires a literal ``## Session Summary``
    H2. Agents under load routinely emit a near-miss (``# Session Summary``,
    ``### Session Summary:``, extra whitespace) or omit the heading entirely
    while still authoring valid body content. Rejecting these costs a round
    trip; the agent-authored content is intact. This normalizes in place:

      - literal heading already present  → unchanged, no warning
      - a recognizable heading *variant* → that line rewritten to the literal
      - no recognizable heading at all   → literal prepended above the content

    Returns ``(normalized_md, warning | None)``. ``warning`` mirrors the
    audit-finding shape (``kind``/``subject``/``severity``/``detail``) so it
    rides the existing ``audit_warnings`` channel. Idempotent: re-applying to
    already-normalized markdown is a no-op (literal-present short-circuit).

    Empty/whitespace input is returned unchanged with no warning — the
    non-empty precondition is enforced separately by the caller.
    """
    if not session_summary_md.strip():
        return session_summary_md, None
    if _SUMMARY_HEADING_CANONICAL_RE.search(session_summary_md):
        return session_summary_md, None

    variant = _SUMMARY_HEADING_VARIANT_RE.search(session_summary_md)
    if variant is not None:
        normalized = (
            session_summary_md[: variant.start()]
            + _SUMMARY_HEADING_LITERAL
            + session_summary_md[variant.end() :]
        )
        warning = {
            "kind": "session_summary_heading_normalized",
            "subject": "session_summary_md",
            "severity": "info",
            "detail": (
                f"structural-layer heading {variant.group().strip()!r} "
                f"normalized to '{_SUMMARY_HEADING_LITERAL}'."
            ),
        }
        return normalized, warning

    normalized = f"{_SUMMARY_HEADING_LITERAL}\n\n{session_summary_md.lstrip()}"
    warning = {
        "kind": "session_summary_heading_normalized",
        "subject": "session_summary_md",
        "severity": "info",
        "detail": (
            "structural-layer markdown had no recognizable 'Session Summary' "
            f"heading; '{_SUMMARY_HEADING_LITERAL}' was prepended."
        ),
    }
    return normalized, warning


# Reason enum for mcp.session.close.rejected — see docs/event-contracts.md
_REJECT_REASONS = frozenset(
    {
        "transcript.hollow",
        "transcript.grammar_invalid",
        "transcript.missing_structure",
        "summary.too_short",
        "session_id.invalid",
        "session.already_closed",
        "transcript_jsonl.invalid",
        "session_summary.invalid",
        "transcript_source.missing",
        "handoff.requires_transcript_entity",
        "handoff.missing_transcript_anchor",
        "agent.invalid",
    }
)


def build_validation_error(
    *,
    reason: str,
    field: str,
    received: Any,
    expected: str,
    examples: tuple[str, ...] | list[str],
    hint: str,
    detail: str | None = None,
) -> dict[str, Any]:
    """Construct a structured MCP validation error payload.

    The shape is the cross-tool standard for any input-validation 422 on the
    session-close surface: an LLM caller can read ``expected`` + ``examples``
    + ``hint`` and fix its next call without external help.  ``error`` and
    ``reason`` are kept for back-compat with consumers that key off them.
    """
    msg = detail or f"{field} {received!r} does not match {expected}"
    return {
        "error": msg,
        "reason": reason,
        "field": field,
        "received": received,
        "expected": expected,
        "examples": list(examples),
        "hint": hint,
    }


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
    transcript_md: str, summary_len: int = 0, transcript_depth: str = "verbatim"
) -> list[str]:
    """Return a list of structural warning strings (empty = clean).

    ∀ warnings: advisory only — callers must not block the close.
    The `user_blocks == 0` check has been promoted to a hard 422 in
    `routes/session_journals.py:close_session` (and mirrored in the dispatch
    handler's pre-write validation block). This advisory layer covers the
    secondary failure modes that don't gate the close: missing assistant voice,
    action-log pattern density, Canary 4 (transcript shorter than its summary).
    ∀ summary_len > 0 ∧ transcript_depth == "verbatim": Canary 4 checked.

    Non-verbatim depths short-circuit to empty — the file is structural-only
    (``light``) or absent (``none``), so verbatim canaries don't apply.
    """
    if transcript_depth != "verbatim":
        return []

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
    transcript_depth: str = "verbatim",
    handoff_prompt: str | None = None,
    handoff_source_path: str | None = None,
    emit_rejected: bool = True,
    skip_handoff_depth_check: bool = False,
) -> dict[str, Any] | None:
    """Lightweight arg-presence + session_id pattern + summary length gate.

    Runs the cheap checks that don't require touching the filesystem or
    parsing the JSONL.  Deep validation (path sandbox, JSONL parse,
    composed-transcript structure) is owned by the route handler so the
    atomic boundary stays in one place.  ``emit_rejected=False``
    suppresses ``mcp.session.close.rejected`` for preflight/dry_run
    probing.

    The verbatim source is required only when ``transcript_depth == "verbatim"``
    (one of ``{transcript_jsonl_path, transcript_md}``). When
    ``transcript_depth == "light"``, the on-disk file is ``session_summary_md``
    only — no transcript source required. When ``transcript_depth == "none"``,
    neither source nor transcript entity is written (journal row only).
    Cursor passes the path at verbatim; web passes markdown at verbatim.

    Handoff at ``none`` is rejected (``handoff.requires_transcript_entity``)
    unless ``skip_handoff_depth_check=True`` (used by dry_run preview, which
    does not write and therefore does not need a transcript entity).
    """
    required = {
        "session_id": session_id,
        "agent": agent,
        "summary": summary,
    }
    for field, val in required.items():
        if not val:
            hint = f"Supply a non-empty {field} on the session_close call."
            if field == "summary":
                hint = (
                    "session_close_preflight / session_close require summary "
                    "(and session_summary_md). Not an ID-only probe — placeholders "
                    "are OK for session_id resolution (session-close.mdc §0b / "
                    "/session-end Step 0b)."
                )
            return {
                "error": f"{field} is required",
                "reason": f"{field}.required",
                "field": field,
                "received": val,
                "expected": "non-empty string",
                "examples": [],
                "hint": hint,
            }
    # session_summary_md may be omitted when session_summary_md_path was
    # resolved earlier (path wins if both set). Callers must resolve the path
    # before this validator so the populated text is present here.
    if not session_summary_md:
        return {
            "error": "session_summary_md is required",
            "reason": "session_summary_md.required",
            "field": "session_summary_md",
            "received": session_summary_md,
            "expected": "non-empty string or session_summary_md_path",
            "examples": [],
            "hint": (
                "Supply session_summary_md inline, or pass "
                "session_summary_md_path (sandbox-rooted; path wins if both set)."
            ),
        }
    handoff_reject = reject_handoff_at_none_depth(
        transcript_depth=transcript_depth,
        handoff_prompt=handoff_prompt,
        handoff_source_path=handoff_source_path,
    )
    if handoff_reject is not None and not skip_handoff_depth_check:
        if emit_rejected and session_id and agent:
            _emit_rejected(
                handoff_reject["reason"],
                session_id=session_id,
                agent=agent,
                detail=handoff_reject["error"],
            )
        return handoff_reject
    if (
        transcript_depth == "verbatim"
        and not transcript_jsonl_path
        and not transcript_md
    ):
        # Seat-specific guidance: the slug tells us which source the agent
        # should have supplied, so the retry is a one-shot fix rather than a
        # guess between two options.
        is_cursor_seat = bool(agent) and "cursor" in agent
        if is_cursor_seat:
            seat_hint = (
                "This is a Cursor seat: re-run Step 0 "
                "(`ls -lt $CURSOR_AGENT_TRANSCRIPTS_ROOT | head -2`), take the "
                "most-recently-modified UUID directory, and pass its .jsonl "
                "as transcript_jsonl_path. Do NOT read or paste the file."
            )
        else:
            seat_hint = (
                "This is a web/API seat: pass the verbatim conversation "
                "markdown via transcript_md (server assembles the dual layer)."
            )
        detail = (
            f"transcript_depth={transcript_depth!r} requires a transcript "
            f"source, but neither transcript_jsonl_path nor transcript_md was "
            f'supplied. {seat_hint} (Use transcript_depth="light" when a '
            'structural summary + handoff suffice; "none" only when no handoff '
            "and no transcript entity is needed.)"
        )
        if emit_rejected and session_id and agent:
            _emit_rejected(
                "transcript_source.missing",
                session_id=session_id,
                agent=agent,
                detail=detail,
            )
        return {
            "error": detail,
            "reason": "transcript_source.missing",
            "field": ("transcript_jsonl_path" if is_cursor_seat else "transcript_md"),
            "received": None,
            "expected": (
                "transcript_jsonl_path (Cursor seat)"
                if is_cursor_seat
                else "transcript_md (web/API seat)"
            )
            + f" for transcript_depth={transcript_depth!r}; omit at light/none "
            "(light uses session_summary_md as the file; none writes no file)",
            "examples": [],
            "hint": seat_hint,
        }
    assert session_id and agent and session_summary_md and summary

    def _reject(payload: dict[str, Any]) -> dict[str, Any]:
        if emit_rejected:
            _emit_rejected(
                payload["reason"],
                session_id=session_id,
                agent=agent,
                detail=payload["error"],
            )
        return payload

    if not _SESSION_ID_RE.match(session_id):
        return _reject(
            build_validation_error(
                reason="session_id.invalid",
                field="session_id",
                received=session_id,
                expected=_SESSION_ID_RE_SOURCE,
                examples=_SESSION_ID_EXAMPLES,
                hint=(
                    "Agent slugs may contain hyphens (e.g. claude-web, "
                    "api-claude) — the full slug must precede the "
                    "YYYY-MM-DD-HHMMSS-{3hex} timestamp."
                ),
                detail=(
                    f"session_id {session_id!r} does not match "
                    f"pattern {_SESSION_ID_RE_SOURCE} "
                    "({agent-slug}-YYYY-MM-DD-HHMMSS-{3hex}, lowercase)."
                ),
            )
        )
    if not _AGENT_SLUG_RE.match(agent):
        return _reject(
            build_validation_error(
                reason="agent.invalid",
                field="agent",
                received=agent,
                expected=_AGENT_SLUG_RE_SOURCE,
                examples=list(_AGENT_SLUG_EXAMPLES),
                hint=(
                    "agent is a routing/metadata hint (no allowlist) — "
                    "must be a lowercase slug starting with a letter "
                    "(hyphens allowed)."
                ),
                detail=(
                    f"agent {agent!r} is not a valid lowercase slug "
                    f"(expected {_AGENT_SLUG_RE_SOURCE})."
                ),
            )
        )
    if len(summary) < 20:
        return _reject(
            build_validation_error(
                reason="summary.too_short",
                field="summary",
                received=summary,
                expected="length >= 20",
                examples=[],
                hint=(
                    "summary is the short synthesis used for the journal "
                    "row + entity name — write at least one full sentence."
                ),
                detail=f"summary must be >= 20 characters (got {len(summary)})",
            )
        )
    # Heading presence is no longer a hard reject: the close path normalizes
    # near-misses (or prepends the heading) via normalize_session_summary_heading
    # and emits an advisory warning instead of a 422. Only genuinely empty /
    # whitespace-only structural layers are rejected here.
    if not session_summary_md.strip():
        return _reject(
            build_validation_error(
                reason="session_summary.invalid",
                field="session_summary_md",
                received=session_summary_md,
                expected="non-empty structural-layer markdown",
                examples=["## Session Summary\\n…\\n## Decisions\\n…"],
                hint=(
                    "session_summary_md is the structural layer the agent "
                    "composes (decisions, files modified, continuation "
                    "state) — it must be non-empty. A '## Session Summary' "
                    "heading is added automatically if absent."
                ),
                detail="session_summary_md must be non-empty (structural layer).",
            )
        )
    return None


def _check_transcript_hollow_guards(
    composed: str, transcript_depth: str = "verbatim"
) -> dict[str, Any] | None:
    """Return a rejection payload when composed transcript fails a hard guard.

    Guards by depth:
      - ``verbatim``: all three guards (length >= 200, structural
        headings present, >=1 User-voice block).
      - ``light``: only the structural-headings guard (the file is the
        ``session_summary_md`` content — no User-voice blocks expected).
      - ``none``: no guards (no file is written; this function should
        not be called, but is a no-op if it is).

    ∀ return None: all applicable guards pass.
    """
    if transcript_depth == "none":
        return None
    # Both verbatim and light require the structural-headings check
    # (## Session Summary is always present in session_summary_md).
    if "## Turn" not in composed and "## Session Summary" not in composed:
        return {
            "reason": "transcript.missing_structure",
            "error": (
                "composed transcript missing structural headings — "
                "'## Turn' blocks and '## Session Summary' both absent."
            ),
            "hollow": True,
        }
    if transcript_depth == "light":
        # Light-depth files contain only the structural layer; no
        # verbatim turns are expected, so skip the length-200 check
        # (a tight summary may be shorter) and the User-voice check.
        return None
    # Verbatim path: full guard set.
    if len(composed) < 200:
        return {
            "reason": "transcript.missing_structure",
            "error": (
                f"composed transcript is {len(composed)} chars "
                "(< 200) — JSONL may be empty or session_summary_md too thin."
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


def _audit_normalization_refusals_for_session(
    conn, session_id: str
) -> list[dict[str, Any]]:
    """Return advisory findings for assertions written during the session
    whose normalization refused due to collision.

    Returns [] when no refusals — close proceeds cleanly. Returns a list
    of finding dicts ({kind, subject, severity, detail, audit_id}) when
    refusals exist; the route handler attaches these to the close
    response as an advisory `audit_warnings` field. NEVER returns a
    rejection — Path 3 is informational only, per the substrate v1.3
    §13 enforcement-layer split (audit-backstop, not write-time gate).

    Session attribution is via `evidence LIKE '%[<session-tag>]%'` —
    the existing _SESSION_TAG_RE pattern from
    routes/assertions/_shared.py. Same pattern boot already uses.
    """
    if not session_id:
        return []
    tag = f"[{session_id}]"
    sql = """
        SELECT id, entity_id, raw_predicate_form, normalization_decision,
               candidate_set_fingerprint
        FROM assertions
        WHERE normalization_decision IN ('collision_refused',
                                          'alias_collision_refused')
          AND superseded_by IS NULL
          AND evidence LIKE ?
    """
    rows = query(conn, sql, (f"%{tag}%",))
    return [
        _finding(
            "unresolved_bare_token_in_predicate_form",
            str(r["id"]),
            f"Assertion {r['id']} on {r['entity_id']}: predicate_form "
            f"{r.get('raw_predicate_form')!r} refused due to "
            f"{r.get('normalization_decision')} (fingerprint "
            f"{r.get('candidate_set_fingerprint')}).",
        )
        for r in rows
    ]
