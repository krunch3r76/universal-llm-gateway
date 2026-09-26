"""Scope utility helpers: configured scopes map and vocab mode resolution.

``configured_scopes_map`` flattens ``RagConfig.scopes`` into name-to-prefixes
for ``update_corpus_hints`` and freshness checks (``_repair``,
``rag_service.scope_freshness``, ``rag_service.state``, admin article routes).
``_resolve_scope_vocab_mode`` applies per-scope ``vocab_mode`` over the global
``vocabulary_mode``, defaulting to "local".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.rag.config import RagConfig


def configured_scopes_map(config: RagConfig) -> dict[str, list[str]]:
    """Build a scope name → source-prefix list mapping from rag.yaml scopes.

    Copies each ``ScopeDefinition.prefixes`` into a fresh list, so callers may mutate
    the result without touching config. Includes every configured scope,
    union scopes too; ``run_scope_freshness_repair`` skips unions itself.
    """
    return {name: list(sdef.prefixes) for name, sdef in config.scopes.items()}


def _resolve_scope_vocab_mode(scope_name: str, config: RagConfig) -> str:
    """Return effective vocab mode for a scope: per-scope override or global default."""
    sdef = config.scopes.get(scope_name)
    if sdef is not None and sdef.vocab_mode:
        return sdef.vocab_mode
    return config.vocabulary_mode or "local"
