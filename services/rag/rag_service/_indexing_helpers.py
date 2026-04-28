"""Shared helpers reused across indexing sub-modules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.rag.config import RagConfig


def _should_skip_cached_source(
    *,
    force: bool,
    operation: str | None,
    cached_source: object | None,
    source_mtime_ns: int,
    source_size_bytes: int,
) -> bool:
    """Return whether the stat-first cache may short-circuit this source.

    With extraction decoupled, the skip check only compares file identity
    (mtime + size). Extraction staleness is handled by the extraction worker.
    """
    if force or operation == "reindex" or cached_source is None:
        return False
    cached = cached_source
    return bool(
        cached.mtime_ns == source_mtime_ns and cached.size_bytes == source_size_bytes
    )


def _derive_subdirectory(source: str, config: RagConfig) -> str:
    """Return the parent path of source relative to its configured watch root."""
    source_path = Path(source).expanduser().resolve()
    for watch_directory in config.watch_directories:
        watch_root = Path(watch_directory.path).expanduser().resolve()
        try:
            relative = source_path.relative_to(watch_root)
        except ValueError:
            continue
        return str(relative.parent) if relative.parent != Path(".") else ""
    return ""
