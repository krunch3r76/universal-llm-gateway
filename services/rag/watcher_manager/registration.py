"""Inotify watcher registration for configured directories."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from universal_hot_reload import HotReloadWatcher
from universal_logging import get_logger

from services.rag.config import WatchDirectory
from services.rag.events.lifecycle import (
    rag_watch_directory_missing,
    rag_watch_started,
)
from services.rag.watcher_manager.protocols import effective_extensions

logger = get_logger(__name__)


class RegistrationMixin:
    async def _register_one(
        self, watch_directory: WatchDirectory
    ) -> tuple[Path, WatchDirectory, tuple[str, ...]] | None:
        """Register a single inotify watcher without running initial reindex."""
        watch_path = Path(watch_directory.path).expanduser().resolve()
        if not watch_path.exists() or not watch_path.is_dir():
            logger.warning("Watch directory missing; skipping: %s", watch_path)
            await self._emit(rag_watch_directory_missing(path=str(watch_path)))
            return None

        effective_ext = effective_extensions(
            watch_directory, self._baseline_extensions
        )
        chunk_tokens = watch_directory.chunk_tokens

        async def on_change(file_path: str) -> None:
            await self._handle_file_change(file_path, chunk_tokens)

        delete_callback: Callable[[str], Awaitable[None]] | None = None
        if self._delete_fn is not None:

            async def _on_delete(file_path: str) -> None:
                await self._handle_file_delete(file_path)

            delete_callback = _on_delete

        watcher = HotReloadWatcher(
            name=f"rag-watch:{watch_path.name}",
            watch_path=watch_path,
            on_change=on_change,
            debounce_ms=2000,
            recursive=watch_directory.recursive,
            patterns=list(effective_ext),
            exclude=watch_directory.exclude,
            on_delete=delete_callback,
        )
        started = await watcher.start()
        if started:
            self._watchers.append(watcher)
            self._watch_configs.append(watch_directory)
            await self._emit(
                rag_watch_started(
                    path=str(watch_path),
                    extensions=list(effective_ext),
                    recursive=watch_directory.recursive,
                )
            )
            return watch_path, watch_directory, effective_ext
        return None
