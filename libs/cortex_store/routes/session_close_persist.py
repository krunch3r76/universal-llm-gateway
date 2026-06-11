"""Session-close idempotency, file write, and DB persistence."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException, status
from universal_logging import get_logger

from ..db import cortex_conn, decode_row, json_encode
from ..dispatch_ops._shared import _FILES_ROOT, record
from ..handoff_surface import build_handoff_surface_preview
from ..models import SessionCloseRequest, SessionCloseResponse
from ..session_close_debrief import attempt_session_close_debrief
from ..session_close_enrichment_telemetry import (
    emit_session_close_depth_decode_fallback,
)
from ..session_close_validation import (
    _audit_normalization_refusals_for_session,
    build_validation_error,
)
from ..session_handoff import (
    WRITE_PATH_SESSION_CLOSE,
    handoff_post_close_findings,
    resolve_handoff_for_write,
)
from ..source_ref_resolution import (
    resolve_source_ref_for_close,
    source_ref_depth_advisory,
)
from ..status_trait_write import trait_insert_extras, transcript_birth_traits
from ..transcript_assembly import compute_text_content_hash
from .session_close_helpers import _ensure_continues_edge, _ensure_transcript_entity
from .session_close_validate import (
    ValidatedCloseContext,
    enforce_handoff_transcript_anchor,
)

logger = get_logger("cortex-api.session_close")

_JSON_FIELDS = frozenset({"domains", "decisions", "open_items", "entity_ids"})


def try_idempotent_session_close(
    body: SessionCloseRequest,
    ctx: ValidatedCloseContext,
) -> SessionCloseResponse | None:
    """Return prior close response when session_id already closed; else None."""
    _idem_conn = cortex_conn()
    try:
        existing = _idem_conn.execute(
            "SELECT id, file_path, handoff_prompt, agent, summary, domains, "
            "decisions, open_items FROM session_journals "
            "WHERE session_id = ?",
            (body.session_id,),
        ).fetchone()
    finally:
        _idem_conn.close()
    if existing is None:
        return None

    prior_transcript_id = f"transcript:{body.session_id}"
    prior_depth = "none"
    with cortex_conn() as _depth_conn:
        depth_row = _depth_conn.execute(
            "SELECT attributes FROM entities WHERE id = ?",
            (prior_transcript_id,),
        ).fetchone()
        if depth_row and depth_row["attributes"]:
            try:
                prior_attrs = json.loads(depth_row["attributes"])
                prior_depth = prior_attrs.get("transcript_depth", "verbatim")
            except (json.JSONDecodeError, AttributeError) as exc:
                emit_session_close_depth_decode_fallback(
                    session_id=body.session_id,
                    error_type=type(exc).__name__,
                )
                prior_depth = "verbatim"
    prior_handoff = existing["handoff_prompt"]
    handoff_retry = resolve_handoff_for_write(
        files_root=_FILES_ROOT,
        write_path=WRITE_PATH_SESSION_CLOSE,
        written_at=ctx.now,
        session_id=body.session_id,
        handoff_source_path=body.handoff_source_path,
        handoff_source_section=body.handoff_source_section,
        handoff_prompt=body.handoff_prompt,
        expected_handoff_prompt=body.expected_handoff_prompt,
        expected_derived_handoff_prompt_sha256=(
            body.expected_derived_handoff_prompt_sha256
        ),
        expected_source_file_sha256=body.expected_source_file_sha256,
    )
    if handoff_retry.handoff_prompt != prior_handoff:
        conflict_detail = build_validation_error(
            reason="session.handoff_would_change",
            field="handoff_prompt",
            received=handoff_retry.handoff_prompt,
            expected=prior_handoff,
            examples=[],
            hint=(
                "Already-closed sessions cannot change handoff via session_close; "
                "use session_handoff_upsert."
            ),
            detail=(
                f"session {body.session_id!r} is already closed; re-close would "
                "change stored handoff_prompt."
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=conflict_detail,
        )
    decoded = decode_row(dict(existing), _JSON_FIELDS)
    debrief = attempt_session_close_debrief(
        session_id=body.session_id,
        agent=decoded["agent"],
        summary=decoded["summary"],
        journal_row_id=existing["id"],
        transcript_depth=prior_depth,
        content_hash=None,
        domains=decoded.get("domains"),
        decisions=decoded.get("decisions"),
        open_items=decoded.get("open_items"),
    )
    return SessionCloseResponse(
        transcript_entity_id=(prior_transcript_id if prior_depth != "none" else None),
        transcript_path=existing["file_path"],
        journal_row_id=existing["id"],
        session_id=body.session_id,
        transcript_depth=prior_depth,
        content_hash=None,
        turn_count=0,
        byte_count=0,
        audit_warnings=None,
        handoff_surface_preview=build_handoff_surface_preview(
            handoff_retry.handoff_prompt,
            handoff_retry.provenance,
            handoff_retry.handoff_verification,
        ),
        debrief_turn_number=debrief.debrief_turn_number,
        debrief_status=debrief.debrief_status,
        debrief_body=debrief.debrief_body,
    )


def persist_session_close(
    body: SessionCloseRequest,
    ctx: ValidatedCloseContext,
) -> SessionCloseResponse:
    """Write transcript file (if any), commit DB tx, return close response."""
    handoff_resolution = resolve_handoff_for_write(
        files_root=_FILES_ROOT,
        write_path=WRITE_PATH_SESSION_CLOSE,
        written_at=ctx.now,
        session_id=body.session_id,
        handoff_source_path=body.handoff_source_path,
        handoff_source_section=body.handoff_source_section,
        handoff_prompt=body.handoff_prompt,
        expected_handoff_prompt=body.expected_handoff_prompt,
        expected_derived_handoff_prompt_sha256=(
            body.expected_derived_handoff_prompt_sha256
        ),
        expected_source_file_sha256=body.expected_source_file_sha256,
    )
    handoff_prompt = handoff_resolution.handoff_prompt
    handoff_provenance = handoff_resolution.provenance
    source_ref_resolution = resolve_source_ref_for_close(
        body.source_ref,
        derivation=body.source_ref_derivation,
        captured_at=ctx.now,
    )

    enforce_handoff_transcript_anchor(
        session_id=body.session_id,
        agent=body.agent,
        handoff_prompt=handoff_prompt,
        handoff_source_path=body.handoff_source_path,
    )

    abs_path: Path | None = None
    if ctx.transcript_path is not None:
        assert ctx.transcript_md is not None
        abs_path = _FILES_ROOT / ctx.transcript_path
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(ctx.transcript_md, encoding="utf-8")
        except OSError as exc:
            logger.error(
                "session_close: failed to write transcript to %s: %s", abs_path, exc
            )
            record(
                "mcp.session.close.write.failed",
                session_id=body.session_id,
                agent=body.agent,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Transcript file write failed: {exc}",
            ) from exc

    name_words = body.summary.split()[:6]
    entity_name = " ".join(name_words)
    if len(name_words) < len(body.summary.split()):
        entity_name += "…"

    conn = cortex_conn()
    journal_row_id = 0
    audit_warnings: list[dict] | None = None
    try:
        if ctx.transcript_entity_id is not None:
            tx_traits = transcript_birth_traits()
            trait_cols, trait_vals = trait_insert_extras(conn, tx_traits)
            tx_cols = [
                "id",
                "type",
                "name",
                "description",
                "source_uri",
                "attributes",
                "created_at",
                "updated_at",
            ]
            tx_attributes: dict[str, object] = {
                "opened_at": ctx.opened_at,
                "closed_at": ctx.now,
                "transcript_depth": body.transcript_depth,
            }
            if handoff_prompt:
                tx_attributes["handoff_prompt"] = handoff_prompt
            if handoff_provenance is not None:
                tx_attributes["handoff_provenance"] = handoff_provenance
            if handoff_resolution.handoff_verification is not None:
                tx_attributes["handoff_verification"] = (
                    handoff_resolution.handoff_verification
                )
            if source_ref_resolution is not None:
                tx_attributes["source_ref"] = source_ref_resolution.stamped_ref
                tx_attributes["source_ref_provenance"] = (
                    source_ref_resolution.provenance
                )
            tx_attributes_json = json_encode(tx_attributes)
            tx_vals: list[object] = [
                ctx.transcript_entity_id,
                "transcript",
                entity_name,
                body.summary,
                ctx.source_uri,
                tx_attributes_json,
                ctx.now,
                ctx.now,
            ]
            tx_cols.extend(trait_cols)
            tx_vals.extend(trait_vals)
            tx_ph = ", ".join(["?"] * len(tx_vals))
            conn.execute(
                f"INSERT OR IGNORE INTO entities ({', '.join(tx_cols)}) "
                f"VALUES ({tx_ph})",
                tuple(tx_vals),
            )
            conn.execute(
                "UPDATE entities SET attributes = ?, updated_at = ? WHERE id = ?",
                (tx_attributes_json, ctx.now, ctx.transcript_entity_id),
            )
        journal_source_ref = (
            source_ref_resolution.stamped_ref if source_ref_resolution else None
        )
        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, domains, decisions, open_items, "
            "entity_ids, file_path, session_id, prior_session_id, handoff_prompt, "
            "source_ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ctx.now,
                body.agent,
                body.summary,
                json_encode(body.domains),
                json_encode(body.decisions),
                json_encode(body.open_items),
                json_encode(body.entity_ids),
                ctx.transcript_path,
                body.session_id,
                body.prior_session_id,
                handoff_prompt,
                journal_source_ref,
            ),
        )
        journal_row_id = cur.lastrowid or 0

        if body.prior_session_id:
            _ensure_transcript_entity(conn, body.prior_session_id, body.agent, ctx.now)
            _ensure_continues_edge(
                conn, body.session_id, body.prior_session_id, body.agent, ctx.now
            )

        conn.commit()
        findings = _audit_normalization_refusals_for_session(conn, body.session_id)
        findings = [
            *findings,
            *handoff_post_close_findings(
                resolution=handoff_resolution,
                handoff_source_path=body.handoff_source_path,
                files_root=_FILES_ROOT,
            ),
        ]
        if ctx.heading_warning is not None:
            findings = [*findings, ctx.heading_warning]
        depth_advisory = source_ref_depth_advisory(
            transcript_depth=body.transcript_depth,
            has_source_ref=source_ref_resolution is not None,
        )
        if depth_advisory is not None:
            findings = [*findings, depth_advisory]
        audit_warnings = findings if findings else None
    except Exception:
        conn.rollback()
        if abs_path is not None:
            try:
                abs_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to unlink transcript after DB rollback: %s", abs_path
                )
                record(
                    "mcp.session.close.cleanup.failed",
                    session_id=body.session_id,
                    agent=body.agent,
                )
        logger.error(
            "session_close DB transaction failed for %s",
            body.session_id,
            exc_info=True,
        )
        raise
    finally:
        conn.close()

    content_hash: str | None = (
        compute_text_content_hash(ctx.transcript_md)
        if ctx.transcript_md is not None
        else None
    )
    byte_count = (
        len(ctx.transcript_md.encode("utf-8")) if ctx.transcript_md is not None else 0
    )
    logger.info(
        "session_close: %s agent=%s entity=%s journal_row=%d hash=%s depth=%s",
        body.session_id,
        body.agent,
        ctx.transcript_entity_id,
        journal_row_id,
        content_hash,
        body.transcript_depth,
    )
    record(
        "mcp.session.close.atomic",
        agent=body.agent,
        session_id=body.session_id,
        transcript_path=ctx.transcript_path,
        content_hash=content_hash,
        turn_count=ctx.turn_count,
        byte_count=byte_count,
        transcript_depth=body.transcript_depth,
    )

    debrief = attempt_session_close_debrief(
        session_id=body.session_id,
        agent=body.agent,
        summary=body.summary,
        journal_row_id=journal_row_id,
        transcript_depth=body.transcript_depth,
        content_hash=content_hash,
        domains=body.domains,
        decisions=body.decisions,
        open_items=body.open_items,
    )

    return SessionCloseResponse(
        transcript_entity_id=ctx.transcript_entity_id,
        transcript_path=ctx.transcript_path,
        journal_row_id=journal_row_id,
        session_id=body.session_id,
        transcript_depth=body.transcript_depth,
        content_hash=content_hash,
        turn_count=ctx.turn_count,
        byte_count=byte_count,
        audit_warnings=audit_warnings,
        handoff_surface_preview=build_handoff_surface_preview(
            handoff_prompt,
            handoff_provenance,
            handoff_resolution.handoff_verification,
        ),
        debrief_turn_number=debrief.debrief_turn_number,
        debrief_status=debrief.debrief_status,
        debrief_body=debrief.debrief_body,
    )
