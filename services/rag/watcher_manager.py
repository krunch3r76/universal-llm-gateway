"""Inotify file watcher and reconciliation sweep manager for RAG indexing.

Watches configured directories for file changes (create, modify, delete) and
triggers re-indexing. Reconciliation sweeps run periodically to catch changes
missed by inotify (e.g. during downtime, NFS mounts, bulk copies).

The watcher uses a shared worker pool for both initial reindex and reconcile
sweeps, with adaptive intervals: 30s between sweeps when files are recovered,
full ``reconcile_interval_s`` otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from universal_hot_reload import HotReloadWatcher, matches_watch_exclude

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
    rag_watchers_registered,
)

if TYPE_CHECKING:
    from universal_event_bus import Event, EventBus

logger = logging.getLogger(__name__)

# Reconciliation: re-sweep watched dirs to recover files missed by initial sweep.
# ∀ file ∈ watched_dir: if not in index → index now.
# Interval is configurable via RagConfig.reconcile_interval_s; 0 = disabled.
_RECONCILE_INTERVAL_S = 300.0

# When a reconcile sweep recovers files, use a shorter interval before re-sweeping
# since more outstanding work likely remains (e.g. extraction failures retrying).
_RECONCILE_BUSY_INTERVAL_S = 30.0


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


class WatcherManager:
    """Manages HotReloadWatcher instances for configured directories.

    The file-system watcher only fires for changes after it starts.
    Files absent from the index (e.g. due to startup embedding failures) are
    recovered by a periodic reconciliation sweep that calls _index_fn on every
    watched file; _index_fn returns unchanged=True immediately for files already
    indexed, so the sweep overhead is dominated by filesystem traversal plus
    bounded progress telemetry rather than content reads.
    """

    def __init__(
        self,
        index_fn: IndexFn,
        event_bus: EventBus | None = None,
        reconcile_interval_s: float = _RECONCILE_INTERVAL_S,
        delete_fn: DeleteFn | None = None,
        index_workers: int = 8,
        reconcile_workers: int = 3,
        post_reconcile_repair: PostReconcileRepairFn | None = None,
        scope_repair_runner: ScopeRepairRunnerFn | None = None,
        scope_repair_debounce_s: float = 30.0,
    ) -> None:
        self._index_fn: IndexFn = index_fn
        self._delete_fn: DeleteFn = (
            delete_fn  # Assuming it's always provided or handled upstream
        )
        self._event_bus: EventBus | None = event_bus
        self._reconcile_interval_s = reconcile_interval_s
        self._index_workers = max(1, index_workers)
        self._reconcile_workers = max(1, reconcile_workers)
        self._post_reconcile_repair: PostReconcileRepairFn | None = (
            post_reconcile_repair
        )
        self._scope_repair_runner: ScopeRepairRunnerFn | None = scope_repair_runner
        self._scope_repair_debounce_s = max(1.0, scope_repair_debounce_s)
        self._watchers: list[HotReloadWatcher] = []
        self._watch_configs: list[WatchDirectory] = []
        self._initial_reindex_tasks: list[asyncio.Task[None]] = []
        self._reconcile_task: asyncio.Task[None] | None = None
        self._repair_debounce_task: asyncio.Task[None] | None = None
        self._pending_repair_scopes: set[str] = set()
        self._rag_config: RagConfig | None = None
        self._baseline_extensions: tuple[str, ...] = _normalize_extensions(
            BASELINE_EXTENSIONS
        )

    async def start(self, config: RagConfig) -> None:
        """Start watchers for all configured directories.

        Watcher registration (inotify fd setup) runs concurrently for all
        directories. Initial reindexes are fired as background tasks so this
        method returns as soon as watchers are live. Use
        ``wait_for_initial_indexing()`` to block until reindexes finish.
        """
        if self._reconcile_task is not None:
            raise RuntimeError(
                "WatcherManager.start() called while already running; call stop() first"
            )
        self._rag_config = config
        self._watchers = []
        self._watch_configs = []
        self._initial_reindex_tasks = []
        self._baseline_extensions = (
            _normalize_extensions(config.baseline_extensions)
            or self._baseline_extensions
        )

        registrations = await asyncio.gather(
            *[self._register_one(wd) for wd in config.watch_directories]
        )

        if self._watch_configs:
            await self._emit(
                rag_watchers_registered(
                    count=len(self._watch_configs),
                    paths=[
                        str(Path(wd.path).expanduser().resolve())
                        for wd in self._watch_configs
                    ],
                )
            )

        for result in registrations:
            if result is not None:
                watch_path, watch_directory, effective_extensions = result
                task = asyncio.create_task(
                    self._initial_reindex(
                        watch_path, watch_directory, effective_extensions
                    ),
                    name=f"rag-initial-reindex:{watch_path.name}",
                )
                self._initial_reindex_tasks.append(task)

        if self._watch_configs and self._reconcile_interval_s > 0:
            self._reconcile_task = asyncio.create_task(
                self._reconcile_loop(), name="rag-watcher-reconcile"
            )

    async def stop(self) -> None:
        """Stop all watchers, background reindexes, and the reconciliation loop."""
        if self._repair_debounce_task is not None:
            self._repair_debounce_task.cancel()
            try:
                await self._repair_debounce_task
            except asyncio.CancelledError:
                pass
            self._repair_debounce_task = None
        self._pending_repair_scopes.clear()
        tasks_to_cancel: list[asyncio.Task[None]] = []
        if self._reconcile_task is not None:
            self._reconcile_task.cancel()
            tasks_to_cancel.append(self._reconcile_task)
        for task in self._initial_reindex_tasks:
            if not task.done():
                task.cancel()
            tasks_to_cancel.append(task)
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        self._reconcile_task = None
        self._initial_reindex_tasks = []
        count = len(self._watchers)
        for watcher in self._watchers:
            await watcher.stop()
        self._watchers = []
        self._watch_configs = []
        self._rag_config = None
        await self._emit(rag_watch_stopped(watchers=count))

    def _schedule_scope_freshness_repair(self, scope: str) -> None:
        if self._scope_repair_runner is None:
            return
        self._pending_repair_scopes.add(scope)
        if self._repair_debounce_task is not None:
            self._repair_debounce_task.cancel()
        self._repair_debounce_task = asyncio.create_task(
            self._scope_repair_debounce_worker(),
            name="rag-scope-freshness-debounce",
        )

    async def _scope_repair_debounce_worker(self) -> None:
        try:
            await asyncio.sleep(self._scope_repair_debounce_s)
            scopes = set(self._pending_repair_scopes)
            if scopes and self._scope_repair_runner is not None:
                self._pending_repair_scopes.clear()  # Clear only after copying
                await self._scope_repair_runner(scopes)
            else:
                self._pending_repair_scopes.clear()  # Clear even if no runner or no scopes
        except asyncio.CancelledError:
            return
        finally:
            self._repair_debounce_task = None

    def _note_index_mutation(self, file_path: Path, result: IndexOutcome) -> None:
        if result.unchanged or result.indexed <= 0:
            return
        if self._scope_repair_runner is None or self._rag_config is None:
            return
        scope = self._rag_config.get_scope_for_path(
            str(file_path.expanduser().resolve())
        )
        self._schedule_scope_freshness_repair(scope)

    async def _register_one(
        self, watch_directory: WatchDirectory
    ) -> tuple[Path, WatchDirectory, tuple[str, ...]] | None:
        """Register a single inotify watcher without running initial reindex.

        Returns ``(watch_path, watch_directory, effective_extensions)`` on
        success, or ``None`` if the directory is missing or the watcher failed.
        """
        watch_path = Path(watch_directory.path).expanduser().resolve()
        if not watch_path.exists() or not watch_path.is_dir():
            logger.warning("Watch directory missing; skipping: %s", watch_path)
            await self._emit(rag_watch_directory_missing(path=str(watch_path)))
            return None

        effective_extensions = _effective_extensions(
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
            return watch_path, watch_directory, effective_extensions
        return None

    async def register_directory(self, watch_directory: WatchDirectory) -> bool:
        """Add a new watch directory at runtime (no restart required).

        Registers the inotify watcher and fires the initial reindex as a
        background task. Returns True if the watcher started successfully.
        """
        result = await self._register_one(watch_directory)
        if result is None:
            return False
        watch_path, wd, effective_extensions = result
        task = asyncio.create_task(
            self._initial_reindex(watch_path, wd, effective_extensions),
            name=f"rag-initial-reindex:{watch_path.name}",
        )
        self._initial_reindex_tasks.append(task)
        return True

    async def wait_for_initial_indexing(self, timeout: float | None = None) -> bool:
        """Block until all background initial reindex tasks complete.

        Returns True if all completed within *timeout*, False on timeout.
        Passing ``timeout=None`` waits indefinitely.
        """
        pending = [t for t in self._initial_reindex_tasks if not t.done()]
        if not pending:
            return True
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
            return True
        except TimeoutError:
            return False

    async def _reconcile_loop(self) -> None:
        """Periodically re-sweep watched directories to recover missed files.

        Uses the same worker-pool pattern as _initial_reindex for concurrent
        processing. Adaptive interval: _RECONCILE_BUSY_INTERVAL_S when files
        were recovered (outstanding work likely remains), full
        reconcile_interval_s when idle.
        """
        if self._initial_reindex_tasks:
            await asyncio.gather(*self._initial_reindex_tasks, return_exceptions=True)
        while True:
            ext_sets = [
                frozenset(_effective_extensions(wd, self._baseline_extensions))
                for wd in self._watch_configs
            ]
            total_recovered = 0
            recovered_roots: list[str] = []
            try:
                for watch_directory, extensions in zip(self._watch_configs, ext_sets):
                    wp = Path(watch_directory.path).expanduser().resolve()
                    recovered = await self._reconcile_directory(
                        watch_directory, extensions
                    )
                    total_recovered += recovered
                    if recovered > 0:
                        recovered_roots.append(str(wp))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Reconcile loop iteration failed unexpectedly: %s", exc
                )
            if total_recovered > 0 and self._post_reconcile_repair is not None:
                try:
                    await self._post_reconcile_repair(recovered_roots)
                except Exception:
                    logger.exception("post_reconcile_repair failed")
            interval = (
                _RECONCILE_BUSY_INTERVAL_S
                if total_recovered > 0
                else self._reconcile_interval_s
            )
            await asyncio.sleep(interval)

    async def _reconcile_directory(
        self,
        watch_directory: WatchDirectory,
        extensions: frozenset[str],
    ) -> int:
        """Sweep one directory with worker pool; return count of recovered files."""
        watch_path = Path(watch_directory.path).expanduser().resolve()
        if not watch_path.exists():
            return 0
        exclude = watch_directory.exclude
        walker = (
            watch_path.rglob("*") if watch_directory.recursive else watch_path.glob("*")
        )
        file_paths: list[Path] = [
            fp
            for fp in walker
            if fp.is_file()
            and fp.suffix.lower() in extensions
            and not matches_watch_exclude(fp, watch_root=watch_path, patterns=exclude)
        ]
        if not file_paths:
            return 0

        chunk_tokens = watch_directory.chunk_tokens
        queue: asyncio.Queue[Path | None] = asyncio.Queue()
        for fp in file_paths:
            queue.put_nowait(fp)

        async def _worker() -> tuple[int, int]:
            worker_recovered = 0
            worker_unchanged = 0
            while True:
                fp = await queue.get()
                if fp is None:
                    queue.task_done()
                    return worker_recovered, worker_unchanged
                try:
                    result = await self._index_fn(
                        fp,
                        chunk_tokens,
                        emit_skip_event=False,
                    )
                    self._note_index_mutation(fp, result)
                    if result.unchanged:
                        worker_unchanged += 1
                    elif result.indexed > 0:
                        worker_recovered += 1
                        logger.info(
                            "Reconcile recovered: file=%s indexed=%d",
                            result.file,
                            result.indexed,
                        )
                except Exception as exc:
                    logger.warning("Reconcile skipped %s: %s", fp, exc, exc_info=True)
                    await self._emit(
                        rag_file_indexing_failed(file=str(fp), error=str(exc))
                    )
                finally:
                    queue.task_done()

        n_workers = min(self._reconcile_workers, len(file_paths))
        workers = [
            asyncio.create_task(_worker(), name=f"reconcile-worker-{i}")
            for i in range(n_workers)
        ]
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)
        worker_counts = await asyncio.gather(*workers)
        recovered = sum(r for r, _ in worker_counts)
        unchanged = sum(u for _, u in worker_counts)

        if recovered:
            await self._emit(
                rag_watch_reconcile_complete(
                    path=str(watch_path),
                    recovered=recovered,
                    unchanged=unchanged,
                )
            )
        return recovered

    async def _initial_reindex(
        self,
        watch_path: Path,
        watch_directory: WatchDirectory,
        effective_extensions: tuple[str, ...],
    ) -> None:
        """Run startup sweep for one watch path and emit summary telemetry."""
        walker = (
            watch_path.rglob("*") if watch_directory.recursive else watch_path.glob("*")
        )
        extensions = set(effective_extensions)
        exclude = watch_directory.exclude
        file_paths: list[Path] = []
        for file_path in walker:
            if not file_path.is_file() or file_path.suffix.lower() not in extensions:
                continue
            if matches_watch_exclude(
                file_path, watch_root=watch_path, patterns=exclude
            ):
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
        # Emit a progress event approximately every 10% of total_files so
        # rag-status --watch can show a live sweep counter.
        _progress_every = max(1, total_files // 10)

        async def _maybe_emit_progress(
            processed: int,
            reindexed: int,
            unchanged: int,
            errors: int,
        ) -> None:
            """Emit rag.watch.initial.progress at ~10% checkpoints."""
            if processed % _progress_every == 0:
                await self._emit(
                    rag_watch_initial_progress(
                        path=str(watch_path),
                        total_files=total_files,
                        processed=processed,
                        reindexed=reindexed,
                        unchanged=unchanged,
                        errors=errors,
                    )
                )

        queue: asyncio.Queue[Path | None] = asyncio.Queue()
        for fp in file_paths:
            queue.put_nowait(fp)

        async def _worker() -> None:
            nonlocal reindexed_total, unchanged_total, error_total
            while True:
                fp = await queue.get()
                if fp is None:
                    queue.task_done()
                    return
                try:
                    result = await self._index_fn(
                        fp,
                        chunk_tokens,
                        emit_skip_event=False,
                    )
                    self._note_index_mutation(fp, result)
                    async with progress_lock:
                        if result.unchanged:
                            unchanged_total += 1
                        else:
                            reindexed_total += 1
                        snap = (
                            reindexed_total + unchanged_total + error_total,
                            reindexed_total,
                            unchanged_total,
                            error_total,
                        )
                    await _maybe_emit_progress(*snap)
                except Exception as exc:
                    logger.warning(
                        "Initial reindex skipped for %s: %s", fp, exc, exc_info=True
                    )
                    await self._emit(
                        rag_file_indexing_failed(file=str(fp), error=str(exc))
                    )
                    async with progress_lock:
                        error_total += 1
                        snap = (
                            reindexed_total + unchanged_total + error_total,
                            reindexed_total,
                            unchanged_total,
                            error_total,
                        )
                    await _maybe_emit_progress(*snap)
                finally:
                    queue.task_done()

        n_workers = min(self._index_workers, total_files)
        workers = [
            asyncio.create_task(_worker(), name=f"reindex-worker-{i}")
            for i in range(n_workers)
        ]

        try:
            await queue.join()
        except asyncio.CancelledError:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

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
        self._note_index_mutation(path, result)
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
