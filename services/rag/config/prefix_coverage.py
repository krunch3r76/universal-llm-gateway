"""Indexed-source coverage against merged RAG scope prefixes.

``orphan_source_prefixes`` is the fixture guard for scope membership: a source
path that matches no merged prefix and no intentional-unscoped prefix is an
orphan. Search uses the same startswith rule (``matches_source_prefix``).
This module does not open the property index; a live orphan census is a
separate follow-up from the fixture assertion.
"""

from __future__ import annotations

from services.rag.search_scope.prefix_filter import matches_source_prefix

__all__ = ["orphan_source_prefixes"]


def orphan_source_prefixes(
    indexed_sources: list[str],
    scope_prefixes: list[str],
    intentional_unscoped: list[str] | None = None,
) -> list[str]:
    """Return source paths covered by neither a scope nor an allowlist.

    Coverage is ``matches_source_prefix`` (startswith), the same predicate
    search uses on chunk metadata. ``intentional_unscoped`` uses that
    predicate too and defaults to an empty list. Input order is preserved.
    An empty list means every source was covered or intentionally unscoped.
    Pass the flattened prefixes of ``load_config().scopes`` so the check
    sees the merged config rather than the box file alone.
    """
    allow = intentional_unscoped or []
    orphans: list[str] = []
    for source in indexed_sources:
        if matches_source_prefix(source, scope_prefixes):
            continue
        if matches_source_prefix(source, allow):
            continue
        orphans.append(source)
    return orphans
