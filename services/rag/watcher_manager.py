from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from fnmatch import fnmatch
from pathlib import Path
from typing import Protocol

from universal_event_bus import Event, EventBus
from universal_hot_reload.watcher import HotReloadWatcher

from services.rag.config import RagConfig, WatchDirectory
from services.rag.events import (
    rag_watch_directory_missing,
    rag_watch_initial_complete,
    rag_watch_reconcile_complete,
    rag_watch_reindex_complete,
    rag_watch_started,
    rag_watch_stopped,
)

logger = logging.getLogger(__name__)

# Reconciliation: re-sweep watched dirs to recover files missed by initial sweep.
# ∀ file ∈ watched_dir: if not in index → index now.
# Interval is intentionally slow; this is a safety net, not a polling mechanism.
_RECONCILE_INTERVAL_S = 60.0


class IndexOutcome(Protocol):
    file: str
    deleted: int
    indexed: int
    unchanged: bool


IndexFn = Callable[[Path, int | None], Awaitable[IndexOutcome]]


class WatcherManager:
    """Manages HotReloadWatcher instances for configured directories.

    The inotify-based watcher only fires for changes after it starts.
    Files absent from the index (e.g. due to startup embedding failures) are
    recovered by a periodic reconciliation sweep that calls _index_fn on every
    watched file; _index_fn returns unchanged=True immediately for files already
    indexed, so the sweep overhead is minimal.
    """

    def __init__(
        self,
        index_fn: IndexFn,
        event_bus: EventBus | None = None,
        reconcile_interval_s: float = _RECONCILE_INTERVAL_S,
    ) -> None:
        self._index_fn: IndexFn = index_fn
        self._event_bus: EventBus | None = event_bus
        self._reconcile_interval_s = reconcile_interval_s
        self._watchers: list[HotReloadWatcher] = []
        self._watch_configs: list[WatchDirectory] = []
        self._reconcile_task: asyncio.Task[None] | None = None

    async def start(self, config: RagConfig) -> None:
        """Start watchers and background reconciliation for all configured directories."""
        for watch_directory in config.watch_directories:
            await self._start_one(watch_directory)
        if self._watch_configs:
            self._reconcile_task = asyncio.create_task(
                self._reconcile_loop(), name="rag-watcher-reconcile"
            )

    async def stop(self) -> None:
        """Stop all watchers and the reconciliation loop."""
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            try:
                await self._reconcile_task
            except asyncio.CancelledError:
                pass
            self._reconcile_task = None
        count = len(self._watchers)
        for watcher in self._watchers:
            await watcher.stop()
        self._watchers = []
        self._watch_configs = []
        await self._emit(rag_watch_stopped(watchers=count))

    async def _start_one(self, watch_directory: WatchDirectory) -> None:
        watch_path = Path(watch_directory.path).expanduser().resolve()
        if not watch_path.exists() or not watch_path.is_dir():
            logger.warning("Watch directory missing; skipping: %s", watch_path)
            await self._emit(rag_watch_directory_missing(path=str(watch_path)))
            return
        if not watch_directory.extensions:
            logger.warning(
                "Watch directory has no extensions; skipping: %s", watch_path
            )
            return

        await self._initial_reindex(watch_path, watch_directory)
        chunk_tokens = watch_directory.chunk_tokens

        async def on_change(file_path: str, *, _ct: int | None = chunk_tokens) -> None:
            await self._handle_file_change(file_path, _ct)

        watcher = HotReloadWatcher(
            name=f"rag-watch:{watch_path.name}",
            watch_path=watch_path,
            on_change=on_change,
            debounce_ms=2000,
            recursive=watch_directory.recursive,
            patterns=watch_directory.extensions,
            exclude=watch_directory.exclude,
        )
        started = await watcher.start()
        if started:
            self._watchers.append(watcher)
            self._watch_configs.append(watch_directory)
            await self._emit(
                rag_watch_started(
                    path=str(watch_path),
                    extensions=watch_directory.extensions,
                    recursive=watch_directory.recursive,
                )
            )

    async def _reconcile_loop(self) -> None:
        """Periodically re-sweep watched directories to recover missed files.

        ∀ file in watched dir: if _index_fn returns unchanged=True → fast path
        (single ChromaDB get). Files absent from the index are embedded and upserted.
        This covers files that failed indexing during startup (e.g. embedding race).
        """
        # watch_configs is immutable after start(); precompute once.
        ext_sets = [
            frozenset(ext.lower() for ext in wd.extensions)
            for wd in self._watch_configs
        ]
        await asyncio.sleep(self._reconcile_interval_s)
        while True:
            for watch_directory, extensions in zip(
                self._watch_configs, ext_sets, strict=True
            ):
                watch_path = Path(watch_directory.path).expanduser().resolve()
                if not watch_path.exists():
                    continue
                exclude = watch_directory.exclude
                walker = (
                    watch_path.rglob("*")
                    if watch_directory.recursive
                    else watch_path.glob("*")
                )
                recovered = 0
                unchanged = 0
                for file_path in walker:
                    if (
                        not file_path.is_file()
                        or file_path.suffix.lower() not in extensions
                    ):
                        continue
                    if any(fnmatch(file_path.name, pat) for pat in exclude):
                        continue
                    try:
                        result = await self._index_fn(
                            file_path, watch_directory.chunk_tokens
                        )
                        if result.unchanged:
                            unchanged += 1
                        else:
                            recovered += 1
                            logger.info(
                                "Reconcile recovered: file=%s indexed=%d",
                                result.file,
                                result.indexed,
                            )
                    except Exception as exc:
                        logger.warning("Reconcile skipped %s: %s", file_path, exc)
                if recovered:
                    await self._emit(
                        rag_watch_reconcile_complete(
                            path=str(watch_path),
                            recovered=recovered,
                            unchanged=unchanged,
                        )
                    )
            await asyncio.sleep(self._reconcile_interval_s)

    async def _initial_reindex(
        self,
        watch_path: Path,
        watch_directory: WatchDirectory,
    ) -> None:
        file_total = 0
        reindexed_total = 0
        unchanged_total = 0

        walker = (
            watch_path.rglob("*") if watch_directory.recursive else watch_path.glob("*")
        )
        extensions = {ext.lower() for ext in watch_directory.extensions}
        exclude = watch_directory.exclude
        for file_path in walker:
            if not file_path.is_file() or file_path.suffix.lower() not in extensions:
                continue
            if any(fnmatch(file_path.name, pat) for pat in exclude):
                continue
            try:
                result = await self._index_fn(file_path, watch_directory.chunk_tokens)
                file_total += 1
                if result.unchanged:
                    unchanged_total += 1
                else:
                    reindexed_total += 1
            except Exception as exc:
                logger.warning("Initial reindex skipped for %s: %s", file_path, exc)

        logger.info(
            "Initial watch reindex complete: path=%s files=%d reindexed=%d unchanged=%d",
            watch_path,
            file_total,
            reindexed_total,
            unchanged_total,
        )
        await self._emit(
            rag_watch_initial_complete(
                path=str(watch_path),
                files=file_total,
                reindexed=reindexed_total,
                unchanged=unchanged_total,
            )
        )

    async def _handle_file_change(
        self, file_path: str, chunk_tokens: int | None
    ) -> None:
        """Reindex a changed file with directory-specific chunk_tokens."""
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            logger.debug("Watcher change ignored for missing/non-file path: %s", path)
            return
        result = await self._index_fn(path, chunk_tokens)
        logger.info(
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

    async def _emit(self, event: Event) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish_async(event)

    def get_status(self) -> list[dict[str, str | int | bool]]:
        """Return watcher status for diagnostics endpoints."""
        statuses: list[dict[str, str | int | bool]] = []
        for watcher in self._watchers:
            raw = watcher.get_status()
            statuses.append(
                {
                    "path": str(raw.get("path", "")),
                    "enabled": bool(raw.get("enabled", False)),
                    "reload_count": int(raw.get("reload_count", 0)),
                    "error_count": int(raw.get("error_count", 0)),
                }
            )
        return statuses
