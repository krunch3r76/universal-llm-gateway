"""Session-close, preflight, and handoff-upsert ops."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from universal_logging import get_logger

from ..routes.session_handoff import _upsert_session_handoff_impl
from ..routes.session_journals import _close_session_impl
from ..session_close_validation import (
    _check_transcript_hollow_guards,
    _validate_session_close_args,
    _validate_transcript_structure,
    normalize_session_summary_heading,
)
from ..session_handoff import handoff_dry_run_preview
from ..transcript_assembly import (
    TranscriptPathError,
    assemble_verbatim_md,
    compose_full_transcript,
    derive_prior_session_id_from_jsonl_path,
    derive_session_id_from_jsonl_start,
    resolve_jsonl_path,
    session_id_timing_hint,
)
from ._session_close_doc_type import check_session_close_validate_attestation
from ._session_objective_promote import promote_session_objectives
from ._session_summary_path import resolve_session_summary_md
from ._session_todo_reconciliation import todo_reconciliation_preflight_fields
from ._shared import _FILES_ROOT, record
from .ops_audit_detectors import run_detectors
from .ops_review_gate import _run_session_audit_or_block, summarize_audit_outcome

logger = get_logger(__name__)


def _append_session_close_warnings(
    result: dict[str, Any],
    *,
    session_id: str,
    agent: str,
) -> None:
    """Attach post-close warning findings for session continuity provenance gaps.

    The handoff transcript-anchor gap is no longer surfaced here — it is a hard
    pre-write 422 (``handoff.missing_transcript_anchor``) enforced in
    ``persist_session_close``. This path keeps only the ``prior_session_id``
    continuity detector.
    """
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
        record(
            "cortex.session.audit.degraded",
            session_id=session_id,
            agent=agent,
            error=type(exc).__name__,
            message=str(exc),
        )
        return {
            "warning": {
                "audit_degraded": True,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        }


def _assemble_transcript_in_memory(
    *,
    session_id: str,
    agent: str,
    transcript_jsonl_path: str | None,
    transcript_md: str | None,
    transcript_depth: str,
    session_summary_md: str,
    assistant_label: str | None = None,
) -> dict[str, Any]:
    """Assemble + compose the transcript in-memory; return info dict or error dict.

    Returns ``{"ok": True, "composed": str, "turn_count": int,
    "jsonl_resolved": Path|None}`` on success, or
    ``{"ok": False, "error": str, "reason": str}`` on failure.

    Shared between preflight and dry_run to eliminate duplicated code.
    """
    if transcript_depth == "none":
        return {
            "ok": True,
            "composed": session_summary_md,
            "turn_count": 0,
            "jsonl_resolved": None,
        }

    jsonl_resolved = None
    if transcript_jsonl_path:
        try:
            resolved = resolve_jsonl_path(transcript_jsonl_path)
            jsonl_resolved = resolved
            verbatim_md, turn_count = assemble_verbatim_md(
                jsonl_path=resolved,
                session_id=session_id,
                assistant_label=assistant_label,
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
    elif transcript_depth == "light":
        verbatim_md = ""
        turn_count = 0
    else:
        assert transcript_md is not None
        verbatim_md = transcript_md
        turn_count = sum(
            1 for line in verbatim_md.splitlines() if line.startswith("## Turn")
        )

    composed = (
        session_summary_md
        if transcript_depth == "light"
        else compose_full_transcript(verbatim_md, session_summary_md)
    )

    guard_error = _check_transcript_hollow_guards(
        composed, transcript_depth=transcript_depth
    )
    if guard_error is not None:
        return {"ok": False, **guard_error}

    return {
        "ok": True,
        "composed": composed,
        "turn_count": turn_count,
        "jsonl_resolved": jsonl_resolved,
    }


def _op_session_close_preflight(
    session_id: str | None = None,
    agent: str | None = None,
    transcript_jsonl_path: str | None = None,
    transcript_md: str | None = None,
    session_summary_md: str | None = None,
    session_summary_md_path: str | None = None,
    summary: str | None = None,
    transcript_depth: str = "verbatim",
    handoff_prompt: str | None = None,
    handoff_source_path: str | None = None,
    entity_ids: list[str] | None = None,
    defer_gaps: dict[str, str] | None = None,
    assistant_label: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Validate args + path sandbox + audit-gate health WITHOUT writing.

    Returns ``{"ok": True, "audit": {...}, "turn_count": int}`` on a path
    that would succeed at close time, or ``{"ok": False, "error", "reason"}``
    otherwise.  Verbatim assembly is performed in-memory (no file
    written, no DB row) so the agent learns about a bad JSONL before
    paying for the audit and DB tx.

    ``transcript_depth`` (default ``"verbatim"``) selects the archival
    depth — ``none`` skips assembly entirely; ``light`` derives the
    composed file from ``session_summary_md`` alone.

    ``session_summary_md_path`` (optional) loads the structural summary from
    a CORTEX_FILES_ROOT-relative file. When both path and inline
    ``session_summary_md`` are set, **path wins**.
    """
    session_summary_md, path_err = resolve_session_summary_md(
        session_summary_md=session_summary_md,
        session_summary_md_path=session_summary_md_path,
    )
    if path_err is not None:
        return {"ok": False, **path_err}

    arg_error = _validate_session_close_args(
        session_id=session_id,
        agent=agent,
        transcript_jsonl_path=transcript_jsonl_path,
        transcript_md=transcript_md,
        session_summary_md=session_summary_md,
        summary=summary,
        transcript_depth=transcript_depth,
        handoff_prompt=handoff_prompt,
        handoff_source_path=handoff_source_path,
        emit_rejected=False,
    )
    if arg_error is not None:
        return {"ok": False, **arg_error}
    assert session_id and agent and session_summary_md and summary

    # Mirror the close-path heading normalization so the preview reflects what
    # would actually be written (idempotent when the literal heading is present).
    session_summary_md, _ = normalize_session_summary_heading(session_summary_md)

    asm = _assemble_transcript_in_memory(
        session_id=session_id,
        agent=agent,
        transcript_jsonl_path=transcript_jsonl_path,
        transcript_md=transcript_md,
        transcript_depth=transcript_depth,
        session_summary_md=session_summary_md,
        assistant_label=assistant_label,
    )
    if not asm["ok"]:
        return {"ok": False, **{k: v for k, v in asm.items() if k != "ok"}}

    composed: str = asm["composed"]
    turn_count: int = asm["turn_count"]
    jsonl_resolved = asm["jsonl_resolved"]

    audit_outcome = _safe_run_audit(
        session_id=session_id,
        agent=agent,
        entity_ids=entity_ids or [],
        defer_gaps=defer_gaps,
    )
    if audit_outcome.get("blocked"):
        return {"ok": False, "reason": "session_audit_blocked", **audit_outcome}

    structural_warnings = _validate_transcript_structure(
        composed, summary_len=len(summary), transcript_depth=transcript_depth
    )
    preflight: dict[str, Any] = {
        "ok": True,
        "audit": summarize_audit_outcome(audit_outcome),
        "turn_count": turn_count,
        "byte_count": (
            len(composed.encode("utf-8")) if transcript_depth != "none" else 0
        ),
        "warnings": structural_warnings,
        "transcript_depth": transcript_depth,
    }
    if jsonl_resolved is not None:
        from_jsonl = derive_session_id_from_jsonl_start(
            jsonl_path=jsonl_resolved, agent=agent
        )
        if from_jsonl is not None:
            preflight["session_id_from_jsonl_start"] = from_jsonl
            # Boot-held / caller session_id is authoritative (friction 23205).
            # Expose JSONL-start for the no-boot copy-paste path; do not
            # overwrite the supplied ID or fail validate on a time-base delta.
        else:
            # No <timestamp> tag and no st_birthtime — keep boot-held
            # session_id. Never mint from st_mtime (Linux append race).
            preflight["jsonl_start_reason"] = "missing_jsonl_start_timestamp"
        prior_suggestion = derive_prior_session_id_from_jsonl_path(
            jsonl_path=jsonl_resolved, agent=agent
        )
        if prior_suggestion:
            preflight["prior_session_id_suggestion"] = prior_suggestion
        timing_note = session_id_timing_hint(
            session_id=session_id,
            jsonl_path=jsonl_resolved,
            agent=agent,
        )
        if timing_note:
            preflight["session_id_timing_note"] = timing_note
    todo_recon = todo_reconciliation_preflight_fields(entity_ids)
    preflight.update(todo_recon)
    if todo_recon.get("todo_reconciliation_warning"):
        preflight["warnings"] = [
            *(preflight.get("warnings") or []),
            todo_recon["todo_reconciliation_warning"],
        ]
    if handoff_source_path or handoff_prompt:
        handoff_preview = handoff_dry_run_preview(
            files_root=_FILES_ROOT,
            handoff_source_path=handoff_source_path,
            handoff_source_section=None,
            handoff_prompt=handoff_prompt,
            session_id=session_id,
        )
        preflight.update(handoff_preview)
        if not handoff_preview.get("handoff_valid", True):
            preflight["ok"] = False
            preflight["reason"] = handoff_preview.get("reason") or "handoff.invalid"
    return preflight


def _op_session_close(
    session_id: str | None = None,
    agent: str | None = None,
    transcript_jsonl_path: str | None = None,
    transcript_md: str | None = None,
    session_summary_md: str | None = None,
    session_summary_md_path: str | None = None,
    summary: str | None = None,
    transcript_depth: str = "verbatim",
    domains: list[str] | None = None,
    decisions: list[str] | None = None,
    open_items: list[str] | None = None,
    entity_ids: list[str] | None = None,
    prior_session_id: str | None = None,
    handoff_prompt: str | None = None,
    handoff_source_path: str | None = None,
    handoff_source_section: str | None = None,
    expected_handoff_prompt: str | None = None,
    expected_derived_handoff_prompt_sha256: str | None = None,
    expected_source_file_sha256: str | None = None,
    assistant_label: str | None = None,
    source_ref: str | None = None,
    source_ref_derivation: str | None = None,
    defer_gaps: dict[str, str] | None = None,
    promote_todos: list[dict[str, Any]] | None = None,
    validate_attestation: list[str] | None = None,
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

    ``transcript_depth`` (default ``"verbatim"``) selects the archival
    layer — ``light`` writes a structural-only file with the transcript
    entity flagged as non-enrichment-eligible; ``none`` writes no file
    and no transcript entity, only the journal row (plus the continues
    edge). ``handoff_prompt`` / ``handoff_source_path`` at ``none`` return
    422 ``handoff.requires_transcript_entity`` — use ``light`` minimum.
    Continuity is preserved at all depths.

    ``session_summary_md_path`` (optional) loads the structural summary from
    a CORTEX_FILES_ROOT-relative file. When both path and inline
    ``session_summary_md`` are set, **path wins**.

    See session-close-server-side-transcript Phase 2 for the architecture
    rewrite; the route handler in `routes/session_journals.py` is the
    single atomic boundary.
    """
    session_summary_md, path_err = resolve_session_summary_md(
        session_summary_md=session_summary_md,
        session_summary_md_path=session_summary_md_path,
    )
    if path_err is not None:
        if dry_run:
            return {"dry_run": True, "would_fail": True, **path_err}
        return path_err

    arg_error = _validate_session_close_args(
        session_id=session_id,
        agent=agent,
        transcript_jsonl_path=transcript_jsonl_path,
        transcript_md=transcript_md,
        session_summary_md=session_summary_md,
        summary=summary,
        transcript_depth=transcript_depth,
        handoff_prompt=handoff_prompt,
        handoff_source_path=handoff_source_path,
        emit_rejected=not dry_run,
        skip_handoff_depth_check=dry_run,
    )
    if arg_error is not None:
        if dry_run:
            return {"dry_run": True, "would_fail": True, **arg_error}
        return arg_error
    assert session_id and agent and session_summary_md and summary

    # Heading normalization (idempotent) — applied before dry_run preview and
    # before handing the body to the route handler, which normalizes again.
    session_summary_md, _ = normalize_session_summary_heading(session_summary_md)

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
        asm = _assemble_transcript_in_memory(
            session_id=session_id,
            agent=agent,
            transcript_jsonl_path=transcript_jsonl_path,
            transcript_md=transcript_md,
            transcript_depth=transcript_depth,
            session_summary_md=session_summary_md,
            assistant_label=assistant_label,
        )
        if not asm["ok"]:
            return {
                "dry_run": True,
                "would_fail": True,
                **{k: v for k, v in asm.items() if k != "ok"},
            }
        composed: str = asm["composed"]
        turn_count: int = asm["turn_count"]
        structural_warnings = _validate_transcript_structure(
            composed, summary_len=len(summary), transcript_depth=transcript_depth
        )
        dry_payload: dict[str, Any] = {
            "dry_run": True,
            "would_succeed": True,
            "warnings": structural_warnings,
            "audit": summarize_audit_outcome(audit_outcome),
            "turn_count": turn_count,
            "byte_count": (
                len(composed.encode("utf-8")) if transcript_depth != "none" else 0
            ),
            "transcript_depth": transcript_depth,
        }
        if (
            handoff_source_path
            or handoff_prompt
            or handoff_source_section
            or expected_handoff_prompt
            or expected_derived_handoff_prompt_sha256
            or expected_source_file_sha256
        ):
            dry_payload.update(
                handoff_dry_run_preview(
                    files_root=_FILES_ROOT,
                    handoff_source_path=handoff_source_path,
                    handoff_source_section=handoff_source_section,
                    handoff_prompt=handoff_prompt,
                    expected_handoff_prompt=expected_handoff_prompt,
                    expected_derived_handoff_prompt_sha256=(
                        expected_derived_handoff_prompt_sha256
                    ),
                    expected_source_file_sha256=expected_source_file_sha256,
                    session_id=session_id,
                )
            )
            if not dry_payload.get("handoff_valid", True):
                dry_payload.pop("would_succeed", None)
                dry_payload["would_fail"] = True
                dry_payload["reason"] = dry_payload.get("reason") or "handoff.invalid"
        todo_recon = todo_reconciliation_preflight_fields(entity_ids)
        dry_payload.update(todo_recon)
        if todo_recon.get("todo_reconciliation_warning"):
            dry_payload["warnings"] = [
                *(dry_payload.get("warnings") or []),
                todo_recon["todo_reconciliation_warning"],
            ]
        return dry_payload

    attestation_error = check_session_close_validate_attestation(
        session_id=session_id,
        validate_attestation=validate_attestation,
    )
    if attestation_error is not None:
        return attestation_error

    body: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "session_summary_md": session_summary_md,
        "summary": summary,
        "transcript_depth": transcript_depth,
    }
    for key, val in [
        ("transcript_jsonl_path", transcript_jsonl_path),
        ("transcript_md", transcript_md),
        ("domains", domains),
        ("decisions", decisions),
        ("open_items", open_items),
        ("entity_ids", entity_ids),
        ("prior_session_id", prior_session_id),
        ("handoff_prompt", handoff_prompt),
        ("handoff_source_path", handoff_source_path),
        ("handoff_source_section", handoff_source_section),
        ("expected_handoff_prompt", expected_handoff_prompt),
        (
            "expected_derived_handoff_prompt_sha256",
            expected_derived_handoff_prompt_sha256,
        ),
        ("expected_source_file_sha256", expected_source_file_sha256),
        ("assistant_label", assistant_label),
        ("source_ref", source_ref),
        ("source_ref_derivation", source_ref_derivation),
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
        result["_warning"] = summarize_audit_outcome(audit_outcome)["warning"]
    _append_session_close_warnings(
        result,
        session_id=session_id,
        agent=agent,
    )

    transcript_path = result.get("transcript_path", "")
    abs_path = _FILES_ROOT / transcript_path if transcript_path else None
    transcript_warnings: list[str] = []
    if abs_path and abs_path.is_file():
        try:
            transcript_warnings = _validate_transcript_structure(
                abs_path.read_text(encoding="utf-8"),
                summary_len=len(summary),
                transcript_depth=transcript_depth,
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

    promoted = promote_session_objectives(
        promote_todos, session_id=session_id, agent=agent
    )
    if promoted:
        result["promoted_todos"] = promoted

    return result


def _op_session_handoff_upsert(
    session_id: str,
    handoff_prompt: str,
    handoff_source_path: str | None = None,
    handoff_source_section: str | None = None,
    expected_handoff_prompt: str | None = None,
    expected_derived_handoff_prompt_sha256: str | None = None,
    expected_source_file_sha256: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Upsert handoff_prompt on a closed session (journal row + transcript mirror)."""
    try:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "handoff_prompt": handoff_prompt,
        }
        if handoff_source_path is not None:
            payload["handoff_source_path"] = handoff_source_path
        if handoff_source_section is not None:
            payload["handoff_source_section"] = handoff_source_section
        if expected_handoff_prompt is not None:
            payload["expected_handoff_prompt"] = expected_handoff_prompt
        if expected_derived_handoff_prompt_sha256 is not None:
            payload["expected_derived_handoff_prompt_sha256"] = (
                expected_derived_handoff_prompt_sha256
            )
        if expected_source_file_sha256 is not None:
            payload["expected_source_file_sha256"] = expected_source_file_sha256
        return _upsert_session_handoff_impl(payload)
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            return {
                "error": detail.get("message") or "session_handoff_upsert rejected",
                **detail,
            }
        return {"error": str(detail), "reason": "session_handoff_upsert.rejected"}
    except Exception as exc:
        logger.error(
            "session_handoff_upsert: route handler raised for %s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        return {"error": f"Session handoff upsert failed: {exc}"}
