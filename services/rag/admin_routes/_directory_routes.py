"""Directory-level delete route: DELETE /directory."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter
from openapi_mcp.binding import x_mcp

if TYPE_CHECKING:
    from collections.abc import Callable

    import chromadb
    from universal_event_bus import EventBus

    from services.rag.property_index import PropertyIndex

from services.rag.directory_ops import find_sources_under_prefixes
from services.rag.events.articles import rag_directory_sources_deleted
from services.rag.models import DirectoryDeleteResponse

logger = logging.getLogger(__name__)


def register_directory_routes(
    router: APIRouter,
    *,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_property_index_fn: Callable[[], PropertyIndex | None],
    get_event_bus_fn: Callable[[], EventBus | None] | None = None,
    **_kwargs: object,
) -> None:
    """Register directory-level delete route onto router."""

    @router.delete(
        "/directory",
        response_model=DirectoryDeleteResponse,
        openapi_extra=x_mcp("delete_directory", tool="rag"),
    )
    async def delete_directory(path: str) -> DirectoryDeleteResponse:
        """Remove all sources under a directory prefix from all storage surfaces."""
        prop_idx = get_property_index_fn()
        collection = get_collection_fn()
        dir_prefix = path.rstrip("/") + "/"

        sources = find_sources_under_prefixes(
            collection=collection,
            prefixes=[dir_prefix],
            list_known_sources_fn=prop_idx.list_known_sources if prop_idx else None,
        )
        if not sources:
            return DirectoryDeleteResponse(
                path=path,
                sources_deleted=0,
                chunks_deleted=0,
                fts_removed=0,
                articles_deleted=0,
            )

        total_chunks = 0
        total_fts_removed = 0
        total_articles = 0
        for source in sorted(sources):
            existing = collection.get(where={"source": source}, include=[])
            chunk_ids_: list[str] = existing.get("ids", [])
            if chunk_ids_:
                collection.delete(ids=chunk_ids_)
                total_chunks += len(chunk_ids_)
            if prop_idx is not None:
                if chunk_ids_:
                    await prop_idx.remove_properties_for_chunks(chunk_ids_)
                    total_fts_removed += await prop_idx.fts.remove_batch(chunk_ids_)
                await prop_idx.clear_failures_for(source)
                await prop_idx.remove_indexed_source(source)
                if await prop_idx.remove_article(source):
                    total_articles += 1

        logger.info(
            "Directory deleted: path=%s sources=%d chunks=%d fts=%d articles=%d",
            path,
            len(sources),
            total_chunks,
            total_fts_removed,
            total_articles,
        )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_nowait(
                rag_directory_sources_deleted(
                    path=path,
                    sources_deleted=len(sources),
                    chunks_deleted=total_chunks,
                    articles_deleted=total_articles,
                )
            )
        return DirectoryDeleteResponse(
            path=path,
            sources_deleted=len(sources),
            chunks_deleted=total_chunks,
            fts_removed=total_fts_removed,
            articles_deleted=total_articles,
        )
