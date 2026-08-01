"""RAG dispatch op names — denominator for openapi_mcp stamping.

Must stay in sync with ``services/mcp-server/server.py`` ``rag_op_tool`` keys.
"""

from __future__ import annotations

RAG_DISPATCH_OPS: frozenset[str] = frozenset(
    {
        "search",
        "recon",
        "list_mapped",
        "list_scopes",
        "coverage",
        "upsert_article",
        "delete_source",
        "refresh_hints",
        "orphaned_articles",
        "delete_directory",
    }
)
