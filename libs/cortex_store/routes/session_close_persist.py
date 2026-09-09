"""Session-close idempotency, file write, and DB persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text
from fastapi import HTTPException, status
from universal_logging import get_logger

from ..db import cortex_conn, json_encode
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
from ..verbatim_succession import (
    build_seal_envelope_meta,
    journal_verbatim_bytes,
    load_sealed_envelope_from_path,
    prefix_holds,
    split_verbatim_layer,
    stamp_verbatim_fields,
    transcript_messages_path,
)
from .session_close_helpers import _ensure_continues_edge, _ensure_transcript_entity
from .session_close_validate import (
    ValidatedCloseContext,
    enforce_handoff_transcript_anchor,
)

logger = get_logger("cortex-api.session_close")


def _enforce_messages_v1_prefix_extend(
    *,
    prior_codec: str | None,
    prior_seal_path: Path | None,
    new_messages: list[dict[str, Any]],
    session_id: str,
    mode: str,
) -> None:
    """PREFIX-EXTEND gate for messages-v1 seal succession (R5)."""
    if prior_codec != "messages-v1" or prior_seal_path is None or not prior_seal_path.is_file():
        return
    prior_envelope = load_sealed_envelope_from_path(prior_seal_path)
    if prefix_holds("messages-v1", prior_envelope.messages, new_messages):
        return
    conflict = build_validation_error(
        reason="succession.verbatim_diverged",
        field="transcript_messages",
        received="non-prefix extension",
        expected="messages-v1 PREFIX-EXTEND of sealed envelope",
        examples=[],
        hint=f"Succession {mode} requires PREFIX-EXTEND on sealed messages.",
        detail=(
            f"session {session_id!r} messages-v1 {mode} refused: "
            "new envelope is not a prefix extension of sealed messages."
        ),
    )
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=conflict)


def _dominant_lane_from_entity_ids(entity_ids: list[str] | None) -> str | None:
    from ..dispatch_ops._session_bus_thread_disposition import parse_bus_thread_refs

    refs = parse_bus_thread_refs(entity_ids)
    return refs[0] if refs else None


def try_idempotent_session_close(
    body: SessionCloseRequest,
    ctx: ValidatedCloseContext,
) -> tuple[SessionCloseResponse | None, int | None]:
    """Idempotency gate for session_close.

    Returns ``(response, reuse_journal_row_id)``:

      - ``(response, None)`` — a *real* prior session_close exists for this
        session_id; echo it (``response.already_closed=True``).
      - ``(None, row_id)`` — a journal row exists but was NOT created by a
        real close (legacy ``journal_write`` row: no transcript entity AND
        ``file_path IS NULL``). Persist must run the full close and UPDATE
        row ``row_id`` in place instead of inserting a duplicate.
      - ``(None, None)`` — no prior row; fresh close.

    Silent-no-op regression guard (friction 22962): a deprecated
    journal_write row must never suppress the compose/write path while
    returning a success envelope.
    """
    _idem_conn = cortex_conn()
    try:
        existing = _idem_conn.execute(
            "SELECT id, file_path, handoff_prompt, agent, summary, domains, "
            "decisions, open_items, closed_by FROM session_journals "
            "WHERE session_id = ?",
            (body.session_id,),
        ).fetchone()
    finally:
        _idem_conn.close()
    if existing is None:
        return None, None

    if (
        existing["file_path"] is not None
        and existing["closed_by"] == "succession"
        and body.closed_by != "succession"
    ):
        return None, existing["id"]

    if (
        existing["file_path"] is not None
        and existing["closed_by"] == "succession"
        and body.closed_by == "succession"
    ):
        return None, existing["id"]

    prior_transcript_id = f"transcript:{body.session_id}"
    prior_depth = "none"
    with cortex_conn() as _depth_conn:
        depth_row = _depth_conn.execute(
            "SELECT attributes FROM entities WHERE id = ?",
            (prior_transcript_id,),
        ).fetchone()
        entity_stamped_by_close = False
        if depth_row and depth_row["attributes"]:
            try:
                prior_attrs = json.loads(depth_row["attributes"])
                entity_stamped_by_close = "transcript_depth" in prior_attrs
                prior_depth = prior_attrs.get("transcript_depth", "verbatim")
            except (json.JSONDecodeError, AttributeError) as exc:
                emit_session_close_depth_decode_fallback(
                    session_id=body.session_id,
                    error_type=type(exc).__name__,
                )
                prior_depth = "verbatim"
                entity_stamped_by_close = True  # can't prove legacy; be safe
        has_bare_transcript_entity = depth_row is not None
        if (
            existing["file_path"] is None
            and not entity_stamped_by_close
            and has_bare_transcript_entity
        ):
            # ``journal_write`` leaves a bare transcript entity (opened_at/
            # closed_at) without ``transcript_depth``. A genuine depth="none"
            # close leaves neither file nor entity — idempotent echo below.
            # Re-close only the journal_write legacy stub in place (22962).
            logger.info(
                "session_close: session_id %s has a legacy journal row "
                "(id=%d, file_path NULL, no close-stamped transcript entity) "
                "— proceeding with real close, reusing the row",
                body.session_id,
                existing["id"],
            )
            record(
                "mcp.session.close.legacy_row.reclose",
                session_id=body.session_id,
                agent=body.agent,
                journal_row_id=existing["id"],
            )
            return None, existing["id"]
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
        if existing["closed_by"] == "succession" and body.closed_by != "succession":
            pass
        else:
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
    # Genuinely re-closing an already-closed session: echo the prior close
    # and retry debrief with stored journal fields (idempotent debrief retry).
    stored_domains = (
        json.loads(existing["domains"]) if existing["domains"] else None
    )
    stored_decisions = (
        json.loads(existing["decisions"]) if existing["decisions"] else None
    )
    stored_open_items = (
        json.loads(existing["open_items"]) if existing["open_items"] else None
    )
    debrief = attempt_session_close_debrief(
        session_id=body.session_id,
        agent=str(existing["agent"] or body.agent),
        summary=str(existing["summary"]),
        journal_row_id=int(existing["id"]),
        transcript_depth=prior_depth,
        content_hash=None,
        domains=stored_domains,
        decisions=stored_decisions,
        open_items=stored_open_items,
        closed_by=existing["closed_by"],
    )
    return (
        SessionCloseResponse(
            transcript_entity_id=(
                prior_transcript_id if prior_depth != "none" else None
            ),
            transcript_path=existing["file_path"],
            journal_row_id=existing["id"],
            session_id=body.session_id,
            transcript_depth=prior_depth,
            content_hash=None,
            turn_count=0,
            byte_count=0,
            already_closed=True,
            audit_warnings=None,
            handoff_surface_preview=build_handoff_surface_preview(
                handoff_retry.handoff_prompt,
                handoff_retry.provenance,
                handoff_retry.handoff_verification,
            ),
            debrief_turn_number=debrief.debrief_turn_number,
            debrief_status=debrief.debrief_status,
            debrief_body=debrief.debrief_body,
        ),
        None,
    )


def persist_session_close(
    body: SessionCloseRequest,
    ctx: ValidatedCloseContext,
    reuse_journal_row_id: int | None = None,
) -> SessionCloseResponse:
    """Write transcript file (if any), commit DB tx, return close response.

    ``reuse_journal_row_id``: id of a legacy journal_write row for this
    session_id — the close UPDATEs that row in place instead of inserting a
    duplicate (see try_idempotent_session_close).
    """
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

    conversation_uuid: str | None = None
    if body.transcript_jsonl_path:
        from ..session_close_successor_hop import conversation_uuid_from_jsonl_path
        from ..transcript_assembly import TranscriptPathError, resolve_jsonl_path

        try:
            conversation_uuid = conversation_uuid_from_jsonl_path(
                resolve_jsonl_path(body.transcript_jsonl_path)
            )
        except TranscriptPathError:
            conversation_uuid = None

    structural_fill = False
    succession_extend = False
    if reuse_journal_row_id is not None and body.closed_by != "succession":
        _fill_conn = cortex_conn()
        try:
            prior = _fill_conn.execute(
                "SELECT closed_by, file_path, verbatim_bytes, verbatim_codec "
                "FROM session_journals WHERE id = ?",
                (reuse_journal_row_id,),
            ).fetchone()
        finally:
            _fill_conn.close()
        if prior is not None and prior["closed_by"] == "succession":
            structural_fill = True
            if prior["file_path"] and ctx.verbatim_md is not None:
                prior_path = _FILES_ROOT / prior["file_path"]
                if prior_path.is_file():
                    prior_text = prior_path.read_text(encoding="utf-8")
                    prior_verbatim = split_verbatim_layer(
                        prior_text,
                        verbatim_bytes=journal_verbatim_bytes(prior),
                    )
                    new_verbatim = ctx.verbatim_md
                    if not new_verbatim.startswith(prior_verbatim):
                        conflict = build_validation_error(
                            reason="succession.verbatim_diverged",
                            field="transcript_messages",
                            received="non-prefix extension",
                            expected="byte-prefix of sealed verbatim",
                            examples=[],
                            hint="Succession fill requires PREFIX-EXTEND from live JSONL.",
                            detail=(
                                f"session {body.session_id!r} succession fill refused: "
                                "new verbatim is not a prefix extension of sealed verbatim."
                            ),
                        )
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=conflict,
                        )
    elif (
        reuse_journal_row_id is not None
        and body.closed_by == "succession"
        and ctx.verbatim_md is not None
    ):
        succession_extend = True
        _ext_conn = cortex_conn()
        try:
            prior = _ext_conn.execute(
                "SELECT file_path, verbatim_bytes, verbatim_codec FROM session_journals "
                "WHERE id = ?",
                (reuse_journal_row_id,),
            ).fetchone()
        finally:
            _ext_conn.close()
        if prior is not None and prior["file_path"]:
            prior_codec = str(prior["verbatim_codec"] or "md-v1")
            if prior_codec != "messages-v1":
                prior_path = _FILES_ROOT / prior["file_path"]
                if prior_path.is_file():
                    prior_text = prior_path.read_text(encoding="utf-8")
                    prior_verbatim = split_verbatim_layer(
                        prior_text,
                        verbatim_bytes=journal_verbatim_bytes(prior),
                    )
                    if not ctx.verbatim_md.startswith(prior_verbatim):
                        conflict = build_validation_error(
                            reason="succession.verbatim_diverged",
                            field="transcript_messages",
                            received="non-prefix extension",
                            expected="byte-prefix of sealed verbatim",
                            examples=[],
                            hint="Succession extend requires PREFIX-EXTEND from live JSONL.",
                            detail=(
                                f"session {body.session_id!r} succession extend refused: "
                                "new verbatim is not a prefix extension of sealed verbatim."
                            ),
                        )
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=conflict,
                        )

    abs_path: Path | None = None
    seal_abs_path: Path | None = None
    prior_transcript_snapshot: str | None = None
    prior_seal_snapshot: str | None = None
    prior_codec: str | None = None
    if ctx.transcript_path is not None:
        assert ctx.composed_md is not None
        abs_path = _FILES_ROOT / ctx.transcript_path
        if ctx.envelope is not None and ctx.messages_path is not None:
            seal_abs_path = _FILES_ROOT / ctx.messages_path
            if reuse_journal_row_id is not None and (structural_fill or succession_extend):
                _pc_conn = cortex_conn()
                try:
                    _prow = _pc_conn.execute(
                        "SELECT verbatim_codec, file_path FROM session_journals WHERE id = ?",
                        (reuse_journal_row_id,),
                    ).fetchone()
                finally:
                    _pc_conn.close()
                prior_rel = str(_prow["file_path"]) if _prow and _prow["file_path"] else None
                prior_codec_for_seal = (
                    str(_prow["verbatim_codec"]) if _prow and _prow["verbatim_codec"] else None
                )
                prior_seal_path = (
                    _FILES_ROOT / transcript_messages_path(prior_rel)
                    if prior_rel
                    else None
                )
                _enforce_messages_v1_prefix_extend(
                    prior_codec=prior_codec_for_seal,
                    prior_seal_path=prior_seal_path,
                    new_messages=ctx.envelope.messages,
                    session_id=body.session_id,
                    mode="fill" if structural_fill else "extend",
                )
            sealed_envelope = build_seal_envelope_meta(
                ctx.envelope,
                session_id=body.session_id,
                tools="marker",
                sealed_at=ctx.now,
            )
            seal_payload = json.dumps(
                sealed_envelope.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
            ) + "\n"
            if (structural_fill or succession_extend) and seal_abs_path.is_file():
                prior_seal_snapshot = seal_abs_path.read_text(encoding="utf-8")
            try:
                durable_write_text(
                    seal_abs_path, seal_payload, retain_store_root=_FILES_ROOT
                )
            except OSError as exc:
                logger.error(
                    "session_close: failed to write seal to %s: %s",
                    seal_abs_path,
                    exc,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Seal file write failed: {exc}",
                ) from exc
        if (structural_fill or succession_extend) and abs_path.is_file():
            prior_transcript_snapshot = abs_path.read_text(encoding="utf-8")
            if reuse_journal_row_id is not None:
                _codec_conn = cortex_conn()
                try:
                    row = _codec_conn.execute(
                        "SELECT verbatim_codec FROM session_journals WHERE id = ?",
                        (reuse_journal_row_id,),
                    ).fetchone()
                    prior_codec = row["verbatim_codec"] if row else None
                finally:
                    _codec_conn.close()
        if (
            ctx.envelope is not None
            and prior_codec == "md-v1"
            and prior_transcript_snapshot is not None
        ):
            _prior_bytes = None
            if reuse_journal_row_id is not None:
                _pb_conn = cortex_conn()
                try:
                    _prow = _pb_conn.execute(
                        "SELECT verbatim_bytes FROM session_journals WHERE id = ?",
                        (reuse_journal_row_id,),
                    ).fetchone()
                    _prior_bytes = journal_verbatim_bytes(_prow)
                finally:
                    _pb_conn.close()
            prior_verbatim = split_verbatim_layer(
                prior_transcript_snapshot,
                verbatim_bytes=_prior_bytes,
            )
            if not (ctx.verbatim_md or "").startswith(prior_verbatim):
                from ..events_tape import transcript_seal_codec_diverged

                transcript_seal_codec_diverged(
                    session_id=body.session_id,
                    transcript_id=conversation_uuid,
                    prior_codec="md-v1",
                    reason="render_prefix_mismatch",
                )
                conflict = build_validation_error(
                    reason="succession.verbatim_diverged",
                    field="transcript_jsonl_path",
                    received="non-prefix extension",
                    expected="byte-prefix of sealed verbatim",
                    examples=[],
                    hint="Legacy md-v1 extension requires render prefix match.",
                    detail=(
                        f"session {body.session_id!r} md-v1→messages-v1 refused: "
                        "rendered verbatim is not a prefix of sealed md."
                    ),
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=conflict,
                )
        try:
            durable_write_text(
                abs_path, ctx.composed_md, retain_store_root=_FILES_ROOT
            )
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
                "transcript_depth": ctx.archival_depth,
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
            if conversation_uuid:
                tx_attributes["conversation_uuid"] = conversation_uuid
            if ctx.verbatim_codec:
                tx_attributes["verbatim_codec"] = ctx.verbatim_codec
            if ctx.messages_sha256:
                tx_attributes["messages_sha256"] = ctx.messages_sha256
            if ctx.messages_path:
                tx_attributes["messages_uri"] = f"files://{ctx.messages_path}"
            if ctx.envelope is not None:
                tx_attributes["surface"] = ctx.envelope.meta.surface
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
        journal_closed_by = (
            body.agent
            if structural_fill
            else (body.closed_by if body.closed_by else body.agent)
        )
        dominant_lane = _dominant_lane_from_entity_ids(body.entity_ids)
        if reuse_journal_row_id is not None:
            conn.execute(
                "UPDATE session_journals SET "
                "timestamp = ?, agent = ?, summary = ?, domains = ?, "
                "decisions = ?, open_items = ?, entity_ids = ?, file_path = ?, "
                "prior_session_id = ?, handoff_prompt = ?, source_ref = ?, "
                "closed_by = ?, conversation_uuid = COALESCE(?, conversation_uuid), "
                "dominant_lane = COALESCE(?, dominant_lane) "
                "WHERE id = ?",
                (
                    ctx.now,
                    body.agent,
                    body.summary,
                    json_encode(body.domains),
                    json_encode(body.decisions),
                    json_encode(body.open_items),
                    json_encode(body.entity_ids),
                    ctx.transcript_path,
                    body.prior_session_id,
                    handoff_prompt,
                    journal_source_ref,
                    journal_closed_by,
                    conversation_uuid,
                    dominant_lane,
                    reuse_journal_row_id,
                ),
            )
            journal_row_id = reuse_journal_row_id
            if ctx.verbatim_md is not None and ctx.verbatim_codec:
                payload = (
                    ctx.envelope.messages
                    if ctx.envelope is not None
                    else ctx.verbatim_md
                )
                stamp_verbatim_fields(
                    conn,
                    session_id=body.session_id,
                    codec=ctx.verbatim_codec,
                    payload=payload,
                )
        else:
            sealed_by = body.agent if body.closed_by == "succession" else None
            sealed_on = ctx.now if body.closed_by == "succession" else None
            cur = conn.execute(
                "INSERT INTO session_journals "
                "(timestamp, agent, summary, domains, decisions, open_items, "
                "entity_ids, file_path, session_id, prior_session_id, "
                "handoff_prompt, source_ref, closed_by, sealed_by, sealed_on, "
                "conversation_uuid, dominant_lane) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    journal_closed_by,
                    sealed_by,
                    sealed_on,
                    conversation_uuid,
                    dominant_lane,
                ),
            )
            journal_row_id = cur.lastrowid or 0
            if ctx.verbatim_md is not None and ctx.verbatim_codec:
                payload = (
                    ctx.envelope.messages
                    if ctx.envelope is not None
                    else ctx.verbatim_md
                )
                stamp_verbatim_fields(
                    conn,
                    session_id=body.session_id,
                    codec=ctx.verbatim_codec,
                    payload=payload,
                )

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
            transcript_depth=ctx.archival_depth,
            has_source_ref=source_ref_resolution is not None,
        )
        if depth_advisory is not None:
            findings = [*findings, depth_advisory]
        audit_warnings = findings if findings else None
    except Exception:
        conn.rollback()
        if seal_abs_path is not None:
            try:
                if prior_seal_snapshot is not None:
                    durable_write_text(
                        seal_abs_path,
                        prior_seal_snapshot,
                        retain_store_root=_FILES_ROOT,
                    )
                elif not structural_fill and not succession_extend:
                    seal_abs_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to restore/unlink seal after DB rollback: %s",
                    seal_abs_path,
                )
        if abs_path is not None:
            try:
                if prior_transcript_snapshot is not None:
                    durable_write_text(
                        abs_path,
                        prior_transcript_snapshot,
                        retain_store_root=_FILES_ROOT,
                    )
                elif not structural_fill and not succession_extend:
                    abs_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to restore/unlink transcript after DB rollback: %s",
                    abs_path,
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
        compute_text_content_hash(ctx.composed_md)
        if ctx.composed_md is not None
        else None
    )
    byte_count = (
        len(ctx.composed_md.encode("utf-8")) if ctx.composed_md is not None else 0
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
    from ..events_close import close_commit_completed

    close_commit_completed(
        session_id=body.session_id,
        agent=body.agent,
        journal_row_id=journal_row_id,
        transcript_depth=ctx.archival_depth,
    )
    if structural_fill:
        from ..events_tape import session_close_succession_structural_filled

        fill_reason = "PREFIX-EXTEND" if ctx.turn_count > 0 and body.transcript_jsonl_path else "SPLICE"
        session_close_succession_structural_filled(
            session_id=body.session_id,
            agent=body.agent,
            journal_row_id=journal_row_id,
            extended=bool(body.transcript_jsonl_path),
            reason=fill_reason,
        )
    elif succession_extend:
        from ..events_tape import session_close_succession_structural_filled

        session_close_succession_structural_filled(
            session_id=body.session_id,
            agent=body.agent,
            journal_row_id=journal_row_id,
            extended=True,
            reason="PREFIX-EXTEND",
        )

    debrief = attempt_session_close_debrief(
        session_id=body.session_id,
        agent=body.agent,
        summary=body.summary,
        journal_row_id=journal_row_id,
        transcript_depth=ctx.archival_depth,
        content_hash=content_hash,
        domains=body.domains,
        decisions=body.decisions,
        open_items=body.open_items,
        closed_by=body.closed_by,
    )

    if ctx.envelope is not None and ctx.verbatim_codec:
        from ..events_tape import transcript_sealed_messages

        transcript_sealed_messages(
            session_id=body.session_id,
            surface=ctx.envelope.meta.surface,
            verbatim_codec=ctx.verbatim_codec,
            prior_codec=prior_codec,
            turn_count=ctx.turn_count,
            messages_sha256=ctx.messages_sha256 or "",
            extended=succession_extend,
        )

    return SessionCloseResponse(
        transcript_entity_id=ctx.transcript_entity_id,
        transcript_path=ctx.transcript_path,
        journal_row_id=journal_row_id,
        session_id=body.session_id,
        transcript_depth=ctx.archival_depth,
        content_hash=content_hash,
        turn_count=ctx.turn_count,
        byte_count=byte_count,
        messages_sha256=ctx.messages_sha256,
        verbatim_codec=ctx.verbatim_codec,
        messages_path=ctx.messages_path,
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
