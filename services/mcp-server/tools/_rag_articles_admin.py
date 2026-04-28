"""Article inventory tools (list / orphaned) split from rag_articles for SLOC."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import httpx
from mcp_events import monotonic_now, record

from ._rag_http import _handle_rag_call_error, _rag_get

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_STARGATE_URL = os.environ.get("STARGATE_URL", "http://io:9999")


def register_article_inventory_tools(mcp: FastMCP) -> None:
    """Register list-articles and orphaned-articles tools."""

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

        try:
            result = _rag_get(
                _STARGATE_URL,
                "api/v1/rag/articles",
                timeout=20.0,
                params=params,
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as exc:
            return _handle_rag_call_error(exc, endpoint_name="articles")

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

        try:
            result = _rag_get(
                _STARGATE_URL,
                "api/v1/rag/orphaned_articles",
                timeout=15.0,
            )
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
            httpx.RequestError,
            ValueError,
        ) as exc:
            return _handle_rag_call_error(exc, endpoint_name="orphaned_articles")

        duration = monotonic_now() - t0
        count = result.get("count", 0) if isinstance(result, dict) else 0
        record(
            "mcp.rag.orphaned.articles.completed",
            duration_s=round(duration, 3),
            orphan_count=count,
        )
        logger.info("rag_orphaned_articles: count=%d in %.1fs", count, duration)
        return result if isinstance(result, dict) else {"error": "Invalid response"}
