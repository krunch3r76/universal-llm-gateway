from __future__ import annotations

from typing import Any

_IMPLICIT_RETRIEVAL_PATH_BY_SCOPE: dict[str, str] = {}
"""Scope-specific retrieval-path defaults used only when the caller omitted the option."""


def resolve_retrieval_path(
    *,
    runtime: dict[str, Any],
    effective: dict[str, Any],
    scope_key: str | None,
) -> str:
    """Resolve retrieval_path with caller override, scope default, then global default."""
    raw = runtime.get("retrieval_path", effective.get("retrieval_path"))
    if raw is not None:
        return str(raw).strip()
    if scope_key is not None:
        implicit_scope_default = _IMPLICIT_RETRIEVAL_PATH_BY_SCOPE.get(scope_key)
        if implicit_scope_default:
            return implicit_scope_default
    return "general"
