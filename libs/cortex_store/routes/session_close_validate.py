"""Session-close request validation and transcript assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..dispatch_ops._session_summary_path import resolve_session_summary_md
from ..dispatch_ops._shared import (
    _AGENT_SLUG_EXAMPLES,
    _AGENT_SLUG_RE,
    _AGENT_SLUG_RE_SOURCE,
    _SESSION_ID_EXAMPLES,
    _SESSION_ID_RE,
    _SESSION_ID_RE_SOURCE,
)
from ..handoff_audit import check_handoff_transcript_anchor
from ..models import SessionCloseRequest
from ..session_close_successor_hop import (
    SUCCESSION_FILL_REASON,
    lookup_sealed_journal,
    resolve_successor_hop,
)
from ..session_close_validation import (
    _USER_VOICE_RE,
    build_validation_error,
    normalize_session_summary_heading,
    reject_handoff_at_none_depth,
)
from ..transcript_assembly import (
    TranscriptPathError,
    assemble_verbatim_md,
    compose_full_transcript,
    count_canonical_turn_headings,
    resolve_jsonl_path,
    validate_transcript_turn_grammar,
)
from ..transcript_session_id import derive_session_id_from_jsonl_start
from ..verbatim_succession import load_sealed_verbatim_for_session
from .session_close_helpers import _parse_opened_at, _raise_422


def _succession_fill_channel(body: SessionCloseRequest) -> bool:
    """True when this close fills an unfilled succession row (R2)."""
    sealed = lookup_sealed_journal(body.session_id)
    if sealed is not None and sealed.closed_by == "succession":
        return True
    if not body.transcript_jsonl_path:
        return False
    try:
        jsonl_path = resolve_jsonl_path(body.transcript_jsonl_path)
    except TranscriptPathError:
        return False
    from_jsonl = derive_session_id_from_jsonl_start(
        jsonl_path=jsonl_path, agent=body.agent
    )
    hop = resolve_successor_hop(
        supplied_session_id=body.session_id,
        jsonl_start_id=from_jsonl,
        jsonl_path=jsonl_path,
        agent=body.agent,
    )
    return (
        hop is not None
        and hop.hop_reason == SUCCESSION_FILL_REASON
        and hop.session_id == body.session_id
    )


def _guard_succession_seal_authority(body: SessionCloseRequest) -> None:
    """B-11: only ``transcript_seal`` may claim ``closed_by=succession``."""
    if body.closed_by != "succession" or body.succession_seal_authority:
        return
    from fastapi import HTTPException, status

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "reason": "succession.caller_forbidden",
            "code": "succession.caller_forbidden",
            "hint": "Machine succession closes require transcript_seal.",
        },
    )


def _guard_succession_fill_required(body: SessionCloseRequest) -> None:
    """R1b: block non-fill close when an unfilled succession row awaits fill."""
    if body.closed_by == "succession" or not body.transcript_jsonl_path:
        return
    try:
        jsonl_path = resolve_jsonl_path(body.transcript_jsonl_path)
    except TranscriptPathError:
        return
    from_jsonl = derive_session_id_from_jsonl_start(
        jsonl_path=jsonl_path, agent=body.agent
    )
    if not from_jsonl:
        return
    hop = resolve_successor_hop(
        supplied_session_id=body.session_id,
        jsonl_start_id=from_jsonl,
        jsonl_path=jsonl_path,
        agent=body.agent,
    )
    if hop is None or hop.hop_reason != SUCCESSION_FILL_REASON:
        return
    if body.session_id != hop.session_id:
        from fastapi import HTTPException, status

        conflict = build_validation_error(
            reason="session.succession_fill_required",
            field="session_id",
            received=body.session_id,
            expected=hop.session_id,
            examples=[hop.session_id],
            hint=(
                "Preflight returned hop_reason=succession_fill — close under the "
                "returned session_id with transcript_jsonl_path to fill structure."
            ),
            detail=(
                f"session {body.session_id!r} must fill succession row "
                f"{hop.session_id!r} before a new close on this uuid."
            ),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict)


@dataclass(frozen=True)
class ValidatedCloseContext:
    """Outputs of validation + transcript assembly for persist."""

    transcript_md: str | None
    turn_count: int
    heading_warning: dict | None
    transcript_entity_id: str | None
    transcript_path: str | None
    source_uri: str | None
    now: str
    opened_at: str | None
    archival_depth: str = "verbatim"
    verbatim_md: str | None = None
    verbatim_bytes: int | None = None


def _structured_422(
    body: SessionCloseRequest,
    *,
    reason: str,
    field: str,
    received: object,
    expected: str,
    examples: list[str],
    hint: str,
    detail: str,
) -> None:
    payload = build_validation_error(
        reason=reason,
        field=field,
        received=received,
        expected=expected,
        examples=examples,
        hint=hint,
        detail=detail,
    )
    _raise_422(
        reason=reason,
        session_id=body.session_id,
        agent=body.agent,
        detail=detail,
        payload=payload,
    )


def validate_session_close(body: SessionCloseRequest) -> ValidatedCloseContext:
    """Validate inputs and assemble transcript markdown when required."""
    if not _SESSION_ID_RE.match(body.session_id):
        payload = build_validation_error(
            reason="session_id.invalid",
            field="session_id",
            received=body.session_id,
            expected=_SESSION_ID_RE_SOURCE,
            examples=_SESSION_ID_EXAMPLES,
            hint=(
                "Agent slugs may contain hyphens (e.g. claude-web, "
                "api-claude) — the full slug must precede the "
                "YYYY-MM-DD-HHMMSS-{3hex} timestamp."
            ),
            detail=(
                f"session_id {body.session_id!r} does not match "
                f"pattern {_SESSION_ID_RE_SOURCE} "
                "({agent-slug}-YYYY-MM-DD-HHMMSS-{3hex}, lowercase)."
            ),
        )
        _raise_422(
            reason="session_id.invalid",
            session_id=body.session_id,
            agent=body.agent,
            detail=payload["error"],
            payload=payload,
        )

    if not _AGENT_SLUG_RE.match(body.agent):
        payload = build_validation_error(
            reason="agent.invalid",
            field="agent",
            received=body.agent,
            expected=_AGENT_SLUG_RE_SOURCE,
            examples=list(_AGENT_SLUG_EXAMPLES),
            hint=(
                "agent is a routing/metadata hint (no allowlist) — must "
                "be a lowercase slug starting with a letter (hyphens "
                "allowed)."
            ),
            detail=(
                f"agent {body.agent!r} is not a valid lowercase slug "
                f"(expected {_AGENT_SLUG_RE_SOURCE})."
            ),
        )
        _raise_422(
            reason="agent.invalid",
            session_id=body.session_id,
            agent=body.agent,
            detail=payload["error"],
            payload=payload,
        )

    _guard_succession_seal_authority(body)
    _guard_succession_fill_required(body)

    succession_fill = _succession_fill_channel(body)
    if succession_fill and body.transcript_jsonl_path:
        effective_depth = "verbatim"
    else:
        effective_depth = body.transcript_depth
    if (
        succession_fill
        and body.transcript_depth in ("light", "none")
        and body.entity_ids
    ):
        from ..dispatch_ops._session_bus_thread_disposition import parse_bus_thread_refs
        from ..events_tape import session_close_root_window_depth_upgraded

        for lane_thread in parse_bus_thread_refs(body.entity_ids):
            session_close_root_window_depth_upgraded(
                session_id=body.session_id,
                thread_id=lane_thread,
                prior_depth=body.transcript_depth,
                new_depth="verbatim",
            )
            break

    splice_fill = (
        succession_fill
        and not body.transcript_jsonl_path
        and not body.transcript_md
    )
    sealed_verbatim: str | None = None
    sealed_transcript_path: str | None = None
    if splice_fill:
        from ..dispatch_ops._shared import _FILES_ROOT

        loaded = load_sealed_verbatim_for_session(
            body.session_id, files_root=_FILES_ROOT
        )
        if loaded is None:
            _structured_422(
                body,
                reason="succession.sealed_verbatim_missing",
                field="session_id",
                received=body.session_id,
                expected="succession row with sealed verbatim on disk",
                examples=[],
                hint=(
                    "Succession fill without JSONL requires a prior "
                    "transcript_seal row with a transcript file on disk."
                ),
                detail=(
                    f"session {body.session_id!r} is not a succession row with "
                    "a readable sealed verbatim file for SPLICE fill."
                ),
            )
        sealed_verbatim, sealed_transcript_path = loaded

    if len(body.summary) < 20:
        _structured_422(
            body,
            reason="summary.too_short",
            field="summary",
            received=body.summary,
            expected="length >= 20",
            examples=[],
            hint=(
                "summary is the short synthesis used for the journal row + "
                "entity name — write at least one full sentence."
            ),
            detail=f"summary must be >= 20 characters (got {len(body.summary)})",
        )

    resolved_md, path_err = resolve_session_summary_md(
        session_summary_md=body.session_summary_md,
        session_summary_md_path=body.session_summary_md_path,
    )
    if path_err is not None:
        _structured_422(
            body,
            reason=str(path_err.get("reason") or "session_summary_md_path.invalid"),
            field=str(path_err.get("field") or "session_summary_md_path"),
            received=path_err.get("received"),
            expected=str(path_err.get("expected") or "readable summary file"),
            examples=list(path_err.get("examples") or []),
            hint=str(path_err.get("hint") or ""),
            detail=str(path_err.get("error") or path_err),
        )
    if resolved_md is not None:
        body.session_summary_md = resolved_md

    if not body.session_summary_md.strip():
        _structured_422(
            body,
            reason="session_summary.invalid",
            field="session_summary_md",
            received=body.session_summary_md,
            expected="non-empty structural-layer markdown or session_summary_md_path",
            examples=["## Session Summary\\n…\\n## Decisions\\n…"],
            hint=(
                "session_summary_md is the structural layer the agent "
                "composes — it must be non-empty. Pass session_summary_md_path "
                "to offload quote-heavy markdown (path wins if both set). "
                "A '## Session Summary' heading is added automatically if absent."
            ),
            detail="session_summary_md is required (structural layer).",
        )
    body.session_summary_md, heading_warning = normalize_session_summary_heading(
        body.session_summary_md
    )

    handoff_reject = reject_handoff_at_none_depth(
        transcript_depth=effective_depth,
        handoff_prompt=body.handoff_prompt,
        handoff_source_path=body.handoff_source_path,
    )
    if handoff_reject is not None:
        _structured_422(
            body,
            reason=handoff_reject["reason"],
            field=handoff_reject["field"],
            received=handoff_reject["received"],
            expected=handoff_reject["expected"],
            examples=list(handoff_reject.get("examples") or []),
            hint=handoff_reject["hint"],
            detail=handoff_reject.get("detail") or handoff_reject["error"],
        )

    if (
        effective_depth == "verbatim"
        and not body.transcript_jsonl_path
        and not body.transcript_md
    ):
        _structured_422(
            body,
            reason="transcript_source.missing",
            field="transcript_jsonl_path|transcript_md",
            received=None,
            expected=(
                f"exactly one of {{transcript_jsonl_path (cursor), "
                f"transcript_md (web)}} for transcript_depth="
                f"{effective_depth!r}"
            ),
            examples=[],
            hint=(
                "Cursor agents pass transcript_jsonl_path under "
                "CURSOR_AGENT_TRANSCRIPTS_ROOT; web agents pass the "
                "verbatim markdown via transcript_md. For structural-only "
                'archival or handoff pickup use transcript_depth="light" '
                '(session_summary_md is the file). Use "none" only when no '
                "handoff and no transcript entity are needed."
            ),
            detail=(
                f"either transcript_jsonl_path (cursor) or transcript_md "
                f"(web) is required for transcript_depth="
                f"{effective_depth!r} — neither was supplied"
            ),
        )

    verbatim_md: str | None = None
    verbatim_bytes: int | None = None

    if effective_depth == "none" and not splice_fill:
        transcript_md = None
        turn_count = 0
    elif effective_depth == "light" and not splice_fill:
        transcript_md = body.session_summary_md
        turn_count = 0
        verbatim_md = ""
        verbatim_bytes = 0
        if "## Session Summary" not in transcript_md:
            _structured_422(
                body,
                reason="transcript.missing_structure",
                field="session_summary_md",
                received=None,
                expected="structural layer must contain '## Session Summary'",
                examples=[],
                hint=(
                    "For transcript_depth=light, the structural layer is "
                    "the entire transcript file — it must contain a "
                    "'## Session Summary' heading."
                ),
                detail="session_summary_md missing '## Session Summary' heading",
            )
    elif splice_fill:
        assert sealed_verbatim is not None
        verbatim_md = sealed_verbatim
        verbatim_bytes = len(sealed_verbatim.encode("utf-8"))
        transcript_md = compose_full_transcript(
            sealed_verbatim, body.session_summary_md
        )
        turn_count = count_canonical_turn_headings(sealed_verbatim)
        if len(transcript_md) < 200:
            _structured_422(
                body,
                reason="transcript.missing_structure",
                field="session_summary_md",
                received=len(transcript_md),
                expected="spliced transcript length >= 200",
                examples=[],
                hint=(
                    "Succession SPLICE fill keeps the sealed verbatim prefix "
                    "and replaces the structural layer — add substance to "
                    "session_summary_md."
                ),
                detail=(
                    f"spliced transcript is {len(transcript_md)} chars (< 200)."
                ),
            )
    else:
        if body.transcript_jsonl_path:
            try:
                resolved_path = resolve_jsonl_path(body.transcript_jsonl_path)
            except TranscriptPathError as exc:
                _structured_422(
                    body,
                    reason="transcript_jsonl.invalid",
                    field="transcript_jsonl_path",
                    received=body.transcript_jsonl_path,
                    expected=(
                        "absolute or relative path under CURSOR_AGENT_TRANSCRIPTS_ROOT"
                    ),
                    examples=[],
                    hint=(
                        "Pass the active session's JSONL path under the cursor "
                        "agent-transcripts root; the server resolves + sandboxes it."
                    ),
                    detail=str(exc),
                )

            try:
                verbatim_md, turn_count = assemble_verbatim_md(
                    jsonl_path=resolved_path,
                    session_id=body.session_id,
                    assistant_label=body.assistant_label,
                )
            except ValueError as exc:
                _structured_422(
                    body,
                    reason="transcript_jsonl.invalid",
                    field="transcript_jsonl_path",
                    received=body.transcript_jsonl_path,
                    expected="well-formed JSONL parseable by assemble_verbatim_md",
                    examples=[],
                    hint=(
                        "Confirm the JSONL is the cursor agent-transcripts "
                        "format (one record per line, user/assistant roles)."
                    ),
                    detail=f"JSONL parse error: {exc}",
                )
            grammar_err = validate_transcript_turn_grammar(verbatim_md)
            if grammar_err is not None:
                _structured_422(
                    body,
                    reason=grammar_err.reason,
                    field="transcript_jsonl_path",
                    received=body.transcript_jsonl_path,
                    expected="turn headings matching assembly grammar ## Turn N — topic",
                    examples=["## Turn 1 — first user message topic"],
                    hint=(
                        "Turn headings must match JSONL assembly output: "
                        "'## Turn {N} — {topic}' sequential from 1."
                    ),
                    detail=grammar_err.detail,
                )
        else:
            assert body.transcript_md is not None
            verbatim_md = body.transcript_md
            grammar_err = validate_transcript_turn_grammar(verbatim_md)
            if grammar_err is not None:
                _structured_422(
                    body,
                    reason=grammar_err.reason,
                    field="transcript_md",
                    received=verbatim_md[:120],
                    expected="each ## Turn line matches '^## Turn (\\\\d+) — .+' sequential 1..N",
                    examples=["## Turn 1 — topic from first user message"],
                    hint=(
                        "Web verbatim must use the same turn grammar as JSONL "
                        "assembly (## Turn N — topic). Malformed headings are "
                        "rejected at close — write-time only; existing archives "
                        "are not retro-scanned."
                    ),
                    detail=grammar_err.detail,
                )
            turn_count = count_canonical_turn_headings(verbatim_md)

        verbatim_bytes = len(verbatim_md.encode("utf-8"))
        transcript_md = compose_full_transcript(verbatim_md, body.session_summary_md)

        if len(transcript_md) < 200:
            _structured_422(
                body,
                reason="transcript.missing_structure",
                field="transcript_md|transcript_jsonl_path",
                received=len(transcript_md),
                expected="composed transcript length >= 200",
                examples=[],
                hint=(
                    "Either JSONL is empty or session_summary_md is too thin; "
                    "check the JSONL path and re-run."
                ),
                detail=(
                    f"composed transcript is {len(transcript_md)} chars "
                    "(< 200) — JSONL may be empty or session_summary_md too thin."
                ),
            )
        if "## Turn" not in transcript_md and "## Session Summary" not in transcript_md:
            _structured_422(
                body,
                reason="transcript.missing_structure",
                field="transcript_md|session_summary_md",
                received=None,
                expected=(
                    "composed transcript contains '## Turn' or '## Session Summary'"
                ),
                examples=[],
                hint=(
                    "JSONL produced no turn blocks and structural layer lacks "
                    "'## Session Summary' — verify both sources."
                ),
                detail=(
                    "composed transcript missing structural headings — assembly "
                    "did not produce '## Turn' blocks and structural layer lacks "
                    "'## Session Summary'."
                ),
            )
        if len(_USER_VOICE_RE.findall(transcript_md)) == 0:
            _structured_422(
                body,
                reason="transcript.hollow",
                field="transcript_jsonl_path|transcript_md",
                received=None,
                expected="composed transcript contains >=1 User-voice block",
                examples=[],
                hint=(
                    "JSONL contained no user messages — likely pointing at a "
                    "continuation-with-no-prompt or tool-only record set."
                ),
                detail=(
                    "composed transcript has zero User-voice blocks. The "
                    "supplied JSONL contained no user messages (or only "
                    "tool_result records). Confirm transcript_jsonl_path "
                    "points at the active session, not a continuation-with-"
                    "no-prompt or a tool-only record set."
                ),
            )

    keep_transcript_artifact = effective_depth != "none" or succession_fill
    transcript_entity_id: str | None = (
        f"transcript:{body.session_id}" if keep_transcript_artifact else None
    )
    if succession_fill and sealed_transcript_path:
        transcript_path: str | None = sealed_transcript_path
    elif effective_depth == "none":
        transcript_path = None
    else:
        transcript_path = f"notes/system/transcripts/{body.session_id}.md"
    source_uri = f"files://{transcript_path}" if transcript_path else None
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    opened_at = _parse_opened_at(body.session_id)

    # B — early-fail anchor gate: check raw handoff_prompt/handoff_source_path
    # at validation time so both session_id and anchor errors surface together.
    # The persist-time gate (persist_session_close.py) is retained as defense-
    # in-depth: it checks the *resolved* prompt that may differ from the raw input.
    if body.handoff_prompt or body.handoff_source_path:
        enforce_handoff_transcript_anchor(
            session_id=body.session_id,
            agent=body.agent,
            handoff_prompt=body.handoff_prompt,
            handoff_source_path=body.handoff_source_path,
        )

    return ValidatedCloseContext(
        transcript_md=transcript_md,
        turn_count=turn_count,
        heading_warning=heading_warning,
        transcript_entity_id=transcript_entity_id,
        transcript_path=transcript_path,
        source_uri=source_uri,
        now=now,
        opened_at=opened_at,
        archival_depth=effective_depth,
        verbatim_md=verbatim_md,
        verbatim_bytes=verbatim_bytes,
    )


def enforce_handoff_transcript_anchor(
    *,
    session_id: str,
    agent: str,
    handoff_prompt: str | None,
    handoff_source_path: str | None,
) -> None:
    """Reject (422) when a persisted handoff omits the closing-session anchor.

    Promotes the former warn-only ``handoff_missing_transcript_anchor`` finding
    to a pre-commit gate (root cause 3, session-close-handoff-pickup-refine).
    Runs on the *resolved* handoff prompt so both detached-string and
    file-marker derivations are covered; the underlying check short-circuits
    when ``handoff_source_path`` already names the session.
    """
    gap = check_handoff_transcript_anchor(
        session_id=session_id,
        handoff_prompt=handoff_prompt,
        handoff_source_path=handoff_source_path,
    )
    if gap is None:
        return
    entity_ref = f"transcript:{session_id}"
    file_ref = f"notes/system/transcripts/{session_id}.md"
    payload = build_validation_error(
        reason="handoff.missing_transcript_anchor",
        field="handoff_prompt",
        received="handoff_prompt without closing-session anchor",
        expected=f"handoff_prompt contains {entity_ref!r} or {file_ref!r}",
        examples=[
            f"**Closing session:** {entity_ref}\n"
            f"**Load context:** fs(cortex, op=read, path={file_ref})"
        ],
        hint=(
            "A handoff must tell the next session how to load THIS closing "
            "transcript. Prepend the anchor block (Closing session + Load "
            "context lines) to handoff_prompt and re-call session_close."
        ),
        detail=gap["detail"],
    )
    _raise_422(
        reason="handoff.missing_transcript_anchor",
        session_id=session_id,
        agent=agent,
        detail=gap["detail"],
        payload=payload,
    )
