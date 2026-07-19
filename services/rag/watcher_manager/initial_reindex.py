"""Startup initial reindex sweep for one watch directory."""

from __future__ import annotations

import asyncio
import random
from pathlib import Path

from universal_hot_reload import matches_watch_exclude
from universal_logging import get_logger

from services.rag.config import WatchDirectory
from services.rag.events.indexing import rag_file_indexing_failed
from services.rag.events.lifecycle import (
    rag_watch_initial_complete,
    rag_watch_initial_progress,
    rag_watch_initial_started,
)
from services.rag.indexing_failure_classifier import classify_indexing_failure
from services.rag.watcher_manager.protocols import _INITIAL_REINDEX_MAX_ATTEMPTS

logger = get_logger(__name__)


class InitialReindexMixin:
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

        eligible: list[Path] = []
        for fp in file_paths:
            if await self._should_attempt(
                fp, entity_gated=watch_directory.entity_gated
            ):
                eligible.append(fp)
        file_paths = eligible

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
        progress_every = max(1, total_files // 10)

        async def _maybe_emit_progress(
            processed: int,
            reindexed: int,
            unchanged: int,
            errors: int,
        ) -> None:
            if processed % progress_every == 0:
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
                    for attempt in range(_INITIAL_REINDEX_MAX_ATTEMPTS):
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
                            break
                        except Exception as exc:
                            category, _ = classify_indexing_failure(
                                exc, chunk_count=0
                            )
                            if (
                                category == "transient"
                                and attempt < _INITIAL_REINDEX_MAX_ATTEMPTS - 1
                            ):
                                delay = (
                                    1.0
                                    * (2**attempt)
                                    * random.uniform(0.75, 1.25)
                                )
                                await asyncio.sleep(delay)
                                continue
                            logger.warning(
                                "Initial reindex skipped for %s: %s",
                                fp,
                                exc,
                                exc_info=True,
                            )
                            await self._emit(
                                rag_file_indexing_failed(
                                    file=str(fp), error=str(exc)
                                )
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
                            break
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
