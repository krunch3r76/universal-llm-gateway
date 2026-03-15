"""RAG article metadata tools — upsert citation metadata for indexed documents.

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

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://host.docker.internal:9999")
_ARTICLE_TIMEOUT = 15.0


def register_rag_article_tools(mcp: FastMCP) -> None:
    """Register RAG article metadata tools on *mcp*."""

    @mcp.tool()
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
