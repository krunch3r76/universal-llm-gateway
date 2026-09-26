"""Hot-reload file change and delete handlers mixed into ``WatcherManager``.

``RegistrationMixin._register_one`` wires each ``HotReloadWatcher``'s on_change
and on_delete callbacks to these handlers. A change reindexes one file through
the injected ``_index_fn``; a delete removes its chunks via ``_delete_fn``.
Failures are logged and emitted as ``rag_file_indexing_failed`` or
``rag_file_deletion_failed`` events rather than raised.
"""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

from services.rag.events.indexing import (
    rag_file_deletion_failed,
    rag_file_indexing_failed,
)
from services.rag.events.lifecycle import (
    rag_watch_file_deleted,
    rag_watch_reindex_complete,
)

logger = get_logger(__name__)


class FileEventsMixin:
    """Per-file hot-reload handlers for inotify change and delete notifications.

    Stateless mixin composed into ``WatcherManager``; it relies on the host's
    ``_index_fn``, ``_delete_fn``, ``_emit`` and ``_note_index_mutation``.
    ``_handle_file_change`` reindexes an existing file and emits
    ``rag_watch_reindex_complete``; ``_handle_file_delete`` emits
    ``rag_watch_file_deleted``.
    """

    async def _handle_file_change(
        self, file_path: str, chunk_tokens: int | None
    ) -> None:
        """Reindex a changed file triggered by a hot-reload watcher event."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            logger.debug("Watcher change ignored for missing/non-file path: %s", path)
            return
        try:
            result = await self._index_fn(path, chunk_tokens)
        except Exception as exc:
            logger.warning(
                "Hot-reload reindex failed for %s: %s", file_path, exc, exc_info=True
            )
            await self._emit(
                rag_file_indexing_failed(file=str(file_path), error=str(exc))
            )
            return
        self._note_index_mutation(path, result)
        logger.debug(
            "Watcher reindex complete: file=%s deleted=%d indexed=%d unchanged=%s",
            result.file,
            result.deleted,
            result.indexed,
            result.unchanged,
        )
        await self._emit(
            rag_watch_reindex_complete(
                file=result.file,
                deleted=result.deleted,
                indexed=result.indexed,
                unchanged=result.unchanged,
            )
        )

    async def _handle_file_delete(self, file_path: str) -> None:
        """Delete all indexed chunks for a removed file."""
        assert self._delete_fn is not None
        path = Path(file_path)
        try:
            result = await self._delete_fn(path)
        except Exception as exc:
            logger.warning(
                "Hot-reload delete failed for %s: %s", file_path, exc, exc_info=True
            )
            await self._emit(rag_file_deletion_failed(file=file_path, error=str(exc)))
            return
        logger.debug(
            "Watcher delete complete: file=%s deleted=%d",
            result.file,
            result.deleted,
        )
        await self._emit(
            rag_watch_file_deleted(
                file=result.file,
                deleted=result.deleted,
            )
        )
