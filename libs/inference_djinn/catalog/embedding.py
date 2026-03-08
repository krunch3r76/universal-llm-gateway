"""Embedding model detection and loader field inference.

Single source of truth for embedding routing discriminants.
∀ embedding model: loader.embedding = True ⟹ routed to embedding/{engine}/
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

EMBEDDING_ARCHITECTURES: frozenset[str] = frozenset(
    {"nomic-bert", "bert", "jina-bert-v2"}
)

NOMIC_TASK_PREFIXES: dict[str, str] = {
    "search_document": "search_document: ",
    "search_query": "search_query: ",
    "clustering": "clustering: ",
    "classification": "classification: ",
}

# Pooling method by GGUF architecture field.
# CLS-pooled: BERT-family models trained with a CLS token.
# Last-token: LLM-derived embedding models — hidden state at EOS position.
# ∀ arch not listed: pooling left unset; engine.py default (cls) applies with a WARNING.
_POOLING_BY_ARCH: dict[str, str] = {
    # BERT-family — CLS pooling
    "nomic-bert": "cls",
    "bert": "cls",
    "jina-bert-v2": "cls",
    # LLM-derived — last-token (EOS) pooling
    "qwen3": "last",
    "qwen2": "last",
    "llama": "last",
    "mistral": "last",
    "falcon": "last",
    "phi3": "last",
    "phi2": "last",
    "gemma": "last",
    "gemma2": "last",
}


def is_embedding_model(
    model_id: str,
    metadata: dict[str, Any],
    loader: dict[str, Any] | None = None,
) -> bool:
    """Detect embedding model from loader flag, model_id, and metadata heuristics.

    Detection (any one suffices):
      - explicit loader.embedding=True flag
      - known embedding architecture (nomic-bert, bert, jina-bert-v2)
      - case-insensitive "embed" substring in model_id or metadata.name
    """
    if loader is not None and loader.get("embedding") is True:
        return True
    arch = metadata.get("arch", "")
    name = metadata.get("name", "")
    return (
        arch in EMBEDDING_ARCHITECTURES
        or "embed" in model_id.lower()
        or "embed" in name.lower()
    )


def infer_embedding_loader(
    loader: dict[str, Any], metadata: dict[str, Any], model_id: str = ""
) -> None:
    """Set embedding-specific loader fields from metadata when applicable.

    Detection (any one suffices):
      - explicit loader.embedding flag already set
      - known embedding architecture
      - case-insensitive "embed" in model_id or metadata.name

    Uses setdefault to preserve any existing values from the catalog entry.
    Mutates loader in place; no-op if model is not an embedding model.

    Pooling inference:
      ∀ arch ∈ _POOLING_BY_ARCH: sets pooling from table (setdefault — catalog wins).
      ∀ arch ∉ _POOLING_BY_ARCH: logs WARNING so silent wrong-pooling bugs surface.
    """
    arch = metadata.get("arch", "")
    is_embedding = is_embedding_model(model_id, metadata, loader)

    if not is_embedding:
        return

    loader.setdefault("embedding", True)

    # n_ctx and n_batch are per-context values — they do NOT belong in the
    # base loader.  The schema's per-profile loader sets both:
    #   n_ctx  = int(profile_key)
    #   n_batch = n_ctx  (llama.cpp hard constraint for embedding mode)
    # See LlamaCppSchema._build_device_profiles.
    # Remove stale values left by earlier catalog versions.
    loader.pop("n_ctx", None)
    loader.pop("n_batch", None)

    loader.setdefault("embedding_task_default", "search_document")
    if "nomic" in arch:
        loader.setdefault("embedding_task_prefixes", dict(NOMIC_TASK_PREFIXES))

    # Pooling: infer from architecture if not already set in catalog.
    # BERT-family → cls. LLM-derived (Qwen3, Llama, Mistral, …) → last.
    # Wrong pooling (e.g. cls on a last-token model) produces degenerate
    # embeddings — all vectors collapse to ~1.0 cosine similarity.
    if "pooling" not in loader:
        inferred = _POOLING_BY_ARCH.get(arch)
        if inferred is not None:
            loader["pooling"] = inferred
        else:
            logger.warning(
                "Embedding model '%s' has unknown arch '%s' — pooling not inferred. "
                "Add 'pooling: last' or 'pooling: cls' to the catalog loader manually. "
                "Engine default (cls) will apply and may produce degenerate embeddings "
                "for LLM-derived models.",
                model_id or metadata.get("name", "unknown"),
                arch,
            )
