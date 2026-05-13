"""Cortex URI resolution — cortex://TYPE/SLUG[?r=N][&a=ARTIFACT][#PINPOINT].

Resolves cortex:// URIs to entity + optional assertion + optional chunk data.
Additive endpoint — does not modify any existing routes. The ``#PINPOINT``
fragment extension is spec § 2.2 of
docs/architecture/entity-backed-claim-provenance.md and reads from the
``chunks.pinpoint`` column added by migration 037.
"""

from __future__ import annotations

import logging
import sqlite3
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException, Query, status

from ..db import cortex_conn, decode_row, query

_resolve_logger = logging.getLogger("cortex-api.resolve")

router = APIRouter(prefix="/resolve", tags=["resolve"])

_ASSERTION_COLS = (
    "id, entity_id, claim, confidence, confidence_score, evidence, evidence_uris, seeded_by, "
    "derivation_type, chunk_id, reasoning_summary, is_atomic, is_decontextualized, "
    "observed_at, valid_from, valid_until, superseded_by, "
    "review_status, reviewer, reviewed_at, review_notes, "
    "resolution_status, fulfillment_assertion_id, quality_score, "
    "prospective_summary, events_json, artifact_uri, artifact_storage, "
    "entrenchment_score, predicate_form, created_at"
)

_JSON_FIELDS = frozenset({"evidence_uris"})


def parse_cortex_uri(uri: str) -> dict:
    """Parse a cortex:// URI into components.

    Supports:
      cortex://TYPE/SLUG
      cortex://TYPE/SLUG?r=N
      cortex://TYPE/SLUG?r=N&a=ARTIFACT
      cortex://TYPE/SLUG#PINPOINT          (spec § 2.2 fragment extension)
      cortex://assertion/ID

    The ``pinpoint`` field is a free-form label (e.g. ``f-1-B`` for
    statute (f)(1)(B)) — its meaning is defined by the source's chunk
    manifest, not by this parser.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "cortex":
        raise ValueError(f"Not a cortex:// URI: {uri}")

    entity_type = parsed.netloc
    slug = parsed.path.lstrip("/")

    if not entity_type or not slug:
        raise ValueError(f"Invalid cortex:// URI: {uri}")

    params = parse_qs(parsed.query)
    revision = int(params["r"][0]) if "r" in params else None
    artifact = params["a"][0] if "a" in params else None
    pinpoint = parsed.fragment or None

    return {
        "type": entity_type,
        "slug": slug,
        "entity_id": f"{entity_type}:{slug}",
        "revision": revision,
        "artifact": artifact,
        "pinpoint": pinpoint,
    }


def _resolve_pinpoint_chunk(
    conn: sqlite3.Connection, *, entity_id: str, pinpoint: str
) -> dict | None:
    """Look up the chunk whose (source_uri = cortex://entity_id, pinpoint).

    Returns the chunk row as a dict, or ``None`` if no chunk matches —
    callers surface this as ``pinpoint_unresolved`` per spec § 2.2.

    The lookup keys on the canonical ``cortex://<entity_id>`` form. If
    callers wrote chunks using an alternative ``source_uri`` (e.g. a
    workspace path), pinpoint resolution will not find them — Phase 2
    seeding standardizes on the cortex:// form.
    """
    canonical_uri = f"cortex://{entity_id.replace(':', '/', 1)}"
    rows = query(
        conn,
        "SELECT id, content, source_uri, source_date, observer, "
        "       chunk_index, token_count, pinpoint "
        "FROM chunks "
        "WHERE source_uri = ? AND pinpoint = ?",
        (canonical_uri, pinpoint),
    )
    if not rows:
        return None
    return dict(rows[0])


@router.get("")
def resolve_cortex_uri(
    uri: str = Query(..., description="cortex:// URI to resolve"),
    tag: str | None = Query(
        None, description="Resolve to assertion pointed to by this tag"
    ),
) -> dict:
    """Resolve a cortex:// URI to entity + optional assertion data.

    When *tag* is provided, resolve to the assertion pointed to by that tag
    instead of the latest non-superseded assertion.
    """
    try:
        parsed = parse_cortex_uri(uri)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    with cortex_conn() as conn:
        # Special case: cortex://assertion/ID
        if parsed["type"] == "assertion":
            try:
                assertion_id = int(parsed["slug"])
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid assertion ID: {parsed['slug']}",
                )
            rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
                (assertion_id,),
            )
            if not rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Assertion not found: {assertion_id}",
                )
            return {
                "resolved": "assertion",
                "uri": uri,
                "assertion": decode_row(rows[0], _JSON_FIELDS),
            }

        # Entity resolution
        entity_id = parsed["entity_id"]
        entity_rows = query(
            conn,
            "SELECT * FROM entities WHERE id = ?",
            (entity_id,),
        )
        if not entity_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {entity_id}",
            )

        result: dict = {
            "resolved": "entity",
            "uri": uri,
            "entity": dict(entity_rows[0]),
        }

        # Spec § 2.2 — fragment resolves to chunk in source's chunk manifest.
        # Pinpoint resolution is independent of tag/revision; both can be
        # present (entity + tagged-assertion + chunk all returned together).
        if parsed["pinpoint"] is not None:
            chunk = _resolve_pinpoint_chunk(
                conn,
                entity_id=entity_id,
                pinpoint=parsed["pinpoint"],
            )
            result["pinpoint"] = parsed["pinpoint"]
            if chunk is None:
                result["pinpoint_status"] = "pinpoint_unresolved"
            else:
                result["chunk"] = chunk
                result["verbatim"] = chunk.get("content")
                result["pinpoint_status"] = "resolved"

        # Tag-based resolution takes precedence over revision pinning
        if tag is not None:
            tag_rows = query(
                conn,
                "SELECT assertion_id FROM tag_assignments "
                "WHERE tag_name = ? AND entity_id = ?",
                (tag, entity_id),
            )
            if not tag_rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Tag {tag!r} not found for entity {entity_id}",
                )
            assertion_rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions WHERE id = ?",
                (tag_rows[0]["assertion_id"],),
            )
            if not assertion_rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Tagged assertion {tag_rows[0]['assertion_id']} no longer exists",
                )
            result["assertion"] = decode_row(assertion_rows[0], _JSON_FIELDS)
            result["resolved_via_tag"] = tag
            try:
                conn.execute(
                    "INSERT INTO entity_access_log "
                    "(entity_id, agent, operation, source) "
                    "VALUES (?, 'system', 'tag_resolve', 'tag_resolve')",
                    (entity_id,),
                )
                conn.commit()
            except Exception:
                _resolve_logger.debug(
                    "Access log insert failed for tag_resolve %s", entity_id
                )
            return result

        # Revision pinning
        if parsed["revision"] is not None:
            r = parsed["revision"]
            assertion_rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions "
                "WHERE entity_id = ? ORDER BY created_at ASC LIMIT 1 OFFSET ?",
                (entity_id, r - 1),
            )
            if not assertion_rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Revision {r} not found for {entity_id}",
                )
            result["assertion"] = decode_row(assertion_rows[0], _JSON_FIELDS)

            # Artifact dereference
            if parsed["artifact"]:
                a = result["assertion"]
                result["artifact"] = {
                    "field": parsed["artifact"],
                    "uri": a.get("artifact_uri"),
                    "storage": a.get("artifact_storage"),
                }

        return result


def _resolve_cortex_uri_impl(*, uri: str, tag: str | None = None) -> dict:
    return resolve_cortex_uri(uri=uri, tag=tag)
