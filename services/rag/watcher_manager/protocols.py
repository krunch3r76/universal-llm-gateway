"""Protocols, constants, and extension helpers for the RAG file watcher."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

from services.rag.config import WatchDirectory

_RECONCILE_INTERVAL_S = 300.0
_RECONCILE_BUSY_INTERVAL_S = 30.0
_INITIAL_REINDEX_MAX_ATTEMPTS = 3


def normalize_extensions(extensions: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            f".{ext.strip().lower().lstrip('.')}" for ext in extensions if ext.strip()
        )
    )


def effective_extensions(
    watch_directory: WatchDirectory,
    baseline_extensions: tuple[str, ...],
) -> tuple[str, ...]:
    configured = normalize_extensions(watch_directory.extensions)
    if configured:
        return configured
    return normalize_extensions(baseline_extensions)


class IndexOutcome(Protocol):
    """Protocol for the outcome of an indexing operation on a single file."""

    file: str
    deleted: int
    indexed: int
    unchanged: bool


class DeleteOutcome(Protocol):
    """Protocol for the outcome of a deletion operation on a single file."""

    file: str
    deleted: int


class IndexFn(Protocol):
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
