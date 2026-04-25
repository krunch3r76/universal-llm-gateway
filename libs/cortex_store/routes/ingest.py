"""Document ingestion routes — auto-chunk + assert-from-chunk for v2.4.

POST /ingest-document: chunks content, creates chunk records, extracts dates.
POST /assert-from-chunk: creates assertion with auto-populated provenance fields.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError

from ..assertion_quality import validate_assertion
from ..claim_hash import compute_claim_hash
from ..db import cortex_conn, decode_row, json_encode, query
from ..models import (
    AssertFromChunkRequest,
    AssertFromChunkResponse,
    AssertionCreate,
    AssertionItem,
    ChunkResult,
    IngestDocumentRequest,
    IngestDocumentResponse,
)
from ..near_dup import check_near_duplicate, record_near_duplicate
from .assertions import _ASSERTION_COLS, _JSON_FIELDS, _payload_validation_exception

logger = logging.getLogger("cortex-api.ingest")
router = APIRouter(tags=["ingest"])

_DATE_RE = re.compile(
    r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b"
    r"|(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4}\b)"
    r"|(\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b)",
    re.IGNORECASE,
)

_PARA_SPLIT = re.compile(r"\n{2,}")
_MAX_CHUNK_TOKENS = 800
_APPROX_CHARS_PER_TOKEN = 4


def _extract_dates(text: str) -> list[str]:
    """Extract date strings from text using regex patterns."""
    return [g.strip() for groups in _DATE_RE.findall(text) for g in groups if g]


def _chunk_content(text: str) -> list[str]:
    """Split text into paragraph-boundary chunks respecting token limits."""
    paragraphs = _PARA_SPLIT.split(text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_tokens = len(para) // _APPROX_CHARS_PER_TOKEN
        if current and (current_len + para_tokens) > _MAX_CHUNK_TOKENS:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_tokens
        else:
            current.append(para)
            current_len += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks if chunks else [text]


@router.post(
    "/ingest-document",
    response_model=IngestDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_document(body: IngestDocumentRequest) -> IngestDocumentResponse:
    """Auto-chunk content, create chunk records, extract dates per chunk.

    Returns chunk list ready for assert_from_chunk(). Each chunk carries
    extracted dates that can be used to set valid_from on assertions.
    """
    raw_chunks = _chunk_content(body.content)

    conn = cortex_conn()
    try:
        results: list[ChunkResult] = []
        for idx, chunk_text in enumerate(raw_chunks):
            token_count = len(chunk_text) // _APPROX_CHARS_PER_TOKEN
            cur = conn.execute(
                "INSERT INTO chunks "
                "(content, source_uri, source_date, observer, chunk_index, token_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    chunk_text,
                    body.source_uri,
                    body.source_date,
                    body.observer,
                    idx,
                    token_count,
                ),
            )
            chunk_id = cur.lastrowid
            assert chunk_id is not None

            dates = _extract_dates(chunk_text)
            snippet = chunk_text[:200]
            results.append(
                ChunkResult(
                    chunk_id=chunk_id,
                    chunk_index=idx,
                    snippet=snippet,
                    extracted_dates=dates,
                    token_count=token_count,
                )
            )
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "ingest_document: %s — %d chunks created",
        body.source_uri,
        len(results),
    )
    return IngestDocumentResponse(
        source_uri=body.source_uri,
        chunk_count=len(results),
        chunks=results,
    )


@router.post(
    "/assert-from-chunk",
    response_model=AssertFromChunkResponse,
    status_code=status.HTTP_201_CREATED,
)
def assert_from_chunk(body: AssertFromChunkRequest) -> AssertFromChunkResponse:
    """Create assertion with provenance auto-populated from chunk.

    Auto-sets evidence_uris from chunk's source_uri, defaults
    derivation_type to 'compression', and suggests valid_from
    from chunk's extracted dates.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = cortex_conn()
    try:
        chunk_rows = query(
            conn,
            "SELECT id, source_uri, source_date, content FROM chunks WHERE id = ?",
            (body.chunk_id,),
        )
        if not chunk_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Chunk not found: {body.chunk_id}",
            )
        chunk = chunk_rows[0]

        evidence_uris = body.evidence_uris or []
        chunk_source = chunk.get("source_uri")
        if chunk_source and chunk_source not in evidence_uris:
            evidence_uris = [chunk_source, *evidence_uris]

        derivation_type = body.derivation_type or "compression"

        suggested_valid_from: str | None = None
        chunk_dates = _extract_dates(chunk.get("content", ""))
        if chunk_dates:
            suggested_valid_from = chunk_dates[0]

        valid_from = body.valid_from or suggested_valid_from
        observed_at = body.observed_at or now

        try:
            assertion_body = AssertionCreate(
                entity_id=body.entity_id,
                claim=body.claim,
                confidence=body.confidence,  # type: ignore[arg-type]
                evidence=body.evidence,
                evidence_uris=evidence_uris,
                chunk_id=body.chunk_id,
                derivation_type=derivation_type,  # type: ignore[arg-type]
                confidence_score=body.confidence_score,
                observed_at=observed_at,
                valid_from=valid_from,
                reasoning_summary=body.reasoning_summary,
                resolution_status=body.resolution_status,  # type: ignore[arg-type]
                seeded_by=body.seeded_by,
            )
        except ValidationError as exc:
            raise _payload_validation_exception(exc) from exc

        validation = validate_assertion(assertion_body)
        quality_score = validation.quality_score

        if validation.rejected:
            diagnostics = [
                {"field": d.field, "message": d.message} for d in validation.hard_reject
            ]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "assertion_quality_rejected",
                    "quality_score": quality_score,
                    "diagnostics": diagnostics,
                },
            )

        review_status: str | None = None
        validation_warnings: list[dict[str, str]] | None = None
        if validation.route_to_staging:
            review_status = "staged"
            validation_warnings = [
                {"field": d.field, "message": d.message} for d in validation.warnings
            ]

        entities = query(
            conn, "SELECT id FROM entities WHERE id = ?", (body.entity_id,)
        )
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {body.entity_id}",
            )

        claim_hash = compute_claim_hash(body.entity_id, body.claim)

        cur = conn.execute(
            "INSERT OR IGNORE INTO assertions ("
            "  entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by,"
            "  chunk_id, derivation_type, reasoning_summary, observed_at,"
            "  valid_from, is_atomic, is_decontextualized, claim_hash,"
            "  resolution_status, quality_score, review_status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.entity_id,
                body.claim,
                body.confidence,
                body.confidence_score,
                body.evidence,
                json_encode(evidence_uris),
                body.seeded_by,
                body.chunk_id,
                derivation_type,
                body.reasoning_summary,
                observed_at,
                valid_from,
                True,
                True,
                claim_hash,
                body.resolution_status,
                quality_score,
                review_status,
            ),
        )
        conn.commit()

        was_new = cur.rowcount > 0
        new_id = cur.lastrowid

        if was_new:
            rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
                (new_id,),
            )
        else:
            rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions "
                "WHERE entity_id = ? AND claim_hash = ? AND superseded_by IS NULL",
                (body.entity_id, claim_hash),
            )

        if was_new:
            match = check_near_duplicate(conn, body.entity_id, body.claim, new_id)
            if match:
                record_near_duplicate(conn, new_id, match.existing_id, match.score)
    finally:
        conn.close()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assertion created but could not be read back",
        )

    item = AssertionItem(**decode_row(rows[0], _JSON_FIELDS))
    return AssertFromChunkResponse(
        item=item,
        was_new=was_new,
        suggested_valid_from=suggested_valid_from,
        quality_score=quality_score,
        validation_warnings=validation_warnings,
    )


def _ingest_document_impl(payload: dict[str, object]) -> dict[str, object]:
    try:
        body = IngestDocumentRequest.model_validate(payload)
    except ValidationError as exc:
        raise _payload_validation_exception(exc) from exc
    result = ingest_document(body)
    return result.model_dump(mode="json")


def _assert_from_chunk_impl(payload: dict[str, object]) -> dict[str, object]:
    try:
        body = AssertFromChunkRequest.model_validate(payload)
    except ValidationError as exc:
        raise _payload_validation_exception(exc) from exc
    result = assert_from_chunk(body)
    return result.model_dump(mode="json")
