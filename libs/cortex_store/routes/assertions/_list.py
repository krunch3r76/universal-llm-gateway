"""List assertions — full + compact projections.

Compact path serves boot consumers with a narrow column set; full path
applies §6.10 compaction-aware projection, near-duplicate enrichment, and
action-hint detection.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Query
from fastapi.responses import JSONResponse
from openapi_mcp.binding import x_mcp

from ...action_hints import detect_expired_unresolved
from ...compaction import (
    apply_compaction_filter,
    extract_summary_ids,
    filter_compaction_pointers,
)
from ...db import cortex_conn, decode_row, query
from ...models import (
    AssertionItem,
    AssertionList,
    AssertionListSummaryItem,
    CompactionProjection,
)
from ._list_filters import append_assertion_list_filters
from ._shared import (
    _ASSERTION_COLS,
    _ASSERTION_COMPACT_COLS,
    _ASSERTION_SUMMARY_COLS,
    _JSON_FIELDS,
    _SESSION_TAG_RE,
    _truncate_claim,
    logger,
    router,
)


def _list_assertions_compact(
    *,
    entity_id: str | None,
    entity_id_prefix: str | None,
    claim_filter: str | None,
    seeded_by: str | None,
    confidence: str | None,
    review_status: str | None,
    superseded: bool | None,
    entity_type: str | None,
    entity_type_in: list[str] | None,
    entity_type_exclude: str | None,
    valid_at: str | None,
    known_at: str | None,
    limit: int,
) -> JSONResponse:
    """Lightweight projection for boot consumers — skips the §6.10 compaction
    pipeline, enrichment action_hints, and heavy fields. Returns only the
    seven fields in `_ASSERTION_COMPACT_COLS`, with null fields omitted so
    the wire bytes match the rendered surface (thread-882 turn-12 contract).
    """
    clauses: list[str] = []
    params: list[str | int] = []
    needs_join = append_assertion_list_filters(
        clauses,
        params,
        entity_id=entity_id,
        entity_id_prefix=entity_id_prefix,
        claim_filter=claim_filter,
        seeded_by=seeded_by,
        confidence=confidence,
        review_status=review_status,
        superseded=superseded,
        entity_type=entity_type,
        entity_type_in=entity_type_in,
        entity_type_exclude=entity_type_exclude,
        valid_at=valid_at,
        known_at=known_at,
    )

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cols = ", ".join(f"a.{c.strip()}" for c in _ASSERTION_COMPACT_COLS.split(","))
    if needs_join:
        sql = (
            f"SELECT {cols} FROM assertions a "
            f"JOIN entities e ON a.entity_id = e.id{where} "
            f"ORDER BY a.created_at DESC LIMIT ?"
        )
    else:
        sql = (
            f"SELECT {cols} FROM assertions a{where} ORDER BY a.created_at DESC LIMIT ?"
        )
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    # Ship only non-null fields. Avoids ~20 B per null-field per row; with
    # AssertionItem's ~30 optional fields this is the dominant win over
    # the SELECT-level column reduction alone.
    #
    # `evidence` is SELECTed only to extract the session-id prefix the
    # briefing renderer displays as `[web-2026-04-30-0528]`, then dropped
    # from the wire payload. The derived `session_tag` carries ~25 B vs
    # evidence's 100-200+ B per row.
    items: list[dict[str, object]] = []
    for row in rows:
        evidence = row.pop("evidence", None)
        if isinstance(evidence, str):
            m = _SESSION_TAG_RE.search(evidence)
            if m:
                row["session_tag"] = m.group()
        items.append({k: v for k, v in row.items() if v is not None})
    return JSONResponse(content={"items": items})


def _list_assertions_summary(
    *,
    entity_id: str | None,
    entity_id_prefix: str | None,
    claim_filter: str | None,
    seeded_by: str | None,
    confidence: str | None,
    review_status: str | None,
    superseded: bool | None,
    entity_type: str | None,
    entity_type_in: list[str] | None,
    entity_type_exclude: str | None,
    valid_at: str | None,
    known_at: str | None,
    limit: int,
    include_compaction_pointers: bool,
) -> AssertionList:
    """Sparse projection for agent browse — skips §6.10 compaction pipeline."""
    clauses: list[str] = []
    params: list[str | int] = []
    needs_join = append_assertion_list_filters(
        clauses,
        params,
        entity_id=entity_id,
        entity_id_prefix=entity_id_prefix,
        claim_filter=claim_filter,
        seeded_by=seeded_by,
        confidence=confidence,
        review_status=review_status,
        superseded=superseded,
        entity_type=entity_type,
        entity_type_in=entity_type_in,
        entity_type_exclude=entity_type_exclude,
        valid_at=valid_at,
        known_at=known_at,
    )

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    if needs_join:
        cols = ", ".join(f"a.{c.strip()}" for c in _ASSERTION_SUMMARY_COLS.split(","))
        sql = (
            f"SELECT {cols} FROM assertions a "
            f"JOIN entities e ON a.entity_id = e.id{where} "
            f"ORDER BY a.created_at DESC LIMIT ?"
        )
    else:
        sql = (
            f"SELECT {_ASSERTION_SUMMARY_COLS} FROM assertions a{where} "
            f"ORDER BY a.created_at DESC LIMIT ?"
        )
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    if not include_compaction_pointers:
        rows, _ = filter_compaction_pointers(rows)

    items: list[AssertionListSummaryItem] = []
    for row in rows:
        d = decode_row(row, _JSON_FIELDS)
        items.append(
            AssertionListSummaryItem(
                id=d["id"],
                entity_id=d.get("entity_id"),
                claim=_truncate_claim(d.get("claim") or ""),
                confidence=d["confidence"],
                review_status=d.get("review_status"),
                derivation_type=d.get("derivation_type"),
                observed_at=d.get("observed_at"),
                superseded_by=d.get("superseded_by"),
                has_evidence_uris=bool(d.get("evidence_uris")),
                has_enrichment=bool(
                    d.get("prospective_summary")
                    or d.get("events_json")
                    or d.get("reasoning_summary")
                    or d.get("attributes")
                ),
                _deepen=f"cortex(tool=assertion_get, assertion_id={d['id']})",
            )
        )

    return AssertionList(
        items=items,
        intent="summary",
        action_hints=None,
        compaction_projection=None,
    )


@router.get("", response_model=AssertionList, openapi_extra=x_mcp("assertions"))
def list_assertions(
    entity_id: str | None = None,
    entity_id_prefix: Annotated[
        str | None,
        Query(
            description=(
                "Filter assertions whose entity_id starts with this prefix "
                "(e.g. 'service:' for friction tickets on service entities)"
            ),
        ),
    ] = None,
    claim_filter: Annotated[
        str | None,
        Query(
            description="Case-insensitive substring filter on claim text (LIKE)",
            alias="filter",
        ),
    ] = None,
    seeded_by: Annotated[
        str | None,
        Query(description="Filter to assertions seeded by this agent slug"),
    ] = None,
    confidence: str | None = None,
    review_status: str | None = None,
    superseded: bool | None = None,
    entity_type: Annotated[
        str | None,
        Query(description="Filter to assertions on entities of this type"),
    ] = None,
    entity_type_in: Annotated[
        list[str] | None,
        Query(
            description=(
                "Filter to assertions on entities whose type is in this set "
                "(positive IN; e.g. friction owner types)"
            ),
        ),
    ] = None,
    entity_type_exclude: Annotated[
        str | None,
        Query(
            description="Comma-separated entity types to exclude (e.g. 'legal_matter,person')",
        ),
    ] = None,
    valid_at: Annotated[
        str | None,
        Query(description="World-state: what was true at this date (YYYY-MM-DD)"),
    ] = None,
    known_at: Annotated[
        str | None,
        Query(description="System-state: what the DB knew at this date (YYYY-MM-DD)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    include_compaction_pointers: Annotated[
        bool,
        Query(
            description=(
                "§6.10: return the raw assertion stream including compaction-pointer "
                "rows. Default false — summaries surface first, pointers deprioritised."
            )
        ),
    ] = False,
    intent: Annotated[
        Literal["summary", "full"],
        Query(
            description=(
                "Response projection. summary (default): sparse hooks with "
                "truncated claim; full: enrichment rows with §6.10 compaction "
                "pipeline and action_hints."
            )
        ),
    ] = "summary",
    compact: Annotated[
        bool,
        Query(
            description=(
                "When true, project to the boot-rendering subset only: "
                "`id, claim, confidence, observed_at, entity_id, seeded_by, "
                "derivation_type`. Drops `evidence`, `evidence_uris`, "
                "`reasoning_summary`, `prospective_summary`, enrichment "
                "metadata, supersession chain, quality/entrenchment scores, "
                "and action hints. Also bypasses the §6.10 compaction "
                "projection (not relevant to the self-reflection boot path). "
                "Boot-internal — agents use intent=summary."
            )
        ),
    ] = False,
) -> AssertionList:
    """List assertions with entity, confidence, review_status, superseded, entity type, and temporal filters.

    When `compact=true`, bypasses the AssertionList/AssertionItem shape and
    returns a JSONResponse directly (FastAPI passes Response subclasses
    through untouched — the `response_model` on the decorator is skipped).
    Declaring `JSONResponse` in the return annotation triggers FastAPIError
    at app startup (confirmed pattern — see assertion 7951).
    """
    if compact:
        return _list_assertions_compact(
            entity_id=entity_id,
            entity_id_prefix=entity_id_prefix,
            claim_filter=claim_filter,
            seeded_by=seeded_by,
            confidence=confidence,
            review_status=review_status,
            superseded=superseded,
            entity_type=entity_type,
            entity_type_in=entity_type_in,
            entity_type_exclude=entity_type_exclude,
            valid_at=valid_at,
            known_at=known_at,
            limit=limit,
        )
    if intent == "summary":
        return _list_assertions_summary(
            entity_id=entity_id,
            entity_id_prefix=entity_id_prefix,
            claim_filter=claim_filter,
            seeded_by=seeded_by,
            confidence=confidence,
            review_status=review_status,
            superseded=superseded,
            entity_type=entity_type,
            entity_type_in=entity_type_in,
            entity_type_exclude=entity_type_exclude,
            valid_at=valid_at,
            known_at=known_at,
            limit=limit,
            include_compaction_pointers=include_compaction_pointers,
        )
    clauses: list[str] = []
    params: list[str | int] = []
    needs_join = append_assertion_list_filters(
        clauses,
        params,
        entity_id=entity_id,
        entity_id_prefix=entity_id_prefix,
        claim_filter=claim_filter,
        seeded_by=seeded_by,
        confidence=confidence,
        review_status=review_status,
        superseded=superseded,
        entity_type=entity_type,
        entity_type_in=entity_type_in,
        entity_type_exclude=entity_type_exclude,
        valid_at=valid_at,
        known_at=known_at,
    )

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    if needs_join:
        cols = ", ".join(f"a.{c.strip()}" for c in _ASSERTION_COLS.split(","))
        sql = (
            f"SELECT {cols} FROM assertions a "
            f"JOIN entities e ON a.entity_id = e.id{where} "
            f"ORDER BY a.created_at DESC LIMIT ?"
        )
    else:
        cols = _ASSERTION_COLS
        sql = f"SELECT {cols} FROM assertions a{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    items: list[AssertionItem] = []
    for row in rows:
        try:
            items.append(AssertionItem(**decode_row(row, _JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s — deserialization failed",
                row.get("id"),
                exc_info=True,
            )

    # §6.10 compaction-aware projection (Tier 0 — deterministic, no model).
    # When compaction pointers are present but the referenced consolidation
    # summary assertion is outside the LIMIT window (created before the pointers),
    # fetch it via a supplementary ID-lookup so it can surface first.
    raw_dicts = [i.model_dump(mode="json") for i in items]
    # todo:cortex-aggregate-compaction-filter — aggregate (cross-entity) reads
    # strict-exclude pointer assertions by default. Per-entity reads keep the
    # §6.10 deprioritize-not-omit contract (assertion 8212).
    aggregate_pointer_count = 0
    if entity_id is None and not include_compaction_pointers:
        raw_dicts, aggregate_pointer_count = filter_compaction_pointers(raw_dicts)
    projected_dicts, proj_meta = apply_compaction_filter(
        raw_dicts, include_compaction_pointers=include_compaction_pointers
    )
    if (
        proj_meta is not None
        and proj_meta.get("summary_count", 0) == 0
        and proj_meta.get("pointer_count", 0) > 0
        and not include_compaction_pointers
    ):
        # Summary was not captured by the LIMIT — fetch by referenced ID.
        summary_ids = extract_summary_ids(raw_dicts)
        existing_ids = {d["id"] for d in projected_dicts}
        missing_ids = [sid for sid in summary_ids if sid not in existing_ids]
        if missing_ids:
            placeholders = ",".join("?" for _ in missing_ids)
            with cortex_conn() as conn:
                summary_rows = query(
                    conn,
                    f"SELECT {_ASSERTION_COLS} FROM assertions "
                    f"WHERE id IN ({placeholders})",
                    tuple(missing_ids),
                )
            for row in summary_rows:
                try:
                    fetched = AssertionItem(**decode_row(row, _JSON_FIELDS))
                    projected_dicts.insert(0, fetched.model_dump(mode="json"))
                except Exception:
                    logger.warning(
                        "Failed to deserialise supplementary summary %s",
                        row.get("id"),
                        exc_info=True,
                    )
            # Re-apply with summaries now present.
            projected_dicts, proj_meta = apply_compaction_filter(projected_dicts)

    if proj_meta is not None:
        items = [AssertionItem(**d) for d in projected_dicts]
    elif aggregate_pointer_count > 0:
        # Strict-exclude path consumed pointer rows; re-materialize items.
        items = [AssertionItem(**d) for d in raw_dicts]

    hints = detect_expired_unresolved([i.model_dump() for i in items])
    if proj_meta is not None:
        compaction_projection = CompactionProjection(**proj_meta)
    elif aggregate_pointer_count > 0:
        compaction_projection = CompactionProjection(
            mode="aggregate_pointers_excluded",
            pointer_count=aggregate_pointer_count,
            summary_count=0,
        )
    else:
        compaction_projection = None
    return AssertionList(
        items=items,
        intent="full",
        action_hints=hints or None,
        compaction_projection=compaction_projection,
    )


__all__ = ["list_assertions"]
