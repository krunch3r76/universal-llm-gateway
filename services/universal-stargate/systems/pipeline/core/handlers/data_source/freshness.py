"""Tier-aware freshness gate for ``rag_corpus_hints`` scope iteration.

Decides whether a RAG scope can be skipped because its corpus files have not
changed since the last indexed run. The decision is mode-sensitive: in
``local`` mode an unchanged hash is always skippable, while in ``frontier`` mode
a scope is only skipped when the stored tier is itself ``frontier`` (a
local-tier cache must not satisfy a frontier-mode request). See the vocabulary
pipeline plan for the originating requirement.
"""

from __future__ import annotations


def should_skip_fresh_scope(
    *,
    skip_fresh: bool,
    mode: str,
    current_hash: str,
    stored: tuple[str, str, str] | None,
) -> bool:
    """Tier-aware skip when corpus hash is unchanged (see vocabulary pipeline plan)."""
    if not skip_fresh:
        return False
    if stored is None:
        return False
    files_hash, _, tier = stored
    if files_hash != current_hash:
        return False
    tier_norm = (tier or "local").strip().lower()
    if tier_norm not in ("local", "frontier"):
        tier_norm = "local"
    mode_norm = (mode or "local").strip().lower()
    if mode_norm == "local":
        return True
    if mode_norm == "frontier":
        return tier_norm == "frontier"
    return True
