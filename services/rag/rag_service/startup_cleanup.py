"""Startup reconciliation, orphan / exclusion purges, and watch chunk-token resolution."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
from universal_hot_reload import matches_watch_exclude

from services.rag.config import DEFAULT_INDEX_WORKERS, RagConfig
from services.rag.directory_ops import (
    delete_sources,
    find_sources_under_prefixes,
    purge_orphaned_sources,
)
from services.rag.events.lifecycle import (
    rag_exclusion_purged,
    rag_orphan_purged,
    rag_pending_reconciled,
)

from . import indexing, state
from .lifecycle_constants import RECONCILE_FILE_TIMEOUT_S

logger = logging.getLogger(__name__)


async def _reconcile_pending(config: RagConfig) -> None:
    """Reconcile pending files after interrupted indexing operations."""
    if state._property_index is None:
        return
    try:
        pending_files = state._property_index.get_pending_files()
    except Exception:
        logger.error(
            "Failed to read pending files during startup reconciliation",
            exc_info=True,
        )
        return
    if not pending_files:
        return

    worker_count = (
        config.index_workers
        if isinstance(config.index_workers, int)
        else DEFAULT_INDEX_WORKERS
    )
    logger.warning(
        "Reconciling %d files pending from interrupted indexing",
        len(pending_files),
    )
    reconciled = cleared = failed_transient = failed_permanent = 0

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    for source in pending_files:
        file_path = Path(source)
        if not file_path.exists():
            await state._property_index.clear_pending(source)
            logger.info("Pending file removed (deleted): %s", source)
            cleared += 1
            continue
        queue.put_nowait(source)

    async def _reconcile_worker() -> None:
        nonlocal reconciled, failed_transient, failed_permanent
        while True:
            src = await queue.get()
            if src is None:
                queue.task_done()
                return
            try:
                ct = _resolve_chunk_tokens_for_file(Path(src), config)
                await asyncio.wait_for(
                    indexing._index_file(Path(src), chunk_tokens=ct),
                    timeout=RECONCILE_FILE_TIMEOUT_S,
                )
                reconciled += 1
            except TimeoutError:
                logger.warning(
                    "Reconciliation timed out for %s after %.0fs; "
                    "will retry on next sweep",
                    src,
                    RECONCILE_FILE_TIMEOUT_S,
                )
                failed_transient += 1
            except (
                ConnectionError,
                httpx.TimeoutException,
                httpx.ConnectError,
            ) as e:
                logger.warning(
                    "Transient error reconciling %s; will retry on next sweep: %r",
                    src,
                    e,
                )
                failed_transient += 1
            except httpx.HTTPStatusError as e:
                if e.response.is_server_error:
                    logger.warning(
                        "Transient %d reconciling %s; will retry on next sweep",
                        e.response.status_code,
                        src,
                    )
                    failed_transient += 1
                else:
                    logger.error(
                        "Permanent HTTP error reconciling %s: %s",
                        src,
                        e,
                    )
                    if state._property_index is not None:
                        await state._property_index.clear_pending(src)
                    failed_permanent += 1
            except Exception as e:
                logger.error(
                    "Permanent error reconciling %s; requires manual intervention: %s",
                    src,
                    e,
                    exc_info=True,
                )
                if state._property_index is not None:
                    await state._property_index.clear_pending(src)
                failed_permanent += 1
            finally:
                queue.task_done()

    n_workers = min(worker_count, len(pending_files))
    workers = [
        asyncio.create_task(_reconcile_worker(), name=f"reconcile-worker-{i}")
        for i in range(n_workers)
    ]

    if workers:
        await queue.join()
        for _ in workers:
            queue.put_nowait(None)
        worker_results = await asyncio.gather(*workers, return_exceptions=True)
        for worker_result in worker_results:
            if isinstance(worker_result, BaseException):
                logger.error(
                    "Pending reconcile worker raised unexpectedly: %r",
                    worker_result,
                )

    if state._event_bus is not None:
        await state._event_bus.publish(
            rag_pending_reconciled(
                reconciled=reconciled,
                cleared=cleared,
                failed_transient=failed_transient,
                failed_permanent=failed_permanent,
            )
        )


async def _purge_orphans(config: RagConfig) -> None:
    """Delete watched sources whose backing files disappeared while service was down."""
    if state._collection is None or not config.watch_directories:
        return
    watch_prefixes = [
        str(Path(wd.path).expanduser().resolve()) + "/"
        for wd in config.watch_directories
    ]
    remove_source_fn = (
        state._property_index.remove_source_metadata if state._property_index else None
    )
    list_known_fn = (
        state._property_index.list_known_sources if state._property_index else None
    )
    if remove_source_fn is None or list_known_fn is None:
        logger.warning("Property index not available, skipping orphan purge.")
        return
    files_purged, chunks_purged, purged_sources = await purge_orphaned_sources(
        collection=state._collection,
        watch_prefixes=watch_prefixes,
        remove_source_metadata_fn=remove_source_fn,
        list_known_sources_fn=list_known_fn,
    )
    if files_purged > 0:
        logger.info(
            "Startup orphan purge complete: files=%d chunks=%d sources=%s",
            files_purged,
            chunks_purged,
            sorted(Path(s).name for s in purged_sources),
        )
    source_names = (
        sorted(Path(s).name for s in purged_sources) if purged_sources else None
    )
    if state._event_bus is not None:
        await state._event_bus.publish(
            rag_orphan_purged(
                files=files_purged, chunks=chunks_purged, sources=source_names
            )
        )


async def _purge_excluded_sources(config: RagConfig) -> None:
    """Delete indexed sources that now match exclusion patterns in watch config.

    ∀ watch_dir with exclude patterns: find indexed sources under that prefix,
    match excludes against watch-root-relative paths (and bare filename globs),
    and purge matches. Covers the case where a file was previously indexed but
    later added to the exclude list.
    """
    if state._collection is None or not config.watch_directories:
        return
    sources_to_purge: set[str] = set()
    for wd in config.watch_directories:
        if not wd.exclude:
            continue
        watch_path = Path(wd.path).expanduser().resolve()
        prefix = str(watch_path) + "/"
        known = find_sources_under_prefixes(
            collection=state._collection,
            prefixes=[prefix],
            list_known_sources_fn=(
                state._property_index.list_known_sources
                if state._property_index is not None
                else None
            ),
        )
        for source in known:
            if matches_watch_exclude(
                source, watch_root=watch_path, patterns=wd.exclude
            ):
                sources_to_purge.add(source)
    if not sources_to_purge:
        if state._event_bus is not None:
            await state._event_bus.publish(rag_exclusion_purged(files=0, chunks=0))
        return
    remove_source_fn = (
        state._property_index.remove_source_metadata
        if state._property_index is not None
        else None
    )
    files_purged, chunks_purged = await delete_sources(
        collection=state._collection,
        sources=sources_to_purge,
        remove_source_metadata_fn=remove_source_fn,
    )
    if files_purged > 0:
        logger.info(
            "Startup exclusion purge: files=%d chunks=%d sources=%s",
            files_purged,
            chunks_purged,
            sorted(Path(s).name for s in sources_to_purge),
        )
    source_names = sorted(Path(s).name for s in sources_to_purge)
    if state._event_bus is not None:
        await state._event_bus.publish(
            rag_exclusion_purged(
                files=files_purged, chunks=chunks_purged, sources=source_names
            )
        )


def _resolve_chunk_tokens_for_file(file_path: Path, config: RagConfig) -> int | None:
    """Resolves the chunk token override for a file based on watch directory configurations.

    Checks if the file is within a watched directory, considering recursion, extensions,
    and exclusion patterns. Returns the `chunk_tokens` from the matching watch directory
    or `None` if no override applies.

    Args:
        file_path: The path to the file.
        config: The RAG configuration.

    Returns:
        The `chunk_tokens` override (int) or `None`.
    """
    resolved_file = file_path.expanduser().resolve()
    baseline: set[str] = {f".{ext.lower()}" for ext in config.baseline_extensions}
    for watch_directory in config.watch_directories:
        watch_path = Path(watch_directory.path).expanduser().resolve()
        if not resolved_file.is_relative_to(watch_path):
            continue
        if not watch_directory.recursive and resolved_file.parent != watch_path:
            continue
        effective_extensions: set[str] = (
            {f".{ext.lower()}" for ext in watch_directory.extensions}
            if watch_directory.extensions
            else baseline
        )
        if resolved_file.suffix.lower() not in effective_extensions:
            continue
        if matches_watch_exclude(
            resolved_file,
            watch_root=watch_path,
            patterns=watch_directory.exclude,
        ):
            continue
        return watch_directory.chunk_tokens
    return None
