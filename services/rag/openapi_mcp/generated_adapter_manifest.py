"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service rag
  python scripts/openapi_mcp_codegen.py --check --service rag
"""

from __future__ import annotations

OPENAPI_SHA256 = "dff92c469e36c4d244bd0c22b01a6672f489491c4d7d583355a9b98ff91fef52"
FACADE_TOOL = "rag"
SERVED_OPS: dict[str, dict[str, str]] = {
    "coverage": {
        "method": "GET",
        "path": "/coverage",
        "operation_id": "get_coverage_coverage_get",
    },
    "delete_directory": {
        "method": "DELETE",
        "path": "/directory",
        "operation_id": "delete_directory_directory_delete",
    },
    "delete_source": {
        "method": "DELETE",
        "path": "/source",
        "operation_id": "delete_source_source_delete",
    },
    "orphaned_articles": {
        "method": "GET",
        "path": "/orphaned_articles",
        "operation_id": "get_orphaned_articles_orphaned_articles_get",
    },
    "refresh_hints": {
        "method": "POST",
        "path": "/refresh_corpus_hints",
        "operation_id": "refresh_corpus_hints_refresh_corpus_hints_post",
    },
    "upsert_article": {
        "method": "POST",
        "path": "/article",
        "operation_id": "upsert_article_article_post",
    },
}
