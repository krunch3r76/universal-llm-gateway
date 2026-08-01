"""FastAPI router definitions for the RAG service.

This module exposes API endpoints and admin routes while delegating heavy
business logic to sibling modules. Shared mutable resources are accessed through
the central ``state`` module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from openapi_mcp.binding import x_mcp

from services.rag.admin_routes import register_admin_routes
from services.rag.embeddings import get_model_id as get_embed_model_id
from services.rag.events.query import rag_scopes_listed
from services.rag.models import (
    ChunkByIndexItem,
    ChunksByIndexRequest,
    ChunksByIndexResponse,
    EmbedBatchRequest,
    EmbedBatchResponse,
    ExtractionQueueBreakdownModel,
    ExtractionQueueResponse,
    ExtractionQueueRowModel,
    FailedChunkItem,
    FailedExtractionResponse,
    ScopeInfo,
    ScopeRegisterRequest,
    ScopeRegisterResponse,
    ScopesResponse,
    SearchRequest,
    SearchResponse,
)
from services.rag.search_scope import require_loaded_config

from . import indexing, search, state

if TYPE_CHECKING:
    import chromadb

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    """Return liveness plus dependency and extraction-queue state."""
    collection = state._collection
    activation = state._dependency_activation
    queue_breakdown = (
        state._property_index.get_extraction_queue_breakdown()
        if state._property_index is not None
        else None
    )
    return {
        "status": "ok" if activation.phase == "ready" else "degraded",
        "phase": activation.phase,
        "waiting_on": activation.waiting_on,
        "activation_attempts": activation.attempts,
        "last_error": activation.last_error,
        "collection": state.COLLECTION_NAME if collection is not None else None,
        "collection_count": collection.count() if collection is not None else 0,
        "property_index": "ready"
        if state._property_index is not None
        else "unavailable",
        "embedding_model": get_embed_model_id() or "unconfigured",
        "watcher": "running" if state._watcher_manager is not None else "inactive",
        "extraction_queue": {
            "total": queue_breakdown.total if queue_breakdown is not None else 0,
            "ready": queue_breakdown.ready if queue_breakdown is not None else 0,
            "in_flight": queue_breakdown.in_flight
            if queue_breakdown is not None
            else 0,
            "cooling_off": queue_breakdown.cooling_off
            if queue_breakdown is not None
            else 0,
            "capacity_blocked": queue_breakdown.capacity_blocked
            if queue_breakdown is not None
            else 0,
            "exhausted": queue_breakdown.exhausted
            if queue_breakdown is not None
            else 0,
        },
    }


@router.post("/search", response_model=SearchResponse)
async def search_endpoint(request: SearchRequest) -> SearchResponse:
    """Execute the RAG retrieval flow and return ranked chunk payloads.

    Delegates search execution to the shared search module so endpoint behavior
    remains a thin transport boundary over core retrieval logic.
    """
    return await search.execute_search(request)


@router.post("/embed_batch", response_model=EmbedBatchResponse)
async def embed_batch(request: EmbedBatchRequest) -> EmbedBatchResponse:
    """Batch-embed multiple query texts in a single GPU forward pass.

    Pipeline handlers call this once before dispatching individual /search
    calls with pre-computed embeddings, eliminating per-query embedding
    latency (N sequential forward passes → 1 batch forward pass).
    """
    from services.rag.embeddings import EmbeddingTransientError, embed_queries_batch

    try:
        embeddings = await embed_queries_batch(request.texts, scope=request.scope)
    except EmbeddingTransientError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Batch embedding failed: {exc}",
        )
    return EmbedBatchResponse(embeddings=embeddings)


@router.post("/chunks_by_index", response_model=ChunksByIndexResponse)
async def chunks_by_index(request: ChunksByIndexRequest) -> ChunksByIndexResponse:
    """Fetch an explicit set of chunk positions for one or more sources.

    This endpoint is used by downstream workflows that need deterministic
    source/index addressing rather than semantic top-k retrieval.
    """
    collection = state._get_collection()
    results: list[ChunkByIndexItem] = []

    for group in request.groups:
        if not group.chunk_indices:
            continue
        where_filter: dict[str, object] = {
            "$and": [
                {"source": group.source},
                {"chunk_index": {"$in": group.chunk_indices}},
            ]
        }
        try:
            raw = collection.get(
                where=where_filter,
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.error(
                "chunks_by_index: failed for source=%s: %s",
                group.source,
                e,
                exc_info=True,
            )
            raise

        ids: list[str] = raw.get("ids") or []
        docs: list[str] = raw.get("documents") or []
        metas: list[dict] = raw.get("metadatas") or []
        requested_set = set(group.chunk_indices)
        if not (len(ids) == len(docs) == len(metas)):
            logger.warning(
                "Mismatched lengths from chromadb.get for source=%s: ids=%d, docs=%d, metas=%d",
                group.source,
                len(ids),
                len(docs),
                len(metas),
            )
            # Depending on desired behavior, could raise, skip, or try to continue with min length
            # For now, we'll let zip(strict=True) handle it, but with a warning.
        for chunk_id, doc, meta in zip(ids, docs, metas, strict=True):
            idx = meta.get("chunk_index")
            if idx is not None and int(idx) in requested_set:
                results.append(
                    ChunkByIndexItem(
                        chunk_id=chunk_id,
                        source=group.source,
                        chunk_index=int(idx),
                        text=doc or "",
                        metadata=meta,
                    )
                )

    return ChunksByIndexResponse(chunks=results)


@router.get(
    "/scopes",
    response_model=ScopesResponse,
    openapi_extra=x_mcp("list_scopes", tool="rag"),
)
async def get_scopes() -> ScopesResponse:
    """Return configured search scopes and their source-prefix mappings."""
    loaded_config = require_loaded_config(state._config)
    if state._event_bus is not None:
        await state._event_bus.publish_nowait(
            rag_scopes_listed(count=len(loaded_config.scopes))
        )
    enrichment: dict[str, dict[str, object]] = {}
    if state._property_index is not None:
        enrichment = state._property_index.scope_list_enrichment()
    return ScopesResponse(
        scopes={
            name: ScopeInfo(
                prefixes=scope.prefixes,
                description=scope.description,
                article_count=int(
                    enrichment.get(name, {}).get("article_count", 0) or 0
                ),
                top_topics=list(enrichment.get(name, {}).get("top_topics") or []),
            )
            for name, scope in loaded_config.scopes.items()
        },
    )


@router.post("/scopes", response_model=ScopeRegisterResponse, status_code=201)
async def register_scope(request: ScopeRegisterRequest) -> ScopeRegisterResponse:
    """Register a new retrieval scope at runtime.

    Adds the scope to the live config (immediately queryable), optionally
    starts file watchers for the prefixes, and persists to rag.yaml so
    the scope survives restarts.  Returns 409 if the scope already exists
    unless ``force`` is set.
    """
    from services.rag.config import ScopeDefinition, WatchDirectory, save_scope

    loaded_config = require_loaded_config(state._config)
    already_exists = request.name in loaded_config.scopes
    if already_exists and not request.force:
        raise HTTPException(
            status_code=409,
            detail=f"Scope '{request.name}' already exists. Set force=true to overwrite.",
        )

    scope_def = ScopeDefinition(
        prefixes=request.prefixes, description=request.description
    )
    loaded_config.scopes[request.name] = scope_def

    watching: list[str] = []
    if request.watch and state._watcher_manager is not None:
        for pfx in request.prefixes:
            wd = WatchDirectory(path=pfx)
            started = await state._watcher_manager.register_directory(wd)
            if started:
                watching.append(pfx)

    save_scope(
        request.name,
        request.prefixes,
        request.description,
        watch=request.watch,
    )

    logger.info(
        "Scope registered: name=%s prefixes=%s watch=%s watching=%s",
        request.name,
        request.prefixes,
        request.watch,
        watching,
    )
    return ScopeRegisterResponse(
        name=request.name,
        created=not already_exists,
        watching=watching,
    )


@router.get("/extraction/queue", response_model=ExtractionQueueResponse)
def get_extraction_queue(limit: int = 100) -> ExtractionQueueResponse:
    """Return extraction queue buckets and source-level rows."""
    if state._property_index is None:
        return ExtractionQueueResponse(
            breakdown=ExtractionQueueBreakdownModel(
                total=0,
                ready=0,
                in_flight=0,
                cooling_off=0,
                capacity_blocked=0,
                exhausted=0,
            ),
            rows=[],
        )

    breakdown = state._property_index.get_extraction_queue_breakdown()
    rows = state._property_index.list_extraction_queue_rows(limit=limit)
    return ExtractionQueueResponse(
        breakdown=ExtractionQueueBreakdownModel(
            total=breakdown.total,
            ready=breakdown.ready,
            in_flight=breakdown.in_flight,
            cooling_off=breakdown.cooling_off,
            capacity_blocked=breakdown.capacity_blocked,
            exhausted=breakdown.exhausted,
        ),
        rows=[
            ExtractionQueueRowModel(
                source=row.source,
                queued_at=row.queued_at,
                attempts=row.attempts,
                last_attempt_at=row.last_attempt_at,
                last_error=row.last_error,
                last_error_type=row.last_error_type,
                last_failure_category=row.last_failure_category,
                last_failure_at=row.last_failure_at,
                state=row.state,
            )
            for row in rows
        ],
    )


@router.get("/extraction/failed", response_model=FailedExtractionResponse)
def get_failed_extractions(source: str | None = None) -> FailedExtractionResponse:
    """Return persisted extraction-failure records for observability and recovery.

    When `source` is provided, results are restricted to that document path;
    otherwise all known failed extraction rows are returned.
    """
    records = (
        state._property_index.get_failed_chunks(source=source)
        if state._property_index is not None
        else []
    )
    return FailedExtractionResponse(
        total=len(records),
        chunks=[
            FailedChunkItem(
                chunk_id=r.chunk_id,
                source=r.source,
                error=r.error,
                parse_failure_reason=r.parse_failure_reason,
                attempt_count=r.attempt_count,
                recorded_at=r.recorded_at,
            )
            for r in records
        ],
    )


def _set_collection(col: chromadb.Collection) -> None:
    """Replace the active collection reference for admin mutations and tests.

    This helper centralizes collection swapping so route wiring can inject
    replacement collections without importing mutable state directly. It is
    primarily used in testing scenarios or during administrative operations
    where the underlying ChromaDB collection needs to be swapped out dynamically.
    """
    state._collection = col


_admin_router = register_admin_routes(
    index_file_fn=indexing._index_file,
    get_collection_fn=state._get_collection,
    get_watcher_manager_fn=lambda: state._watcher_manager,
    get_chroma_fn=lambda: state._chroma,
    set_collection_fn=_set_collection,
    collection_name=state.COLLECTION_NAME,
    get_property_index_fn=lambda: state._property_index,
    get_event_bus_fn=lambda: state._event_bus,
    get_config_fn=lambda: state._config,
    refresh_article_registry_from_row_fn=state.refresh_article_registry_from_row,
    reconcile_article_registry_delete_fn=state.reconcile_article_registry_delete,
)
router.include_router(_admin_router)
