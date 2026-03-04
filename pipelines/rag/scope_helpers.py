"""Shared scope helpers for RAG pipeline callers."""

from __future__ import annotations

from transport_utils.rag_client import DEFAULT_RAG_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)
_FALLBACK_SCOPE_OPTIONS = '"research", "project", or "all"'
_scopes_cache: dict[str, dict[str, str | list[str]]] | None = None


def _fetch_scopes(rag_url: str = DEFAULT_RAG_URL) -> dict[str, dict[str, str | list[str]]]:
    """Fetch and cache scope registry from RAG service."""
    global _scopes_cache  # noqa: PLW0603
    if _scopes_cache is not None:
        return _scopes_cache
    try:
        with make_sync_client(rag_url, timeout=3.0) as client:
            resp = client.get("/scopes")
        resp.raise_for_status()
        _scopes_cache = resp.json().get("scopes", {})
    except Exception:
        logger.warning("Could not fetch scopes from %s", rag_url)
        _scopes_cache = {}
    return _scopes_cache


def fetch_scope_choices(rag_url: str = DEFAULT_RAG_URL) -> list[str]:
    """Return available scope identifiers from RAG service."""
    scopes = _fetch_scopes(rag_url)
    if scopes:
        return list(scopes.keys())
    return ["project", "research", "all"]


def fetch_scope_options_text(rag_url: str = DEFAULT_RAG_URL) -> str:
    """Fetch scopes from RAG service and format for prompt injection.

    Returns a formatted string listing available scopes with descriptions,
    suitable for injection into an LLM prompt template.
    """
    scopes = _fetch_scopes(rag_url)
    if not scopes:
        return _FALLBACK_SCOPE_OPTIONS

    lines: list[str] = []
    for name, info in scopes.items():
        if name == "all":
            continue
        desc = info.get("description", "")
        lines.append(f'"{name}" — {desc}' if desc else f'"{name}"')
    lines.append('"all" — when unclear or mixed across multiple scopes')
    return "\n        ".join(lines)
