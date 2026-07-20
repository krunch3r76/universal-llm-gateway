"""Search scope resolution, property boost, BM25 sidecar, and recency sort."""

from __future__ import annotations

from services.rag.search_scope.bm25_sidecar import apply_bm25_sidecar
from services.rag.search_scope.prefix_filter import (
    apply_max_distance_filter,
    apply_source_prefix_filter,
    apply_source_prefix_filter_with_ids,
)
from services.rag.search_scope.property_boost import apply_property_boost
from services.rag.search_scope.recency import apply_recency_sort
from services.rag.search_scope.scope_resolution import (
    require_loaded_config,
    resolve_scope_request,
)

__all__ = [
    "apply_bm25_sidecar",
    "apply_max_distance_filter",
    "apply_property_boost",
    "apply_recency_sort",
    "apply_source_prefix_filter",
    "apply_source_prefix_filter_with_ids",
    "require_loaded_config",
    "resolve_scope_request",
]
