"""Entity HTTP routes (Cortex v2.4).

Thin FastAPI handlers — CRUD logic lives in ``cortex_store.entity_crud`` and
card-mode read in ``cortex_store.card``. This module is the HTTP surface
only; dispatch ops and tests should import from the impl modules directly.
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request, status

from ..card import (
    CARD_INTENTS_DEFERRED,
    CARD_TOP_K_DEFAULT,
    get_entity_card,
)
from ..db import cortex_conn
from ..entity_crud import (
    create_entity_impl,
    list_entities_impl,
    update_entity_impl,
)
from ..entity_read import get_entity_impl
from ..models import (
    EntityCreate,
    EntityDetail,
    EntityIntent,
    EntityList,
    EntitySummary,
    EntityUpdate,
)

# Re-exports for back-compat with callers that imported the underscored names
# from this module before the v2.4 Slice 2 split.
_CARD_TOP_K_DEFAULT = CARD_TOP_K_DEFAULT
_CARD_INTENTS_DEFERRED = CARD_INTENTS_DEFERRED
_list_entities_impl = list_entities_impl
_get_entity_impl = get_entity_impl
_get_entity_card_impl = get_entity_card
_update_entity_impl = update_entity_impl
_create_entity_impl = create_entity_impl

logger = logging.getLogger("cortex-api.entities")
router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("", response_model=EntityList)
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
            "attribute contains either `*` (universal) or this agent "
            "slug. Entities without the attribute are treated as "
            "universal."
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
) -> EntityList:
    """List entities, optionally constrained to one entity type / workflow_state."""
    with cortex_conn() as conn:
        data = list_entities_impl(
            conn,
            entity_type=type,
            workflow_state=workflow_state,
            limit=limit,
            for_agent=for_agent,
            query=query,
        )
    return EntityList(items=[EntitySummary(**item) for item in data["items"]])


@router.get("/{entity_id}")
def get_entity(
    entity_id: str,
    request: Request,
    intent: EntityIntent = Query(
        "full",
        description=(
            "v2.4 §6.1 read intent. `full` (default) preserves the existing "
            "EntityDetail payload. `card` returns Card v0 (§6.3) via a "
            "projection-aware fetch plan. `cluster` and `impact` are "
            "reserved in the surface but not implemented yet — calls "
            "return 501."
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
            "Tunable; default 7. Applies to intent=card only."
        ),
    ),
) -> dict[str, object]:
    """Fetch one entity at the requested intent."""
    source = request.headers.get("x-cortex-source", "agent")
    agent = request.headers.get("x-cortex-agent", "web")
    session_id = request.headers.get("x-cortex-session")
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
        )


@router.patch("/{entity_id}", response_model=EntityDetail)
def update_entity(entity_id: str, body: EntityUpdate) -> EntityDetail:
    """Update mutable fields on an entity.

    Uses ``model_fields_set`` so omitted keys are untouched while explicitly
    sending ``null`` clears the field (sets it to SQL NULL).
    """
    updates = {field: getattr(body, field) for field in body.model_fields_set}
    with cortex_conn() as conn:
        result = update_entity_impl(conn, entity_id=entity_id, updates=updates)
    return EntityDetail(**result)


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
            result = create_entity_impl(conn, body.model_dump())
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
    return EntityDetail(**result)
