"""Entity HTTP routes (Cortex v2.4).

Thin FastAPI handlers — CRUD logic lives in ``cortex_store.entity_crud`` and
card-mode read in ``cortex_store.card``. This module is the HTTP surface
only; dispatch ops and tests should import from the impl modules directly.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from universal_logging import get_logger

from ..card import (
    CARD_INTENTS_DEFERRED,
    CARD_TOP_K_DEFAULT,
    get_entity_card,
)
from ..db import WRITE_LOCK, cortex_conn
from ..db import query as db_query
from ..entity_collision import attach_collision_warning, check_entity_collision
from ..entity_crud import (
    create_entity_impl,
    list_entities_impl,
    update_entity_impl,
)
from ..entity_read import get_entity_impl
from ..entity_rekey import entity_merge_impl, entity_rekey_impl
from ..models import (
    EntityCreate,
    EntityDetail,
    EntityIntent,
    EntityList,
    EntitySummary,
    EntityUpdate,
)

logger = get_logger("cortex-api.entities")
router = APIRouter(prefix="/entities", tags=["entities"])


def _resolve_entity_get_historical(
    *,
    intent: EntityIntent,
    include_superseded: bool,
    include_superseded_present: bool,
) -> bool:
    if intent == "full-historical" and include_superseded_present:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid combo: intent='full-historical' with include_superseded "
                "— use intent='full-historical' alone (canonical audit path) or "
                "intent='full' with include_superseded=true (legacy alias)."
            ),
        )
    if include_superseded and intent in {"card", "card-md", "cluster", "impact"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid combo: intent={intent!r} with include_superseded=true "
                "— include_superseded applies only to intent='full' or "
                "intent='full-historical'."
            ),
        )
    return intent == "full-historical" or (intent == "full" and include_superseded)


class EntityRekeyRequest(BaseModel):
    new_id: str


class EntityMergeRequest(BaseModel):
    source_id: str
    target_id: str


class SourcePathsResponse(BaseModel):
    """Resolved absolute filesystem paths of every entity carrying a source_uri.

    Consumed by the RAG EntityAdmissionGate (plan:rag-entity-gated-indexing).
    `unresolved` counts source_uris that could not be resolved to a path
    (e.g. a cortex:// URI to a missing entity) — they are simply not admitted.
    """

    paths: list[str]
    count: int
    unresolved: int = 0


@router.get("")
def list_entities(
    type: str | None = None,
    workflow_state: str | None = Query(
        None,
        description="Filter by typed workflow_state column (replaces the "
        "json_extract(attributes,'$.status') pattern).",
    ),
    limit: int = Query(50, ge=1, le=500),
    for_agent: str | None = Query(
        None,
        description=(
            "Filter to entities whose `applicable_agents` JSON-list "
            "attribute contains either `*` (universal) or this seat slug. "
            "For `agent_skill`/`skill` rows, `applicable_agents` is "
            "informational metadata only and does NOT filter. Other entity "
            "types retain the seat-slug filter with NULL → fail-open "
            "universal semantics."
        ),
    ),
    query: str | None = Query(
        None,
        description=(
            "Case-insensitive substring filter on entity `id` and `name`. "
            "Composes with `type`, `workflow_state`, and `for_agent`. "
            "Whitespace-only values are treated as absent."
        ),
    ),
    content_hash: str | None = Query(
        None,
        description=(
            "Exact-match filter on the `content_hash` column. Accepts raw hex "
            "or `sha256:<hex>` form; the prefix is stripped before matching."
        ),
    ),
    fields: str | None = Query(
        None,
        description=(
            "Comma-separated field projection. Base columns pass through; "
            "other names resolve from the attributes JSON blob (e.g. "
            "applicable_agents). Returns raw dict rows, not EntitySummary."
        ),
    ),
    include_non_active: bool = Query(
        False,
        description=(
            "Maintenance/debug: include inactive agent_skill rows "
            "(draft, deprecated, retired, merged, NULL lifecycle). "
            "Default listing returns only lifecycle=active skills. "
            "Not a security boundary."
        ),
    ),
) -> EntityList | dict[str, object]:
    """List entities, optionally constrained to one entity type / workflow_state."""
    field_list: list[str] | None = None
    if fields is not None:
        field_list = [part.strip() for part in fields.split(",") if part.strip()]
    with cortex_conn() as conn:
        data = list_entities_impl(
            conn,
            entity_type=type,
            workflow_state=workflow_state,
            limit=limit,
            for_agent=for_agent,
            query=query,
            content_hash=content_hash,
            fields=field_list,
            include_non_active=include_non_active,
        )
    if field_list:
        return data
    return EntityList(items=[EntitySummary(**item) for item in data["items"]])


@router.get("/source-paths", response_model=SourcePathsResponse)
def list_entity_source_paths() -> SourcePathsResponse:
    """Resolved absolute paths of every entity that carries a source_uri.

    cortex-api owns the entities table and imports the canonical resolver
    (_source_uri_to_absolute_path), keeping _FILES_ROOT / _WORKSPACES_ROOT
    authoritative server-side; RAG never opens cortex.db ([universal:rest]).
    Paths are deduplicated — multiple entities resolving to the same path
    collapse to one set member. An unresolvable source_uri is skipped (counted
    under `unresolved`) rather than failing the whole snapshot, so the
    consuming gate stays fail-closed.
    """
    from ..rag_resolver import _source_uri_to_absolute_path

    with cortex_conn() as conn:
        rows = db_query(
            conn,
            "SELECT source_uri FROM entities "
            "WHERE source_uri IS NOT NULL AND TRIM(source_uri) != ''",
        )

    paths: set[str] = set()
    unresolved = 0
    for row in rows:
        source_uri = row.get("source_uri")
        if not isinstance(source_uri, str) or not source_uri.strip():
            continue
        try:
            paths.add(_source_uri_to_absolute_path(source_uri.strip()))
        except Exception as exc:
            logger.warning(
                "source-paths: unresolvable source_uri=%r: %s", source_uri, exc
            )
            unresolved += 1
    return SourcePathsResponse(
        paths=sorted(paths), count=len(paths), unresolved=unresolved
    )


@router.get("/{entity_id}")
def get_entity(
    entity_id: str,
    request: Request,
    intent: EntityIntent = Query(
        "card",
        description=(
            "v2.4 §6.1 read intent. `card` (default) returns Card v0 (§6.3). "
            "`full` returns EntityDetail with active assertions plus "
            "superseded breadcrumb/corrections. "
            "`full-historical` returns all superseded rows with full enrichment "
            "(audit escape hatch). `card-md` "
            "returns a comprehension-first markdown render (root-only). "
            "`cluster` and `impact` are reserved — calls return 501."
        ),
    ),
    include_superseded: bool = Query(
        False,
        description=(
            "Legacy alias: when intent='full', set true to inline all superseded "
            "rows with full enrichment (same as intent='full-historical'). "
            "Prefer intent='full-historical'. Invalid with intent='full-historical' "
            "or intent in {card, card-md, cluster, impact}."
        ),
    ),
    include_edges: bool = Query(
        False, description="Include reasoning edges from session_edges (full only)"
    ),
    edge_limit: int = Query(
        20, ge=1, le=100, description="Max reasoning edges to return"
    ),
    include_compaction_pointers: bool = Query(
        False,
        description=(
            "§6.10: return the raw assertion stream including compaction-pointer "
            "rows. Default false — summaries surface first, pointers deprioritised."
        ),
    ),
    debug: bool = Query(
        False,
        description=(
            "§7.8 observability: when intent=card, attach a `debug` block "
            "exposing `fetch_plan_row_volume` so callers can verify card "
            "mode is executing a projection-aware fetch (§6.2)."
        ),
    ),
    top_k: int = Query(
        CARD_TOP_K_DEFAULT,
        ge=1,
        le=50,
        description=(
            "v2.4 §6.3: number of top-K active assertions in Card v0 payload. "
            "Tunable; default 7. Applies to intent=card and intent=card-md."
        ),
    ),
) -> dict[str, object] | str:
    """Fetch one entity at the requested intent."""
    source = request.headers.get("x-cortex-source", "agent")
    agent = request.headers.get("x-cortex-agent", "web")
    session_id = request.headers.get("x-cortex-session")
    include_superseded_present = "include_superseded" in request.query_params
    historical = _resolve_entity_get_historical(
        intent=intent,
        include_superseded=include_superseded,
        include_superseded_present=include_superseded_present,
    )
    if intent in CARD_INTENTS_DEFERRED:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": f"intent={intent!r} reserved but not implemented yet",
                "supported_intents": ["full", "card"],
                "reference": "cortex-v2.4 §6.1, §7.1, §7.3",
            },
        )
    with cortex_conn() as conn:
        if intent == "card-md":
            from ..subgraph_template import render_root_card_markdown

            return render_root_card_markdown(
                conn,
                entity_id=entity_id,
                top_k=top_k,
                source=source,
                agent=agent,
                session_id=session_id,
            )
        if intent == "card":
            return get_entity_card(
                conn,
                entity_id=entity_id,
                top_k=top_k,
                debug=debug,
                source=source,
                agent=agent,
                session_id=session_id,
            )
        return get_entity_impl(
            conn,
            entity_id=entity_id,
            include_edges=include_edges,
            edge_limit=edge_limit,
            source=source,
            agent=agent,
            session_id=session_id,
            include_compaction_pointers=include_compaction_pointers,
            include_superseded=historical,
        )


@router.patch("/{entity_id}", response_model=None)
def update_entity(
    entity_id: str,
    body: EntityUpdate,
    intent: EntityIntent = Query(
        "full",
        description=(
            "Post-update read-back shape. `full` (default) returns the legacy "
            "EntityDetail echo. `card` returns bounded Card v0 (opt-in)."
        ),
    ),
) -> dict[str, object]:
    """Update mutable fields on an entity.

    Uses ``model_fields_set`` so omitted keys are untouched while explicitly
    sending ``null`` clears the field (sets it to SQL NULL).
    """
    if intent in CARD_INTENTS_DEFERRED:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": f"intent={intent!r} reserved but not implemented yet",
                "supported_intents": ["full", "card"],
                "reference": "cortex-v2.4 §6.1, §7.1, §7.3",
            },
        )
    updates = {field: getattr(body, field) for field in body.model_fields_set}
    with cortex_conn() as conn:
        result = update_entity_impl(
            conn, entity_id=entity_id, updates=updates, intent=intent
        )
    return result


@router.post("/merge")
def merge_entities(body: EntityMergeRequest) -> dict[str, object]:
    """Fold source entity into target with dedup-before-repoint semantics."""
    with WRITE_LOCK, cortex_conn() as conn:
        return entity_merge_impl(conn, body.source_id, body.target_id)


@router.post("/{old_id}/rekey")
def rekey_entity(old_id: str, body: EntityRekeyRequest) -> dict[str, object]:
    """Identity-preserving relabel: old_id becomes an alias of new_id."""
    with WRITE_LOCK, cortex_conn() as conn:
        return entity_rekey_impl(conn, old_id, body.new_id)


@router.post("", response_model=EntityDetail, status_code=status.HTTP_201_CREATED)
def create_entity(body: EntityCreate) -> EntityDetail:
    """Create an entity and return the stored entity detail payload.

    Failure modes are explicitly disambiguated so callers can react correctly:
      - 409 Conflict: duplicate ID (caller error, ¬retryable)
      - 503 Service Unavailable: transient sqlite degradation (retryable)
      - 500: unknown structural failure (fall-through)
    """
    with cortex_conn() as conn:
        try:
            result = create_entity_impl(conn, body.model_dump(exclude_unset=True))
        except sqlite3.IntegrityError:
            logger.warning("Entity create conflict for id=%s", body.id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "detail": f"Entity already exists: {body.id}",
                    "retryable": False,
                },
            )
        except sqlite3.OperationalError as exc:
            logger.error(
                "Entity create transient cortex degradation for id=%s: %s",
                body.id,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "detail": f"Cortex temporarily unavailable: {exc}",
                    "retryable": True,
                },
            )
        try:
            collision = check_entity_collision(
                conn,
                entity_id=body.id,
                entity_type=body.type,
                name=body.name,
                description=body.description,
            )
            if collision is not None:
                attach_collision_warning(result, collision)
        except Exception:
            logger.warning(
                "Entity create collision_warning failed for %s — proceeding",
                body.id,
                exc_info=True,
            )
    return EntityDetail(**result)
