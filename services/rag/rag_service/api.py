"""FastAPI router definitions for the RAG service.

This module exposes API endpoints and admin routes while delegating heavy
business logic to sibling modules. Shared mutable resources are accessed through
the central ``state`` module.
"""

from __future__ import annotations

import logging

import chromadb
from fastapi import APIRouter

from services.rag.admin_routes import register_admin_routes
from services.rag.events.query import rag_scopes_listed
from services.rag.models import (
    ChunkByIndexItem,
    ChunksByIndexRequest,
    ChunksByIndexResponse,
    FailedChunkItem,
    FailedExtractionResponse,
    ScopeInfo,
    ScopesResponse,
    SearchRequest,
    SearchResponse,
)
from services.rag.search_scope import require_loaded_config

from . import indexing, search, state

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search_endpoint(request: SearchRequest) -> SearchResponse:
    """Execute RAG search request and return ranked chunks."""
    return await search.execute_search(request)


@router.post("/chunks_by_index", response_model=ChunksByIndexResponse)
async def chunks_by_index(request: ChunksByIndexRequest) -> ChunksByIndexResponse:
    """Fetch specific chunks by source path and chunk indices."""
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

        ids = raw.get("ids") or []
        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        # Add type hints for clarity, assuming these are lists of str/dict
        # ids: list[str] = raw.get("ids") or []
        # docs: list[str] = raw.get("documents") or []
        # metas: list[dict] = raw.get("metadatas") or []
        requested_set = set(group.chunk_indices)
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


@router.get("/scopes", response_model=ScopesResponse)
async def get_scopes() -> ScopesResponse:
    """Return configured search scopes and their source-prefix mappings."""
    loaded_config = require_loaded_config(state._config)
    if state._event_bus is not None:
        await state._event_bus.publish_async_nowait(
            rag_scopes_listed(count=len(loaded_config.scopes))
        )
    return ScopesResponse(
        scopes={
            name: ScopeInfo(prefixes=scope.prefixes, description=scope.description)
            for name, scope in loaded_config.scopes.items()
        },
    )


@router.get("/extraction/failed", response_model=FailedExtractionResponse)
def get_failed_extractions(source: str | None = None) -> FailedExtractionResponse:
    """Return extraction-failure records, optionally narrowed to one source."""
    if state._property_index is None:
        return FailedExtractionResponse(total=0, chunks=[])
    records = state._property_index.get_failed_chunks(source=source)
    return FailedExtractionResponse(
        total=len(records),
        chunks=[
            FailedChunkItem(
                chunk_id=r.chunk_id,
                source=r.source,
                error=r.error,
                attempt_count=r.attempt_count,
                recorded_at=r.recorded_at,
            )
            for r in records
        ],
    )


def _set_collection(col: chromadb.Collection) -> None:
    """Swap collection instance for admin operations and tests."""
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
)
router.include_router(_admin_router)
