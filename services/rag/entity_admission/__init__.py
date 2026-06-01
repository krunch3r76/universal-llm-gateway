"""Fail-closed entity-admission gate for entity-curated RAG watch roots.

Owns the set of absolute paths that some cortex entity points at via
source_uri. Consulted at two layers (WatcherManager._should_attempt and
indexing._index_file_impl) to enforce: a file in an entity-gated root is
indexed only if a backing entity exists. See plan:rag-entity-gated-indexing.
"""

from .gate import EntityAdmissionGate

__all__ = ["EntityAdmissionGate"]
