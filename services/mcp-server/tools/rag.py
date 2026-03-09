"""RAG search tool — semantic search via the RAG service.

Connects to the RAG service over Unix Domain Socket at
/tmp/universal-protocol/rag.sock (default). Falls back gracefully
when the service is unavailable.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_RAG_SOCKET = os.environ.get("RAG_SOCKET_PATH", "/tmp/universal-protocol/rag.sock")
_RAG_TIMEOUT = 15.0


def register_rag_tools(mcp: FastMCP) -> None:
    """Register RAG search tools on *mcp*."""

    @mcp.tool()
    def rag_search(
        query: str,
        top_k: int = 5,
        scope: str | None = None,
    ) -> dict[str, list[dict[str, str | float]]] | dict[str, str]:
        """Search the knowledge base using semantic similarity.

        Queries the RAG service (ChromaDB-backed) for chunks matching
        the query. Results include source file paths and relevance scores.

        The RAG service indexes project documentation, research papers,
        journal entries, and other text files placed in watched directories.

        Args:
            query: Natural language search query.
            top_k: Maximum number of results to return (default 5).
            scope: Optional named scope to filter results (e.g. "project", "research").

        Returns:
            A dictionary with search results or an error message.
            On success: {"results": [{"content", "source", "distance"}, ...]}
            On error: {"error": "<message>"}
        """
        if not os.path.exists(_RAG_SOCKET):
            return {
                "error": "RAG service is not running (socket not found). Start it via ./manage."
            }

        body: dict[str, str | int | None] = {
            "query": query,
            "top_k": top_k,
        }
        if scope:
            body["scope"] = scope

        try:
            transport = httpx.HTTPTransport(uds=_RAG_SOCKET)
            with httpx.Client(
                transport=transport,
                base_url="http://localhost",
                timeout=_RAG_TIMEOUT,
            ) as client:
                resp = client.post("/search", json=body)
                resp.raise_for_status()
        except httpx.ConnectError as e:
            logger.warning("RAG service connection failed: %s", e)
            return {"error": "RAG service connection failed. It may not be running."}
        except httpx.HTTPStatusError as e:
            logger.warning("RAG search HTTP status error: %s", e)
            return {
                "error": "RAG service returned an error: "
                f"{e.response.status_code} {e.response.reason_phrase}"
            }
        except httpx.RequestError as e:
            logger.warning("RAG search request error: %s", e)
            return {"error": f"RAG search failed: {e}"}

        data = resp.json()
        chunks = data.get("chunks", [])
        metadata = data.get("metadata", [])
        distances = data.get("distances", [])

        results = []
        for i, chunk in enumerate(chunks):
            meta = metadata[i] if i < len(metadata) else {}
            dist = distances[i] if i < len(distances) else 0.0
            results.append(
                {
                    "content": chunk,
                    "source": meta.get("source", "unknown"),
                    "distance": round(dist, 4),
                }
            )

        logger.info(
            "rag_search: query=%r scope=%s → %d results", query, scope, len(results)
        )
        return {"results": results}
