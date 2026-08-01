"""Article metadata and source deletion routes."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query
from openapi_mcp.binding import x_mcp

if TYPE_CHECKING:
    from collections.abc import Callable

    import chromadb
    from universal_event_bus import EventBus

    from services.rag.property_index import PropertyIndex

from services.rag.admin_routes._directory_routes import register_directory_routes
from services.rag.admin_routes._helpers import (
    ArticleRow,
    OrphanedArticlesResponse,
    _get_pipeline_stage,
)
from services.rag.events.articles import rag_article_upserted, rag_source_deleted
from services.rag.models import (
    ArticleListingItem,
    ArticleListingResponse,
    ArticleUpsertRequest,
    ArticleUpsertResponse,
    RefreshCorpusHintsRequest,
    RefreshCorpusHintsResponse,
    SourceDeleteResponse,
)

logger = logging.getLogger(__name__)


def _parse_scope_filter(scope: str | None) -> list[str]:
    if scope is None:
        return []
    normalized = [token.strip() for token in scope.split(",") if token.strip()]
    return list(dict.fromkeys(normalized))


def register_article_routes(
    router: APIRouter,
    *,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_property_index_fn: Callable[[], PropertyIndex | None],
    get_event_bus_fn: Callable[[], EventBus | None] | None = None,
    refresh_article_registry_from_row_fn: Callable[[ArticleRow | None], None]
    | None = None,
    reconcile_article_registry_delete_fn: Callable[[str, ArticleRow | None], None]
    | None = None,
    **_kwargs: object,
) -> None:
    """Register article metadata and source deletion routes onto router."""

    @router.get(
        "/articles",
        response_model=ArticleListingResponse,
        response_model_exclude_none=True,
    )
    def list_articles(
        scope: str | None = Query(default=None),
        include_abstract: bool = Query(default=False),
    ) -> ArticleListingResponse:
        """List structured article metadata with optional scope and abstract filters."""
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")

        scopes = _parse_scope_filter(scope)
        select_cols = [
            "source_path",
            "filename",
            "title",
            "authors",
            "venue",
            "published_date",
            "doi",
            "scope",
            "comments",
            "updated_at",
        ]
        if include_abstract:
            select_cols.append("abstract")

        query = f"SELECT {', '.join(select_cols)} FROM articles"
        params: tuple[str, ...] = ()
        if scopes:
            placeholders = ", ".join("?" for _ in scopes)
            query += f" WHERE scope IN ({placeholders})"
            params = tuple(scopes)
        query += (
            " ORDER BY scope ASC, published_date DESC, filename ASC, source_path ASC"
        )

        conn = prop_idx._ensure_conn()
        try:
            rows = conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            logger.error("Article listing query failed: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=500, detail="Failed to query article metadata"
            ) from exc

        articles = [
            ArticleListingItem(
                source_path=row["source_path"] or "",
                filename=row["filename"] or "",
                title=row["title"] or "",
                authors=row["authors"] or "",
                venue=row["venue"] or "",
                published_date=row["published_date"] or "",
                doi=row["doi"] or "",
                scope=row["scope"] or "",
                comments=row["comments"] or "",
                updated_at=row["updated_at"] or "",
                abstract=(row["abstract"] or "") if include_abstract else None,
            )
            for row in rows
        ]
        return ArticleListingResponse(
            articles=articles,
            count=len(articles),
            scopes_queried=scopes,
        )

    @router.get(
        "/orphaned_articles",
        response_model=OrphanedArticlesResponse,
        openapi_extra=x_mcp("orphaned_articles", tool="rag"),
    )
    def get_orphaned_articles() -> OrphanedArticlesResponse:
        """Return articles that have no corresponding indexed chunks."""
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            return {"orphans": [], "count": 0}
        conn = prop_idx._ensure_conn()
        rows = conn.execute(
            "SELECT a.source_path, a.title, a.scope, a.updated_at "
            "FROM articles a "
            "LEFT JOIN indexed_sources s ON a.source_path = s.source "
            "WHERE s.source IS NULL "
            "ORDER BY a.updated_at DESC"
        ).fetchall()
        return {
            "orphans": [
                {"source_path": r[0], "title": r[1], "scope": r[2], "updated_at": r[3]}
                for r in rows
            ],
            "count": len(rows),
        }

    @router.post(
        "/refresh_corpus_hints",
        response_model=RefreshCorpusHintsResponse,
        openapi_extra=x_mcp("refresh_hints", tool="rag"),
    )
    async def refresh_corpus_hints(
        request: RefreshCorpusHintsRequest,
    ) -> RefreshCorpusHintsResponse:
        """Refresh corpus hints, optionally for a single scope with tuning params."""
        from services.rag.config import load_config
        from services.rag.corpus_hints import update_corpus_hints
        from services.rag.vocabulary._scope_helpers import configured_scopes_map

        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")
        eb = get_event_bus_fn() if get_event_bus_fn else None

        bl_override: frozenset[str] | None = None
        if request.blocklist_override is not None:
            bl_override = frozenset(t.lower() for t in request.blocklist_override)
        extra_bl: frozenset[str] = frozenset()
        if request.extra_blocklist:
            extra_bl = frozenset(t.lower() for t in request.extra_blocklist)

        # Match CLI / freshness-repair: prefix-match via configured scopes so
        # umbrella scopes refresh correctly (leaf files, not stored scope col).
        cs_map = configured_scopes_map(load_config())
        if request.scope is not None and request.scope not in cs_map:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scope '{request.scope}' (not in rag.yaml)",
            )

        result = await update_corpus_hints(
            prop_idx,
            scope=request.scope,
            configured_scopes=cs_map,
            entity_boost_hyphen=request.entity_boost_hyphen,
            entity_boost_single=request.entity_boost_single,
            blocklist_override=bl_override,
            extra_blocklist=extra_bl,
            event_bus=eb,
        )
        if result:
            await prop_idx.stamp_watermark("corpus_hints")
        terms_by_scope = {
            s: len([t for t in (csv or "").split(",") if t.strip()])
            for s, csv in result.items()
        }
        logger.info(
            "Corpus hints refreshed: scope=%s scopes_updated=%s",
            request.scope or "(all)",
            sorted(result),
        )
        return RefreshCorpusHintsResponse(
            scopes_updated=sorted(result),
            terms_by_scope=terms_by_scope,
        )

    @router.post(
        "/article",
        response_model=ArticleUpsertResponse,
        openapi_extra=x_mcp("upsert_article", tool="rag"),
    )
    async def upsert_article(request: ArticleUpsertRequest) -> ArticleUpsertResponse:
        """Insert or update an article metadata row (merge semantics for empty fields)."""
        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(status_code=503, detail="Property index not available")
        filename = request.filename or Path(request.source_path).name
        created = await prop_idx.upsert_article(
            source_path=request.source_path,
            filename=filename,
            title=request.title,
            authors=request.authors,
            venue=request.venue,
            published_date=request.published_date,
            doi=request.doi,
            abstract=request.abstract,
            content_hash=request.content_hash,
            subdirectory=request.subdirectory,
            scope=request.scope,
        )
        row = prop_idx.get_article_row(request.source_path)
        if refresh_article_registry_from_row_fn is not None:
            refresh_article_registry_from_row_fn(row)
        stage, queue_state, queue_depth = _get_pipeline_stage(
            request.source_path, prop_idx
        )
        logger.info(
            "Article %s: source_path=%s title=%s stage=%s",
            "created" if created else "updated",
            request.source_path,
            request.title[:60] if request.title else "(empty)",
            stage,
        )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_nowait(
                rag_article_upserted(
                    source_path=request.source_path,
                    created=created,
                    title=request.title,
                    content_hash=request.content_hash,
                    pipeline_stage=stage,
                    queue_state=queue_state,
                    queue_depth=queue_depth,
                    frontier_status="unknown",
                )
            )
        return ArticleUpsertResponse(
            source_path=request.source_path,
            created=created,
            pipeline_stage=stage,
            queue_state=queue_state,
            queue_depth=queue_depth,
            frontier_status="unknown",
        )

    @router.delete(
        "/source",
        response_model=SourceDeleteResponse,
        openapi_extra=x_mcp("delete_source", tool="rag"),
    )
    async def delete_source(path: str) -> SourceDeleteResponse:
        """Remove a single source from all storage surfaces."""
        prop_idx = get_property_index_fn()
        collection = get_collection_fn()

        existing = collection.get(where={"source": path}, include=[])
        chunk_ids: list[str] = existing.get("ids", [])
        chunks_deleted = len(chunk_ids)
        if chunk_ids:
            collection.delete(ids=chunk_ids)

        properties_removed = 0
        fts_removed = 0
        if prop_idx is not None:
            if chunk_ids:
                properties_removed = await prop_idx.remove_properties_for_chunks(
                    chunk_ids
                )
                fts_removed = await prop_idx.fts.remove_batch(chunk_ids)
            await prop_idx.clear_failures_for(path)
            await prop_idx.remove_indexed_source(path)

        fallback_row = None
        if prop_idx is not None:
            fallback_row = prop_idx.find_latest_article_by_filename(
                Path(path).name,
                exclude_source_path=path,
            )
        article_deleted = False
        if prop_idx is not None:
            article_deleted = await prop_idx.remove_article(path)
        if reconcile_article_registry_delete_fn is not None:
            reconcile_article_registry_delete_fn(
                source_path=path, fallback_row=fallback_row
            )

        logger.info(
            "Source deleted: source=%s chunks=%d properties=%d article=%s",
            path,
            chunks_deleted,
            properties_removed,
            article_deleted,
        )
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            await eb.publish_nowait(
                rag_source_deleted(
                    source=path,
                    chunks_deleted=chunks_deleted,
                    article_deleted=article_deleted,
                )
            )
        return SourceDeleteResponse(
            source=path,
            chunks_deleted=chunks_deleted,
            fts_removed=fts_removed,
            properties_removed=properties_removed,
            article_deleted=article_deleted,
        )

    register_directory_routes(
        router,
        get_collection_fn=get_collection_fn,
        get_property_index_fn=get_property_index_fn,
        get_event_bus_fn=get_event_bus_fn,
    )
