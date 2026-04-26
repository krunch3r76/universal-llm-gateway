"""RAG PropertyIndex: SQLite property/chunk/queue metadata."""
from __future__ import annotations

from services.rag.property_index.mixin_01 import _PropertyIndexPart01
from services.rag.property_index.mixin_02 import _PropertyIndexPart02
from services.rag.property_index.mixin_03 import _PropertyIndexPart03
from services.rag.property_index.mixin_04 import _PropertyIndexPart04
from services.rag.property_index.mixin_05 import _PropertyIndexPart05
from services.rag.property_index.mixin_06 import _PropertyIndexPart06
from services.rag.property_index.mixin_07 import _PropertyIndexPart07
from services.rag.property_index.mixin_08 import _PropertyIndexPart08

from services.rag.property_index._spec import (
    FailedChunk,
    PendingSnapshot,
    FailureSnapshot,
    IndexingFailure,
    ContextualizationException,
    IndexedSourceSnapshot,
)

class PropertyIndex(_PropertyIndexPart01, _PropertyIndexPart02, _PropertyIndexPart03, _PropertyIndexPart04, _PropertyIndexPart05, _PropertyIndexPart06, _PropertyIndexPart07, _PropertyIndexPart08):  # type: ignore[misc]
    """SQLite-backed property inverted index mapping property keys to chunk IDs.

    Write methods route through ``SequentialExecutor``; reads use the SQLite
    connection directly (single-writer).
    """

__all__ = ['PropertyIndex', 'FailedChunk', 'PendingSnapshot', 'FailureSnapshot', 'IndexingFailure', 'ContextualizationException', 'IndexedSourceSnapshot']
