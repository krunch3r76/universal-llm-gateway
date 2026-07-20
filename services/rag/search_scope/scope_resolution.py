"""Named scope resolution for search requests."""

from __future__ import annotations

from fastapi import HTTPException

from services.rag.config import RagConfig
from services.rag.models import SearchRequest

__all__ = ["require_loaded_config", "resolve_scope_request"]


def require_loaded_config(config: RagConfig | None) -> RagConfig:
    """Return loaded config or raise HTTP 503 when startup has not finished."""
    if config is None:
        raise HTTPException(status_code=503, detail="RAG config not loaded")
    return config


def resolve_scope_request(
    request: SearchRequest,
    config: RagConfig | None,
) -> SearchRequest:
    """Resolve named scope(s) to merged source prefixes; reject conflicting fields."""
    if request.scope and request.source_prefixes:
        raise HTTPException(
            status_code=400,
            detail="'scope' and 'source_prefixes' are mutually exclusive",
        )
    if request.scope is None:
        return request
    scope_names = [request.scope] if isinstance(request.scope, str) else request.scope
    if not scope_names:
        raise HTTPException(
            status_code=400,
            detail="scope cannot be empty list",
        )

    loaded_config = require_loaded_config(config)
    merged_prefixes: list[str] = []
    seen: set[str] = set()
    for name in scope_names:
        scope_def = loaded_config.scopes.get(name)
        if scope_def is None:
            available = sorted(loaded_config.scopes)
            available_display = ", ".join(available)
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scope {name!r}. Available: {available_display}",
            )
        for p in scope_def.prefixes:
            if p not in seen:
                seen.add(p)
                merged_prefixes.append(p)
    return request.model_copy(update={"source_prefixes": merged_prefixes})
