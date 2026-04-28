"""RAG article metadata tools for listing and curating indexed documents.

Article rows in rag_metadata.db are joined to search results at query time
via the source_hash (plain SHA-256 of file bytes). This tool allows agents
to add or update article metadata without reindexing.

Connectivity: MCP container → Stargate host on port 9999 →
passthrough → RAG service (UDS or TCP).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record

from ._rag_articles_admin import register_article_inventory_tools
from ._rag_http import (
    _handle_rag_call_error,
    _rag_delete,
    _rag_get,
    _rag_post,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")
_ARTICLE_TIMEOUT = 15.0


def register_rag_article_tools(mcp: FastMCP) -> None:
    """Register RAG article metadata tools on *mcp*."""

    @mcp.tool(title="RAG: Upsert Article")
    def rag_upsert_article(
        source_path: str,
        title: str = "",
        authors: str = "",
        venue: str = "",
        published_date: str = "",
        doi: str = "",
        abstract: str = "",
        content_hash: str = "",
        subdirectory: str = "",
        scope: str = "all",
    ) -> dict[str, Any]:
        """Add or update article citation metadata for a document.

        This does NOT index content or create chunks — it only writes
        metadata to the articles table. Indexing happens separately via
        the file watcher or POST /index.

        The metadata is joined to search results at query time via
        content_hash (the plain SHA-256 of the file bytes). Non-empty
        fields overwrite existing values; empty strings preserve
        current values (merge semantics).

        Typical use: after downloading a new paper, call this with the
        source_path, title, authors, and content_hash so search results
        include citation metadata.

        Args:
            source_path: Absolute path to the source file.
            title: Article title.
            authors: Comma-separated author names.
            venue: Publication venue (journal, conference).
            published_date: ISO date string (e.g. "2025-01-15").
            doi: Digital Object Identifier.
            abstract: Article abstract.
            content_hash: Plain SHA-256 hex digest of the file bytes.
                This is the join key to indexed chunks. If omitted,
                the existing value (if any) is preserved.
            subdirectory: Subdirectory within the corpus root.
            scope: Retrieval scope (default "all").

        Returns:
            On success: {
              "source_path": "...",
              "created": true/false,
              "pipeline_stage": "registered"|"queued"|"chunked"|"contextualized",
              "queue_state": null|"ready"|"in_flight"|"cooling_off"|"capacity_blocked"|"exhausted",
              "queue_depth": <int — total items in extraction_queue across all sources>,
              "frontier_status": "reachable"|"unreachable"|"unknown"
            }
            pipeline_stage describes where this source sits in the indexing pipeline.
            "registered" means only the metadata row exists; content is not yet indexed.
            "queued" means extraction/contextualization is pending — check queue_state for detail.
            "chunked" means indexed into ChromaDB but not yet contextualized.
            "contextualized" means fully indexed and contextualized; ready to query.
            On error:   {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.article.upsert.called", source_path=source_path)

        body: dict[str, Any] = {"source_path": source_path, "scope": scope}
        if title:
            body["title"] = title
        if authors:
            body["authors"] = authors
        if venue:
            body["venue"] = venue
        if published_date:
            body["published_date"] = published_date
        if doi:
            body["doi"] = doi
        if abstract:
            body["abstract"] = abstract
        if content_hash:
            body["content_hash"] = content_hash
        if subdirectory:
            body["subdirectory"] = subdirectory

        try:
            result = _rag_post(
                _STARGATE_URL,
                "api/v1/rag/article",
                body,
                timeout=_ARTICLE_TIMEOUT,
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as exc:
            return _handle_rag_call_error(exc, endpoint_name="article")

        duration = monotonic_now() - t0
        created = result.get("created", False) if isinstance(result, dict) else False
        record(
            "mcp.rag.article.upsert.completed",
            duration_s=round(duration, 3),
            created=created,
            source_path=source_path,
        )
        logger.info(
            "rag_upsert_article: %s source_path=%s in %.1fs",
            "created" if created else "updated",
            source_path,
            duration,
        )
        return result if isinstance(result, dict) else {"error": "Invalid response"}

    @mcp.tool(title="RAG: Source Status")
    def rag_source_status(source_paths: list[str]) -> dict[str, Any]:
        """Query the current RAG pipeline stage for one or more source files.

        Use after rag_upsert_article to verify actual pipeline progress, or
        at any point to audit ingest state. Returns stage per source plus
        aggregate queue depth and stale corpus hints count.

        When to use over rag_upsert_article:
        - Checking status of previously ingested sources (not just after upsert)
        - Auditing a batch ingest across multiple sources at once
        - Monitoring whether vocab hints are stale
          (stale_corpus_hints_count > 0 means scopes have been re-indexed
          since last classify run; use scripts/rag/classify_vocabulary.py for
          authoritative staleness check)

        Args:
            source_paths: Absolute paths of source files to query.

        Returns:
            On success: {
              "sources": [{"source_path": ..., "pipeline_stage": ...,
                           "queue_position": ..., "queue_attempts": ...,
                           "last_error": ..., "indexed_at": ...,
                           "contextualized_chunks": ...}],
              "queue_depth": N,
              "frontier_status": "reachable"|"unreachable"|"unknown",
              "stale_corpus_hints_count": N,
            }
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.source.status.called", count=len(source_paths))

        try:
            result = _rag_get(
                _STARGATE_URL,
                "api/v1/rag/source-status",
                timeout=_ARTICLE_TIMEOUT,
                params=[("sources", s) for s in source_paths],
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as exc:
            return _handle_rag_call_error(exc, endpoint_name="source_status")

        duration = monotonic_now() - t0
        count = len(result.get("sources", [])) if isinstance(result, dict) else 0
        record(
            "mcp.rag.source.status.completed",
            duration_s=round(duration, 3),
            sources_queried=count,
        )
        logger.info("rag_source_status: sources=%d in %.1fs", count, duration)
        return result if isinstance(result, dict) else {"error": "Invalid response"}

    @mcp.tool(title="RAG: Delete Directory")
    def rag_delete_directory(directory_path: str) -> dict[str, Any]:
        """Remove all sources under a directory from all RAG storage surfaces.

        Prefix-matches source paths to find every file under the directory,
        then deletes ChromaDB chunks, FTS, properties, failed extractions,
        and articles for each matched source.

        Args:
            directory_path: Absolute directory path. All sources whose
                source_path starts with this prefix (plus trailing slash)
                will be removed.

        Returns:
            On success: {"path": "...", "sources_deleted": N,
                         "chunks_deleted": N, "articles_deleted": N}
            On error:   {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.directory.delete.called", directory_path=directory_path)

        try:
            result = _rag_delete(
                _STARGATE_URL,
                "api/v1/rag/directory",
                timeout=60.0,
                params={"path": directory_path},
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as exc:
            return _handle_rag_call_error(exc, endpoint_name="directory")

        duration = monotonic_now() - t0
        sources = result.get("sources_deleted", 0) if isinstance(result, dict) else 0
        chunks = result.get("chunks_deleted", 0) if isinstance(result, dict) else 0
        record(
            "mcp.rag.directory.delete.completed",
            duration_s=round(duration, 3),
            directory_path=directory_path,
            sources_deleted=sources,
            chunks_deleted=chunks,
        )
        logger.info(
            "rag_delete_directory: path=%s sources=%d chunks=%d in %.1fs",
            directory_path,
            sources,
            chunks,
            duration,
        )
        return result if isinstance(result, dict) else {"error": "Invalid response"}

    @mcp.tool(title="RAG: Delete Source")
    def rag_delete_source(source_path: str) -> dict[str, Any]:
        """Remove a source file from all RAG storage surfaces.

        Deletes ChromaDB chunks, FTS entries, property index entries,
        and the articles table row for the given source_path. Use this
        to clean up files that should no longer be in the index.

        For bulk removal of an entire directory, use
        rag_delete_directory instead.

        Args:
            source_path: Absolute path to the source file to remove.

        Returns:
            On success: {"source": "...", "chunks_deleted": N,
                         "fts_removed": N, "properties_removed": N,
                         "article_deleted": true/false}
            On error:   {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.source.delete.called", source_path=source_path)

        try:
            result = _rag_delete(
                _STARGATE_URL,
                "api/v1/rag/source",
                timeout=_ARTICLE_TIMEOUT,
                params={"path": source_path},
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as exc:
            return _handle_rag_call_error(exc, endpoint_name="source")

        duration = monotonic_now() - t0
        chunks = result.get("chunks_deleted", 0) if isinstance(result, dict) else 0
        article = (
            result.get("article_deleted", False) if isinstance(result, dict) else False
        )
        record(
            "mcp.rag.source.delete.completed",
            duration_s=round(duration, 3),
            source_path=source_path,
            chunks_deleted=chunks,
            article_deleted=article,
        )
        logger.info(
            "rag_delete_source: source_path=%s chunks=%d article=%s in %.1fs",
            source_path,
            chunks,
            article,
            duration,
        )
        return result if isinstance(result, dict) else {"error": "Invalid response"}

    register_article_inventory_tools(mcp)
