"""WatcherManager core lifecycle and admission gating."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

from services.rag.config import BASELINE_EXTENSIONS, RagConfig, WatchDirectory
from services.rag.events.indexing import (
    rag_file_indexing_failed,
    rag_file_indexing_failure_skipped,
    rag_file_indexing_gated,
)
from services.rag.events.lifecycle import (
    rag_watch_stopped,
    rag_watchers_registered,
)
from services.rag.watcher_manager.file_events import FileEventsMixin
from services.rag.watcher_manager.initial_reindex import InitialReindexMixin
from services.rag.watcher_manager.protocols import (
    _RECONCILE_INTERVAL_S,
    DeleteFn,
    IndexFn,
    PostReconcileRepairFn,
    ScopeRepairRunnerFn,
    normalize_extensions,
)
from services.rag.watcher_manager.reconcile import ReconcileMixin
from services.rag.watcher_manager.registration import RegistrationMixin
from services.rag.watcher_manager.scope_repair import ScopeRepairMixin

if TYPE_CHECKING:
    from universal_event_bus import Event, EventBus

    from services.rag.entity_admission import EntityAdmissionGate
    from services.rag.property_index import PropertyIndex

logger = get_logger(__name__)


class WatcherManager(
    ScopeRepairMixin,
    ReconcileMixin,
    InitialReindexMixin,
    FileEventsMixin,
    RegistrationMixin,
):
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
        property_index: PropertyIndex | None = None,
        entity_admission_gate: EntityAdmissionGate | None = None,
    ) -> None:
        self._index_fn: IndexFn = index_fn
        self._delete_fn: DeleteFn = delete_fn
        self._event_bus: EventBus | None = event_bus
        self._reconcile_interval_s = reconcile_interval_s
        self._index_workers = max(1, index_workers)
        self._reconcile_workers = max(1, reconcile_workers)
        self._post_reconcile_repair: PostReconcileRepairFn | None = (
            post_reconcile_repair
        )
        self._scope_repair_runner: ScopeRepairRunnerFn | None = scope_repair_runner
        self._scope_repair_debounce_s = max(1.0, scope_repair_debounce_s)
        self._watchers: list = []
        self._watch_configs: list[WatchDirectory] = []
        self._initial_reindex_tasks: list[asyncio.Task[None]] = []
        self._reconcile_task: asyncio.Task[None] | None = None
        self._repair_debounce_task: asyncio.Task[None] | None = None
        self._pending_repair_scopes: set[str] = set()
        self._rag_config: RagConfig | None = None
        self._property_index: PropertyIndex | None = property_index
        self._entity_admission_gate: EntityAdmissionGate | None = entity_admission_gate
        self._baseline_extensions: tuple[str, ...] = normalize_extensions(
            BASELINE_EXTENSIONS
        )

    async def start(self, config: RagConfig) -> None:
        """Start watchers for all configured directories."""
        if self._reconcile_task is not None:
            raise RuntimeError(
                "WatcherManager.start() called while already running; call stop() first"
            )
        self._rag_config = config
        self._watchers = []
        self._watch_configs = []
        self._initial_reindex_tasks = []
        self._baseline_extensions = (
            normalize_extensions(config.baseline_extensions)
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

    async def register_directory(self, watch_directory: WatchDirectory) -> bool:
        """Add a new watch directory at runtime (no restart required)."""
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
        """Block until all background initial reindex tasks complete."""
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

    async def _should_attempt(self, fp: Path, *, entity_gated: bool = False) -> bool:
        """Consult entity-admission gate and indexing_failures before indexing."""
        source = str(fp)
        if (
            entity_gated
            and self._entity_admission_gate is not None
            and not self._entity_admission_gate.is_admitted(source)
        ):
            await self._emit(
                rag_file_indexing_gated(file=source, layer="watcher_sweep")
            )
            return False
        if self._property_index is None:
            return True
        failure = self._property_index.get_indexing_failure(source)
        if failure is None:
            return True
        try:
            st = await asyncio.to_thread(fp.stat)
        except OSError:
            return True
        if (
            failure.source_mtime_ns != st.st_mtime_ns
            or failure.source_size_bytes != st.st_size
        ):
            return True
        if failure.failure_category == "permanent":
            await self._emit(
                rag_file_indexing_failure_skipped(
                    file=source,
                    failure_reason=failure.failure_reason,
                    attempt_count=failure.attempt_count,
                )
            )
            return False
        return True

    async def request_reindex(self, file_path: Path) -> bool:
        """Admin admission: schedule one file for reindex if watchers are live."""
        if self._reconcile_task is None or self._rag_config is None:
            return False
        source = str(file_path)
        chunk_tokens: int | None = None
        for wd in self._watch_configs:
            watch_root = Path(wd.path).expanduser().resolve()
            try:
                file_path.expanduser().resolve().relative_to(watch_root)
            except ValueError:
                continue
            chunk_tokens = wd.chunk_tokens
            break

        async def _run() -> None:
            try:
                result = await self._index_fn(
                    file_path, chunk_tokens, emit_skip_event=False
                )
                self._note_index_mutation(file_path, result)
            except Exception as exc:
                logger.warning(
                    "Requested reindex failed for %s: %s", source, exc, exc_info=True
                )
                await self._emit(rag_file_indexing_failed(file=source, error=str(exc)))

        asyncio.create_task(_run(), name=f"rag-reindex-request:{file_path.name}")
        return True

    async def _emit(self, event: Event) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event)

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
