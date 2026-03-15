"""Search execution for RAG query endpoints.

This module isolates hybrid retrieval flow (vector, FTS sidecar, property boost,
distance filter, and recency sort) from router wiring. API handlers call
``execute_search`` and receive the final `SearchResponse`.
"""

from __future__ import annotations

from fastapi import HTTPException

from services.rag.embeddings import EmbeddingTransientError, embed_query
from services.rag.events.query import (
    rag_scope_rejected,
    rag_scope_resolved,
    rag_search_embedding_failed,
    rag_search_executed,
    rag_search_no_results,
)
from services.rag.models import SearchRequest, SearchResponse
from services.rag.search_scope import (
    apply_bm25_sidecar,
    apply_max_distance_filter,
    apply_property_boost,
    apply_recency_sort,
    apply_source_prefix_filter_with_ids,
    resolve_scope_request,
)

from . import state


async def execute_search(request: SearchRequest) -> SearchResponse:
    """Execute hybrid retrieval over indexed chunks and return ranked search results."""
    if state._post_index_stale:
        raise HTTPException(
            status_code=503,
            detail="Post-index enrichment stale. Run: tasks/runbooks/rag-post-index-refresh.md",
        )
    original_scope = request.scope
    try:
        request = resolve_scope_request(request, state._config)
    except HTTPException as exc:
        if (
            state._event_bus is not None
            and original_scope is not None
            and exc.status_code == 400
        ):
            available_scopes = sorted(state._config.scopes) if state._config else []
            await state._event_bus.publish_async_nowait(
                rag_scope_rejected(
                    scope=original_scope,
                    reason="validation_error",
                    available=available_scopes,
                )
            )
        raise

    if state._event_bus is not None and original_scope is not None:
        resolved_prefixes = request.source_prefixes or []
        if resolved_prefixes:
            state._event_bus.publish_async_nowait(
                rag_scope_resolved(
                    scope=original_scope, prefix_count=len(resolved_prefixes)
                )
            )

    collection = state._get_collection()

    result_ids: list[str]
    chunks: list[str]
    metadatas: list[dict[str, str | int | float | bool]]
    distances: list[float]

    if request.sparse_only:
        # Skip dense embedding + vector search entirely.
        # BM25 sidecar below starts from an empty dense set and promotes all
        # FTS5 hits into the result (they land in bm25_only_ids and are fetched
        # from ChromaDB by ID). OR-joined term queries work correctly here
        # because FTS5 sees individual token matches, not an AND phrase.
        result_ids, chunks, metadatas, distances = [], [], [], []
    else:
        try:
            query_embedding = await embed_query(request.query, scope=request.scope)
        except EmbeddingTransientError as exc:
            if state._event_bus is not None:
                state._event_bus.publish_async_nowait(
                    rag_search_embedding_failed(
                        model_id=exc.model_id,
                        attempts=exc.attempts,
                        last_status=exc.last_status,
                        query_len=len(request.query),
                        scope=request.scope,
                    )
                )
            raise HTTPException(
                status_code=503,
                detail=f"Embedding model temporarily unavailable after {exc.attempts} "
                f"attempts (model={exc.model_id}, last_status={exc.last_status})",
            )

        fetch_k = request.top_k * (5 if request.source_prefixes else 3)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=fetch_k,
            include=["documents", "metadatas", "distances"],
        )

        result_ids = results["ids"][0] if results["ids"] else []
        chunks = results["documents"][0] if results["documents"] else []
        metadatas = results["metadatas"][0] if results["metadatas"] else []
        distances = results["distances"][0] if results["distances"] else []

        if result_ids:
            clean = [
                (rid, doc, meta, dist)
                for rid, doc, meta, dist in zip(
                    result_ids, chunks, metadatas, distances, strict=True
                )
                if not meta.get("is_bibliography")
            ]
            if clean:
                result_ids = [t[0] for t in clean]
                chunks = [t[1] for t in clean]
                metadatas = [t[2] for t in clean]
                distances = [t[3] for t in clean]

    result_ids, chunks, metadatas, distances = apply_source_prefix_filter_with_ids(
        ids=result_ids,
        chunks=chunks,
        metadatas=metadatas,
        distances=distances,
        source_prefixes=request.source_prefixes,
        top_k=request.top_k,
    )

    if state._property_index is not None:
        result_ids, chunks, metadatas, distances, _bm25_hits = apply_bm25_sidecar(
            ids=result_ids,
            chunks=chunks,
            metadatas=metadatas,
            distances=distances,
            query=request.query,
            fts=state._property_index.fts,
            collection=collection,
            source_prefixes=request.source_prefixes,
        )

    property_hits = 0
    if state._property_index is not None and state._config is not None:
        result_ids, chunks, metadatas, distances, property_hits = apply_property_boost(
            ids=result_ids,
            chunks=chunks,
            metadatas=metadatas,
            distances=distances,
            query=request.query,
            property_index=state._property_index,
            boost_factor=state._config.knowledge_extraction.property_boost_factor,
        )

    chunks, metadatas, distances = apply_max_distance_filter(
        chunks=chunks,
        metadatas=metadatas,
        distances=distances,
        max_distance=request.max_distance,
    )
    chunks, metadatas, distances = apply_recency_sort(
        chunks=chunks,
        metadatas=metadatas,
        distances=distances,
        recency_weight=request.recency_weight,
    )

    if state._property_index is not None and metadatas:
        unique_hashes: list[str] = [
            str(h)
            for h in {m.get("source_hash") for m in metadatas}
            if isinstance(h, str)
        ]
        if unique_hashes:
            articles = state._property_index.lookup_articles_by_hash(unique_hashes)
            for meta in metadatas:
                h = meta.get("source_hash")
                if isinstance(h, str) and h in articles:
                    entry = articles[h]
                    if entry.title:
                        meta["article_title"] = entry.title
                    if entry.authors:
                        meta["article_authors"] = entry.authors
                    if entry.venue:
                        meta["article_venue"] = entry.venue
                    if entry.published_date:
                        meta["article_published_date"] = entry.published_date
                    if entry.doi:
                        meta["article_doi"] = entry.doi

    if state._event_bus is not None:
        result_count = len(chunks)
        event = (
            rag_search_no_results(query_len=len(request.query), scope=request.scope)
            if result_count == 0
            else rag_search_executed(
                query_len=len(request.query),
                top_k=request.top_k,
                results=result_count,
                scope=request.scope,
            )
        )
        state._event_bus.publish_async_nowait(event)

    return SearchResponse(
        chunks=chunks,
        metadata=metadatas,
        distances=distances,
        property_hits=property_hits,
    )
