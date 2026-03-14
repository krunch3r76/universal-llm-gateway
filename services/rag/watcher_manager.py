from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from fnmatch import fnmatch
from pathlib import Path
from typing import Protocol

from universal_event_bus import Event, EventBus
from universal_hot_reload.watcher import HotReloadWatcher

from services.rag.config import BASELINE_EXTENSIONS, RagConfig, WatchDirectory
from services.rag.events.indexing import (
    rag_file_deletion_failed,
    rag_file_indexing_failed,
)
from services.rag.events.lifecycle import (
    rag_watch_directory_missing,
    rag_watch_file_deleted,
    rag_watch_initial_complete,
    rag_watch_initial_progress,
    rag_watch_initial_started,
    rag_watch_reconcile_complete,
    rag_watch_reindex_complete,
    rag_watch_started,
    rag_watch_stopped,
)

logger = logging.getLogger(__name__)

# Reconciliation: re-sweep watched dirs to recover files missed by initial sweep.
# ∀ file ∈ watched_dir: if not in index → index now.
# Interval is configurable via RagConfig.reconcile_interval_s; 0 = disabled.
_RECONCILE_INTERVAL_S = 300.0


def _normalize_extensions(extensions: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            f".{ext.strip().lower().lstrip('.')}" for ext in extensions if ext.strip()
        )
    )


def _effective_extensions(
    watch_directory: WatchDirectory,
    baseline_extensions: tuple[str, ...],
) -> tuple[str, ...]:
    configured = _normalize_extensions(watch_directory.extensions)
    if configured:
        return configured
    return _normalize_extensions(baseline_extensions)


class IndexOutcome(Protocol):
    file: str
    deleted: int
    indexed: int
    unchanged: bool


class DeleteOutcome(Protocol):
    file: str
    deleted: int


IndexFn = Callable[[Path, int | None], Awaitable[IndexOutcome]]
DeleteFn = Callable[[Path], Awaitable[DeleteOutcome]]


class WatcherManager:
    """Manages HotReloadWatcher instances for configured directories.

    The file-system watcher only fires for changes after it starts.
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
        delete_fn: DeleteFn | None = None,
        index_workers: int = 8,
    ) -> None:
        self._index_fn: IndexFn = index_fn
        self._delete_fn: DeleteFn | None = delete_fn
        self._event_bus: EventBus | None = event_bus
        self._reconcile_interval_s = reconcile_interval_s
        self._index_workers = max(1, index_workers)
        self._watchers: list[HotReloadWatcher] = []
        self._watch_configs: list[WatchDirectory] = []
        self._reconcile_task: asyncio.Task[None] | None = None
        self._baseline_extensions: tuple[str, ...] = _normalize_extensions(
            BASELINE_EXTENSIONS
        )

    async def start(self, config: RagConfig) -> None:
        """Start watchers and background reconciliation for all configured directories."""
        if self._reconcile_task is not None:
            raise RuntimeError(
                "WatcherManager.start() called while already running; call stop() first"
            )
        self._watchers = []
        self._watch_configs = []
        configured_baseline = _normalize_extensions(config.baseline_extensions)
        self._baseline_extensions = configured_baseline or _normalize_extensions(BASELINE_EXTENSIONS)
        for watch_directory in config.watch_directories:
            await self._start_one(watch_directory)
        if self._watch_configs and self._reconcile_interval_s > 0:
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

        effective_extensions = _effective_extensions(
            watch_directory, self._baseline_extensions
        )
        await self._initial_reindex(watch_path, watch_directory, effective_extensions)
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
            patterns=list(effective_extensions),
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
                    extensions=list(effective_extensions),
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
        await asyncio.sleep(self._reconcile_interval_s)
        while True:
            ext_sets = [
                frozenset(_effective_extensions(wd, self._baseline_extensions))
                for wd in self._watch_configs
            ]
            try:
                for watch_directory, extensions in zip(self._watch_configs, ext_sets):
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
                            logger.warning(
                                "Reconcile skipped %s: %s",
                                file_path,
                                exc,
                                exc_info=True,
                            )
                            await self._emit(
                                rag_file_indexing_failed(
                                    file=str(file_path), error=str(exc)
                                )
                            )
                    if recovered:
                        await self._emit(
                            rag_watch_reconcile_complete(
                                path=str(watch_path),
                                recovered=recovered,
                                unchanged=unchanged,
                            )
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Reconcile loop iteration failed unexpectedly: %s", exc)
            await asyncio.sleep(self._reconcile_interval_s)

    async def _initial_reindex(
        self,
        watch_path: Path,
        watch_directory: WatchDirectory,
        effective_extensions: tuple[str, ...],
    ) -> None:
        """Run startup sweep for one watch path and emit monotonic progress telemetry.

        Invariants: processed == reindexed + unchanged + errors, processed <= total_files.
        """
        walker = (
            watch_path.rglob("*") if watch_directory.recursive else watch_path.glob("*")
        )
        extensions = set(effective_extensions)
        exclude = watch_directory.exclude
        file_paths: list[Path] = []
        for file_path in walker:
            if not file_path.is_file() or file_path.suffix.lower() not in extensions:
                continue
            if any(fnmatch(file_path.name, pat) for pat in exclude):
                continue
            file_paths.append(file_path)

        total_files = len(file_paths)
        await self._emit(
            rag_watch_initial_started(path=str(watch_path), total_files=total_files)
        )
        if total_files == 0:
            await self._emit(
                rag_watch_initial_complete(
                    path=str(watch_path),
                    files=0,
                    reindexed=0,
                    unchanged=0,
                    errors=0,
                )
            )
            return

        chunk_tokens = watch_directory.chunk_tokens
        reindexed_total = 0
        unchanged_total = 0
        error_total = 0
        progress_lock = asyncio.Lock()

        queue: asyncio.Queue[Path | None] = asyncio.Queue()
        for fp in file_paths:
            queue.put_nowait(fp)

        async def _emit_progress_snapshot() -> None:
            async with progress_lock:
                processed = reindexed_total + unchanged_total + error_total
            await self._emit(
                rag_watch_initial_progress(
                    path=str(watch_path),
                    total_files=total_files,
                    processed=processed,
                    reindexed=reindexed_total,
                    unchanged=unchanged_total,
                    errors=error_total,
                )
            )

        async def _worker() -> None:
            nonlocal reindexed_total, unchanged_total, error_total
            while True:
                fp = await queue.get()
                if fp is None:
                    queue.task_done()
                    return
                try:
                    result = await self._index_fn(fp, chunk_tokens)
                    async with progress_lock:
                        if result.unchanged:
                            unchanged_total += 1
                        else:
                            reindexed_total += 1
                    await _emit_progress_snapshot()
                except Exception as exc:
                    logger.warning(
                        "Initial reindex skipped for %s: %s", fp, exc, exc_info=True
                    )
                    await self._emit(
                        rag_file_indexing_failed(file=str(fp), error=str(exc))
                    )
                    async with progress_lock:
                        error_total += 1
                    await _emit_progress_snapshot()
                finally:
                    queue.task_done()

        n_workers = min(self._index_workers, total_files)
        workers = [
            asyncio.create_task(_worker(), name=f"reindex-worker-{i}")
            for i in range(n_workers)
        ]

        await queue.join()

        for _ in workers:
            queue.put_nowait(None)
        await asyncio.gather(*workers)

        file_total = reindexed_total + unchanged_total + error_total
        logger.info(
            "Initial watch reindex complete: path=%s files=%d reindexed=%d unchanged=%d errors=%d",
            watch_path,
            file_total,
            reindexed_total,
            unchanged_total,
            error_total,
        )
        await self._emit(
            rag_watch_initial_complete(
                path=str(watch_path),
                files=file_total,
                reindexed=reindexed_total,
                unchanged=unchanged_total,
                errors=error_total,
            )
        )

    async def _handle_file_change(
        self, file_path: str, chunk_tokens: int | None
    ) -> None:
        """Reindex a changed file triggered by a hot-reload watcher event.

        Errors are caught and logged so a single failed reindex does not
        terminate the watcher callback loop or affect other watched files.
        """
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
        logger.info(
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
