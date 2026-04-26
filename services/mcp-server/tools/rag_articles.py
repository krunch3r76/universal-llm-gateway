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
            On success: {"source_path": "...", "created": true/false}
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

        url = f"{_STARGATE_URL}/api/v1/rag/article"
        try:
            with httpx.Client(timeout=_ARTICLE_TIMEOUT) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                result = resp.json()
        except httpx.ConnectError as exc:
            logger.warning("RAG article upsert connection failed: %s", exc)
            record("mcp.rag.article.upsert.failed", error="connect_error")
            return {
                "error": "RAG service not reachable. Ensure Stargate and RAG are running."
            }
        except httpx.HTTPStatusError as exc:
            logger.warning("RAG article upsert HTTP error: %s", exc)
            record(
                "mcp.rag.article.upsert.failed",
                error=f"{exc.response.status_code}",
            )
            return {
                "error": f"Article upsert failed: {exc.response.status_code} "
                f"{exc.response.text}"
            }
        except httpx.RequestError as exc:
            logger.warning("RAG article upsert request error: %s", exc)
            record("mcp.rag.article.upsert.failed", error=str(exc))
            return {"error": f"Article upsert request failed: {exc}"}

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

        url = f"{_STARGATE_URL}/api/v1/rag/directory"
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.delete(url, params={"path": directory_path})
                resp.raise_for_status()
                result = resp.json()
        except httpx.ConnectError as exc:
            logger.warning("RAG directory delete connection failed: %s", exc)
            record("mcp.rag.directory.delete.failed", error="connect_error")
            return {
                "error": "RAG service not reachable. Ensure Stargate and RAG are running."
            }
        except httpx.HTTPStatusError as exc:
            logger.warning("RAG directory delete HTTP error: %s", exc)
            record(
                "mcp.rag.directory.delete.failed",
                error=f"{exc.response.status_code}",
            )
            return {
                "error": f"Directory delete failed: {exc.response.status_code} "
                f"{exc.response.text}"
            }
        except httpx.RequestError as exc:
            logger.warning("RAG directory delete request error: %s", exc)
            record("mcp.rag.directory.delete.failed", error=str(exc))
            return {"error": f"Directory delete request failed: {exc}"}

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

        url = f"{_STARGATE_URL}/api/v1/rag/source"
        try:
            with httpx.Client(timeout=_ARTICLE_TIMEOUT) as client:
                resp = client.delete(url, params={"path": source_path})
                resp.raise_for_status()
                result = resp.json()
        except httpx.ConnectError as exc:
            logger.warning("RAG source delete connection failed: %s", exc)
            record("mcp.rag.source.delete.failed", error="connect_error")
            return {
                "error": "RAG service not reachable. Ensure Stargate and RAG are running."
            }
        except httpx.HTTPStatusError as exc:
            logger.warning("RAG source delete HTTP error: %s", exc)
            record(
                "mcp.rag.source.delete.failed",
                error=f"{exc.response.status_code}",
            )
            return {
                "error": f"Source delete failed: {exc.response.status_code} "
                f"{exc.response.text}"
            }
        except httpx.RequestError as exc:
            logger.warning("RAG source delete request error: %s", exc)
            record("mcp.rag.source.delete.failed", error=str(exc))
            return {"error": f"Source delete request failed: {exc}"}

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

    @mcp.tool(title="RAG: List Articles")
    def rag_list_articles(
        scope: str | None = None,
        include_abstract: bool = False,
    ) -> dict[str, Any]:
        """List article metadata rows from the RAG corpus.

        Use this when you need corpus inventory or citation-level coverage,
        such as checking what papers already exist before deciding whether to
        ingest more. For semantic retrieval over chunk text, use `rag(op="search")`
        or `rag(op="answer")` instead of article listing.

        Args:
            scope: Comma-separated scope names to filter by. Omit to list all
                scopes. Example: "rag_systems,small_llm_prompting"
            include_abstract: Include the abstract field for each row. Leave
                false for faster, more compact planning-oriented responses.

        Returns:
            {"articles": [...], "count": N, "scopes_queried": [...]}
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record(
            "mcp.rag.articles.list.called",
            include_abstract=include_abstract,
            has_scope=bool(scope),
        )

        params: dict[str, str] = {
            "include_abstract": "true" if include_abstract else "false",
            **({"scope": scope} if scope else {}),
        }

        url = f"{_STARGATE_URL}/api/v1/rag/articles"
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                result = resp.json()
        except httpx.ConnectError as exc:
            logger.warning("RAG article listing connection failed: %s", exc)
            record("mcp.rag.articles.list.failed", error="connect_error")
            return {
                "error": "RAG service not reachable. Ensure Stargate and RAG are running."
            }
        except httpx.HTTPStatusError as exc:
            logger.warning("RAG article listing HTTP error: %s", exc)
            record("mcp.rag.articles.list.failed", error=f"{exc.response.status_code}")
            return {
                "error": f"Article listing failed: {exc.response.status_code} "
                f"{exc.response.text}"
            }
        except httpx.RequestError as exc:
            logger.warning("RAG article listing request error: %s", exc)
            record("mcp.rag.articles.list.failed", error=str(exc))
            return {"error": f"Article listing request failed: {exc}"}

        duration = monotonic_now() - t0
        count = result.get("count", 0) if isinstance(result, dict) else 0
        record(
            "mcp.rag.articles.list.completed",
            duration_s=round(duration, 3),
            count=count,
        )
        logger.info("rag_list_articles: count=%d in %.1fs", count, duration)
        return result if isinstance(result, dict) else {"error": "Invalid response"}

    @mcp.tool(title="RAG: Orphaned Articles")
    def rag_orphaned_articles() -> dict[str, Any]:
        """List articles with no corresponding indexed chunks.

        An article is "orphaned" when rag_upsert_article was called but
        the source file was never indexed (or its chunks were later deleted).
        Use this to detect metadata-only rows that should be cleaned up.

        Returns:
            {"orphans": [{"source_path", "title", "scope", "updated_at"}, ...],
             "count": N}
            On error: {"error": "<message>"}
        """
        t0 = monotonic_now()
        record("mcp.rag.orphaned.articles.called")

        url = f"{_STARGATE_URL}/api/v1/rag/orphaned_articles"
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                result = resp.json()
        except httpx.ConnectError as exc:
            logger.warning("RAG orphaned articles connection failed: %s", exc)
            record("mcp.rag.orphaned.articles.failed", error="connect_error")
            return {
                "error": "RAG service not reachable. Ensure Stargate and RAG are running."
            }
        except httpx.HTTPStatusError as exc:
            logger.warning("RAG orphaned articles HTTP error: %s", exc)
            record(
                "mcp.rag.orphaned.articles.failed",
                error=f"{exc.response.status_code}",
            )
            return {
                "error": f"Orphaned articles query failed: {exc.response.status_code} "
                f"{exc.response.text}"
            }
        except httpx.RequestError as exc:
            logger.warning("RAG orphaned articles request error: %s", exc)
            record("mcp.rag.orphaned.articles.failed", error=str(exc))
            return {"error": f"Orphaned articles request failed: {exc}"}

        duration = monotonic_now() - t0
        count = result.get("count", 0) if isinstance(result, dict) else 0
        record(
            "mcp.rag.orphaned.articles.completed",
            duration_s=round(duration, 3),
            orphan_count=count,
        )
        logger.info("rag_orphaned_articles: count=%d in %.1fs", count, duration)
        return result if isinstance(result, dict) else {"error": "Invalid response"}
