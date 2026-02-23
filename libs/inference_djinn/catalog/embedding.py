"""Embedding model detection and loader field inference.

Single source of truth for embedding routing discriminants.
∀ embedding model: loader.embedding = True ⟹ routed to embedding/{engine}/
"""

from typing import Any

EMBEDDING_ARCHITECTURES: frozenset[str] = frozenset(
    {"nomic-bert", "bert", "jina-bert-v2"}
)

NOMIC_TASK_PREFIXES: dict[str, str] = {
    "search_document": "search_document: ",
    "search_query": "search_query: ",
    "clustering": "clustering: ",
    "classification": "classification: ",
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
    """
    arch = metadata.get("arch", "")
    is_embedding = is_embedding_model(model_id, metadata, loader)

    if not is_embedding:
        return

    loader.setdefault("embedding", True)
    training_ctx = metadata.get("training_context_length")
    loader.setdefault("n_ctx", training_ctx or 2048)
    loader.setdefault("embedding_task_default", "search_document")
    if "nomic" in arch:
        loader.setdefault("embedding_task_prefixes", dict(NOMIC_TASK_PREFIXES))
