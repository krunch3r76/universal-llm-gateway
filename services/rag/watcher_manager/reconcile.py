"""Periodic reconciliation sweeps to recover missed files."""

from __future__ import annotations

import asyncio
from pathlib import Path

from universal_hot_reload import matches_watch_exclude
from universal_logging import get_logger

from services.rag.config import WatchDirectory
from services.rag.events.indexing import rag_file_indexing_failed
from services.rag.events.lifecycle import (
    rag_watch_reconcile_complete,
    rag_watch_reconcile_failed,
    rag_watch_reconcile_repair_failed,
)
from services.rag.watcher_manager.protocols import (
    _RECONCILE_BUSY_INTERVAL_S,
    effective_extensions,
)

logger = get_logger(__name__)


class ReconcileMixin:
    async def _reconcile_loop(self) -> None:
        """Periodically re-sweep watched directories to recover missed files."""
        if self._initial_reindex_tasks:
            await asyncio.gather(*self._initial_reindex_tasks, return_exceptions=True)
        while True:
            ext_sets = [
                frozenset(effective_extensions(wd, self._baseline_extensions))
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
                await self._emit(rag_watch_reconcile_failed(error=str(exc)))
            if total_recovered > 0 and self._post_reconcile_repair is not None:
                try:
                    await self._post_reconcile_repair(recovered_roots)
                except Exception as exc:
                    logger.exception("post_reconcile_repair failed")
                    await self._emit(
                        rag_watch_reconcile_repair_failed(
                            error=str(exc),
                            roots=recovered_roots,
                        )
                    )
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

        eligible: list[Path] = []
        for fp in file_paths:
            if await self._should_attempt(
                fp, entity_gated=watch_directory.entity_gated
            ):
                eligible.append(fp)
        file_paths = eligible
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
                        logger.debug(
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
