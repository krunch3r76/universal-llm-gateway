"""Session-close request validation and transcript assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from continuity_tape.extract_jsonl import extract_turns_from_jsonl
from continuity_tape.messages import ContinuityMessagesEnvelope
from continuity_tape.render_md import render_verbatim_md

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
    conversation_uuid_from_jsonl_path,
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
    compose_full_transcript,
    count_canonical_turn_headings,
    resolve_jsonl_path,
    validate_transcript_turn_grammar,
)
from ..transcript_session_id import derive_session_id_from_jsonl_start
from ..verbatim_succession import (
    divergence_index,
    load_sealed_payload_for_session,
    load_sealed_verbatim_for_session,
    prefix_holds,
    transcript_messages_path,
)
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

    composed_md: str | None
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
    envelope: ContinuityMessagesEnvelope | None = None
    messages_path: str | None = None
    verbatim_codec: str | None = None
    messages_sha256: str | None = None
    splice_cause: str | None = None
    diverged_live_turns: int | None = None
    diverged_first_index: int | None = None
    diverged_sealed_turns: int | None = None
    diverged_codec: str | None = None


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

    diverged_jsonl_splice = False
    splice_cause: str | None = None
    diverged_live_turns: int | None = None
    diverged_first_index: int | None = None
    diverged_sealed_turns: int | None = None
    diverged_codec: str | None = None
    fill_check_envelope: ContinuityMessagesEnvelope | None = None
    fill_check_verbatim_md: str | None = None
    fill_check_turn_count: int | None = None

    if succession_fill and body.transcript_jsonl_path:
        from ..dispatch_ops._shared import _FILES_ROOT

        try:
            _fill_resolved = resolve_jsonl_path(body.transcript_jsonl_path)
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
            fill_check_envelope = extract_turns_from_jsonl(
                _fill_resolved,
                tools="marker",
                session_id=body.session_id,
            )
            fill_check_verbatim_md, fill_check_turn_count = render_verbatim_md(
                fill_check_envelope,
                body.session_id,
                body.assistant_label,
            )
        except ValueError as exc:
            _structured_422(
                body,
                reason="transcript_jsonl.invalid",
                field="transcript_jsonl_path",
                received=body.transcript_jsonl_path,
                expected="well-formed JSONL parseable by extract_turns_from_jsonl",
                examples=[],
                hint=(
                    "Confirm the JSONL is the cursor agent-transcripts "
                    "format (one record per line, user/assistant roles)."
                ),
                detail=f"JSONL parse error: {exc}",
            )
        sealed_payload = load_sealed_payload_for_session(
            body.session_id, files_root=_FILES_ROOT
        )
        if sealed_payload is not None:
            if sealed_payload.codec == "messages-v1":
                ok = prefix_holds(
                    "messages-v1",
                    sealed_payload.messages or [],
                    fill_check_envelope.messages,
                )
            else:
                ok = prefix_holds(
                    "md-v1",
                    sealed_payload.verbatim_md,
                    fill_check_verbatim_md or "",
                )
            if not ok:
                path_uuid = conversation_uuid_from_jsonl_path(_fill_resolved)
                if (
                    sealed_payload.conversation_uuid
                    and path_uuid != sealed_payload.conversation_uuid
                ):
                    from fastapi import HTTPException, status

                    conflict = build_validation_error(
                        reason="succession.conversation_uuid_mismatch",
                        field="transcript_jsonl_path",
                        received=path_uuid,
                        expected=sealed_payload.conversation_uuid,
                        examples=[],
                        hint=(
                            "Succession fill requires JSONL from the same "
                            "conversation_uuid as the sealed row — check "
                            "transcript_jsonl_path."
                        ),
                        detail=(
                            f"session {body.session_id!r} succession fill refused: "
                            f"JSONL uuid {path_uuid!r} != sealed "
                            f"{sealed_payload.conversation_uuid!r}."
                        ),
                    )
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT, detail=conflict
                    )
                diverged_jsonl_splice = True
                splice_cause = "diverged"
                diverged_live_turns = fill_check_turn_count
                diverged_sealed_turns = sealed_payload.sealed_turns
                diverged_codec = sealed_payload.codec
                if sealed_payload.codec == "messages-v1":
                    diverged_first_index = divergence_index(
                        "messages-v1",
                        sealed_payload.messages or [],
                        fill_check_envelope.messages,
                    )
                else:
                    diverged_first_index = divergence_index(
                        "md-v1",
                        sealed_payload.verbatim_md,
                        fill_check_verbatim_md or "",
                    )
            else:
                splice_cause = "prefix_extend"

    splice_fill = (
        (
            succession_fill
            and not body.transcript_jsonl_path
            and not body.transcript_messages
            and not body.transcript_messages_path
        )
        or diverged_jsonl_splice
    )
    sealed_verbatim: str | None = None
    sealed_transcript_path: str | None = None
    if splice_fill:
        from ..dispatch_ops._shared import _FILES_ROOT

        if diverged_jsonl_splice:
            sealed_payload = load_sealed_payload_for_session(
                body.session_id, files_root=_FILES_ROOT
            )
            if sealed_payload is None:
                _structured_422(
                    body,
                    reason="succession.sealed_verbatim_missing",
                    field="session_id",
                    received=body.session_id,
                    expected="succession row with sealed verbatim on disk",
                    examples=[],
                    hint=(
                        "Succession SPLICE on divergence requires a prior "
                        "transcript_seal row with a readable sealed verbatim."
                    ),
                    detail=(
                        f"session {body.session_id!r} is not a succession row with "
                        "a readable sealed verbatim file for diverged SPLICE fill."
                    ),
                )
            sealed_verbatim = sealed_payload.verbatim_md
            sealed_transcript_path = sealed_payload.rel_path
        else:
            splice_cause = "no_jsonl"
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
        and not body.transcript_messages
        and not body.transcript_messages_path
    ):
        _structured_422(
            body,
            reason="transcript_source.missing",
            field="transcript_jsonl_path|transcript_messages",
            received=None,
            expected=(
                f"exactly one of {{transcript_jsonl_path (cursor), "
                f"transcript_messages* (web)}} for transcript_depth="
                f"{effective_depth!r}"
            ),
            examples=[],
            hint=(
                "Cursor agents pass transcript_jsonl_path under "
                "CURSOR_AGENT_TRANSCRIPTS_ROOT; web agents pass "
                "transcript_messages or transcript_messages_path. For "
                'structural-only archival use transcript_depth="light".'
            ),
            detail=(
                f"transcript source required for transcript_depth="
                f"{effective_depth!r} — none supplied"
            ),
        )

    verbatim_md: str | None = None
    verbatim_bytes: int | None = None
    envelope: ContinuityMessagesEnvelope | None = None
    messages_path: str | None = None
    verbatim_codec: str | None = None
    messages_sha256: str | None = None

    if effective_depth == "none" and not splice_fill:
        composed_md = None
        turn_count = 0
    elif effective_depth == "light" and not splice_fill:
        composed_md = body.session_summary_md
        turn_count = 0
        verbatim_md = ""
        verbatim_bytes = 0
        if "## Session Summary" not in composed_md:
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
        envelope = None
        messages_path = None
        verbatim_codec = None
        messages_sha256 = None
        composed_md = compose_full_transcript(
            sealed_verbatim, body.session_summary_md
        )
        turn_count = count_canonical_turn_headings(sealed_verbatim)
        if len(composed_md) < 200:
            _structured_422(
                body,
                reason="transcript.missing_structure",
                field="session_summary_md",
                received=len(composed_md),
                expected="spliced transcript length >= 200",
                examples=[],
                hint=(
                    "Succession SPLICE fill keeps the sealed verbatim prefix "
                    "and replaces the structural layer — add substance to "
                    "session_summary_md."
                ),
                detail=(
                    f"spliced transcript is {len(composed_md)} chars (< 200)."
                ),
            )
    else:
        if body.transcript_jsonl_path:
            if fill_check_envelope is not None:
                envelope = fill_check_envelope
                verbatim_md = fill_check_verbatim_md
                turn_count = fill_check_turn_count or 0
            else:
                try:
                    resolved_path = resolve_jsonl_path(body.transcript_jsonl_path)
                except TranscriptPathError as exc:
                    _structured_422(
                        body,
                        reason="transcript_jsonl.invalid",
                        field="transcript_jsonl_path",
                        received=body.transcript_jsonl_path,
                        expected=(
                            "absolute or relative path under "
                            "CURSOR_AGENT_TRANSCRIPTS_ROOT"
                        ),
                        examples=[],
                        hint=(
                            "Pass the active session's JSONL path under the cursor "
                            "agent-transcripts root; the server resolves + sandboxes it."
                        ),
                        detail=str(exc),
                    )

                try:
                    envelope = extract_turns_from_jsonl(
                        resolved_path,
                        tools="marker",
                        session_id=body.session_id,
                    )
                    verbatim_md, turn_count = render_verbatim_md(
                        envelope,
                        body.session_id,
                        body.assistant_label,
                    )
                except ValueError as exc:
                    _structured_422(
                        body,
                        reason="transcript_jsonl.invalid",
                        field="transcript_jsonl_path",
                        received=body.transcript_jsonl_path,
                        expected="well-formed JSONL parseable by extract_turns_from_jsonl",
                        examples=[],
                        hint=(
                            "Confirm the JSONL is the cursor agent-transcripts "
                            "format (one record per line, user/assistant roles)."
                        ),
                        detail=f"JSONL parse error: {exc}",
                    )
            verbatim_codec = "messages-v1"
            messages_path = transcript_messages_path(
                f"notes/system/transcripts/{body.session_id}.md"
            )
            messages_sha256 = envelope.meta.messages_sha256
        else:
            envelope = _resolve_transcript_messages_envelope(body)
            _validate_web_envelope(body, envelope)
            verbatim_md, turn_count = render_verbatim_md(
                envelope,
                body.session_id,
                body.assistant_label,
            )
            verbatim_codec = "messages-v1"
            messages_path = transcript_messages_path(
                f"notes/system/transcripts/{body.session_id}.md"
            )
            messages_sha256 = envelope.meta.messages_sha256

        grammar_err = validate_transcript_turn_grammar(verbatim_md)
        if grammar_err is not None:
            _structured_422(
                body,
                reason=grammar_err.reason,
                field="transcript_jsonl_path|transcript_messages",
                received=body.transcript_jsonl_path or "envelope",
                expected="turn headings matching assembly grammar ## Turn N — topic",
                examples=["## Turn 1 — first user message topic"],
                hint=(
                    "Turn headings must match render output: "
                    "'## Turn {N} — {topic}' sequential from 1."
                ),
                detail=grammar_err.detail,
            )

        verbatim_bytes = len(verbatim_md.encode("utf-8"))
        composed_md = compose_full_transcript(verbatim_md, body.session_summary_md)

        if len(composed_md) < 200:
            _structured_422(
                body,
                reason="transcript.missing_structure",
                field="transcript_jsonl_path|transcript_messages",
                received=len(composed_md),
                expected="composed transcript length >= 200",
                examples=[],
                hint=(
                    "Either JSONL/envelope is empty or session_summary_md is too thin."
                ),
                detail=(
                    f"composed transcript is {len(composed_md)} chars (< 200)."
                ),
            )
        if "## Turn" not in composed_md and "## Session Summary" not in composed_md:
            _structured_422(
                body,
                reason="transcript.missing_structure",
                field="transcript_jsonl_path|session_summary_md",
                received=None,
                expected=(
                    "composed transcript contains '## Turn' or '## Session Summary'"
                ),
                examples=[],
                hint="Verify transcript source and session_summary_md.",
                detail="composed transcript missing structural headings.",
            )
        if len(_USER_VOICE_RE.findall(composed_md)) == 0:
            _structured_422(
                body,
                reason="transcript.hollow",
                field="transcript_jsonl_path|transcript_messages",
                received=None,
                expected="composed transcript contains >=1 User-voice block",
                examples=[],
                hint="Transcript source contained no user messages.",
                detail="composed transcript has zero User-voice blocks.",
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
        composed_md=composed_md,
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
        envelope=envelope,
        messages_path=messages_path,
        verbatim_codec=verbatim_codec,
        messages_sha256=messages_sha256,
        splice_cause=splice_cause,
        diverged_live_turns=diverged_live_turns,
        diverged_first_index=diverged_first_index,
        diverged_sealed_turns=diverged_sealed_turns,
        diverged_codec=diverged_codec,
    )


def _resolve_transcript_messages_envelope(
    body: SessionCloseRequest,
) -> ContinuityMessagesEnvelope:
    from ..dispatch_ops._shared import _FILES_ROOT

    if body.transcript_messages is not None:
        return ContinuityMessagesEnvelope.model_validate(body.transcript_messages)
    assert body.transcript_messages_path is not None
    rel = body.transcript_messages_path.lstrip("/")
    if not (
        rel.startswith("ephemeral/harvests/")
        or rel.startswith("notes/system/transcripts/")
    ):
        _structured_422(
            body,
            reason="transcript_messages_path.invalid",
            field="transcript_messages_path",
            received=body.transcript_messages_path,
            expected="path under ephemeral/harvests/ or notes/system/transcripts/",
            examples=["ephemeral/harvests/web-anthropic.json"],
            hint="Web harvest paths must be gated under allowed roots.",
            detail=f"transcript_messages_path {rel!r} is outside allowed roots.",
        )
    path = _FILES_ROOT / rel
    if not path.is_file():
        _structured_422(
            body,
            reason="transcript_messages_path.invalid",
            field="transcript_messages_path",
            received=body.transcript_messages_path,
            expected="readable envelope JSON file",
            examples=[],
            hint="Confirm the harvest envelope file exists on disk.",
            detail=f"transcript_messages_path not found: {rel}",
        )
    import json

    return ContinuityMessagesEnvelope.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _validate_web_envelope(
    body: SessionCloseRequest,
    envelope: ContinuityMessagesEnvelope,
) -> None:
    user_with_content = [
        m
        for m in envelope.messages
        if m.get("role") == "user" and m.get("content") not in (None, "")
    ]
    if not user_with_content:
        _structured_422(
            body,
            reason="transcript_messages.invalid",
            field="transcript_messages",
            received=len(envelope.messages),
            expected=">= 1 user message with non-null content",
            examples=[],
            hint="Web close requires at least one user turn in the envelope.",
            detail="transcript_messages envelope has no user content.",
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
