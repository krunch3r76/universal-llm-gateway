"""RAG service configuration: YAML parsing and dataclass definitions.

Loads ``~/.gateway/rag.yaml`` into typed dataclasses (``RagConfig``,
``WatchDirectory``, ``KnowledgeExtractionConfig``). Configuration controls
watch directories, scopes, embedding model, knowledge extraction settings,
contextualization, and post-index enforcement watermarks.
"""

from ._loader import load_config, save_scope
from ._models import (
    BASELINE_EXTENSIONS,
    DEFAULT_CONTEXTUALIZE_CLIENT_TIMEOUT_S,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_INDEX_WORKERS,
    KnowledgeExtractionConfig,
    RagConfig,
    ScopeDefinition,
    WatchDirectory,
)

__all__ = [
    "RagConfig",
    "WatchDirectory",
    "ScopeDefinition",
    "KnowledgeExtractionConfig",
    "BASELINE_EXTENSIONS",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_INDEX_WORKERS",
    "DEFAULT_CONTEXTUALIZE_CLIENT_TIMEOUT_S",
    "load_config",
    "save_scope",
]
