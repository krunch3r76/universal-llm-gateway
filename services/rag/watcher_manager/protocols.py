"""Protocols, constants, and extension helpers for the RAG file watcher.

Shared by all ``watcher_manager`` mixins: reconcile intervals (300 s idle, 30 s
after recovery), the initial reindex retry cap, typing Protocols for the
injected index/delete callables and their outcomes, and extension normalization
so watch-directory and baseline extension lists compare as lowercase ``.ext``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

from services.rag.config import WatchDirectory

_RECONCILE_INTERVAL_S = 300.0
_RECONCILE_BUSY_INTERVAL_S = 30.0
_INITIAL_REINDEX_MAX_ATTEMPTS = 3


def normalize_extensions(extensions: Sequence[str]) -> tuple[str, ...]:
    """Canonicalize file extensions to unique, lowercase, dot-prefixed strings.

    Blank entries are dropped and duplicates removed with first-seen order kept,
    so "MD", ".md" and " md " all become ".md". Used by ``WatcherManager`` for
    baseline extensions and by ``effective_extensions``.
    """
    return tuple(
        dict.fromkeys(
            f".{ext.strip().lower().lstrip('.')}" for ext in extensions if ext.strip()
        )
    )


def effective_extensions(
    watch_directory: WatchDirectory,
    baseline_extensions: tuple[str, ...],
) -> tuple[str, ...]:
    """Pick the extension set a watch directory should index, with fallback.

    Returns the directory's own normalized ``extensions`` when any are
    configured, otherwise the normalized ``baseline_extensions``. Used by
    watcher registration (inotify patterns) and the reconcile sweep filter.
    """
    configured = normalize_extensions(watch_directory.extensions)
    if configured:
        return configured
    return normalize_extensions(baseline_extensions)


class IndexOutcome(Protocol):
    """Protocol for the outcome of an indexing operation on a single file.

    Returned by ``IndexFn``. ``unchanged`` means the file was already indexed and
    nothing was rewritten; watcher mixins use ``indexed`` and ``unchanged`` to
    count recoveries and to decide whether to schedule scope freshness repair.
    """

    file: str
    deleted: int
    indexed: int
    unchanged: bool


class DeleteOutcome(Protocol):
    """Protocol for the outcome of a deletion operation on a single file.

    Returned by the injected ``DeleteFn``; ``deleted`` is the number of chunks
    removed for ``file``, reported in the ``rag_watch_file_deleted`` event
    emitted by ``FileEventsMixin._handle_file_delete``.
    """

    file: str
    deleted: int


class IndexFn(Protocol):
    """Callable protocol for the injected single-file indexing coroutine.

    ``WatcherManager`` calls it from hot-reload changes, initial sweeps,
    reconcile sweeps and ``request_reindex``. ``chunk_tokens`` is the watch
    directory override; sweeps pass ``emit_skip_event=False`` to avoid one
    skip event per unchanged file.
    """

    async def __call__(
        self,
        file_path: Path,
        chunk_tokens: int | None,
        *,
        emit_skip_event: bool = True,
    ) -> IndexOutcome: ...


DeleteFn = Callable[[Path], Awaitable[DeleteOutcome]]
PostReconcileRepairFn = Callable[[list[str]], Awaitable[None]]
ScopeRepairRunnerFn = Callable[[set[str]], Awaitable[None]]
