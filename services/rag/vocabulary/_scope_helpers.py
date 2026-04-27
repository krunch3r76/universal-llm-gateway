"""Scope utility helpers: configured scopes map and vocab mode resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.rag.config import RagConfig


def configured_scopes_map(config: RagConfig) -> dict[str, list[str]]:
    """Map scope name → prefix list from rag.yaml."""
    return {name: list(sdef.prefixes) for name, sdef in config.scopes.items()}


def _resolve_scope_vocab_mode(scope_name: str, config: RagConfig) -> str:
    """Return effective vocab mode for a scope: per-scope override or global default."""
    sdef = config.scopes.get(scope_name)
    if sdef is not None and sdef.vocab_mode:
        return sdef.vocab_mode
    return config.vocabulary_mode or "local"
