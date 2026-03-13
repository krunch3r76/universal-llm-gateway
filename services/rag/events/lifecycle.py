"""RAG lifecycle and watcher event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_started() -> Event:
    return Event(signal="rag.started", payload={})


@event_factory
def rag_shutdown() -> Event:
    return Event(signal="rag.shutdown", payload={})


@event_factory
def rag_watch_started(
    *,
    path: str,
    extensions: list[str],
    recursive: bool,
) -> Event:
    return Event(
        signal="rag.watch.started",
        payload={"path": path, "extensions": extensions, "recursive": recursive},
    )


@event_factory
def rag_watch_directory_missing(*, path: str) -> Event:
    return Event(signal="rag.watch.directory.missing", payload={"path": path})


@event_factory
def rag_watch_initial_started(*, path: str, total_files: int) -> Event:
    """Emitted once per watch path when startup sweep candidate list is finalized."""
    return Event(
        signal="rag.watch.initial.started",
        payload={"path": path, "total_files": total_files},
    )


@event_factory
def rag_watch_initial_progress(
    *,
    path: str,
    total_files: int,
    processed: int,
    reindexed: int,
    unchanged: int,
    errors: int,
) -> Event:
    """Emitted on each terminal file result during startup sweep with monotonic counters."""
    return Event(
        signal="rag.watch.initial.progress",
        payload={
            "path": path,
            "total_files": total_files,
            "processed": processed,
            "reindexed": reindexed,
            "unchanged": unchanged,
            "errors": errors,
        },
    )


@event_factory
def rag_watch_initial_complete(
    *,
    path: str,
    files: int,
    reindexed: int,
    unchanged: int,
    errors: int,
) -> Event:
    """Emitted once per watch path at end of startup sweep.

    Invariant: total_files (from progress) == reindexed + unchanged + errors.
    files is the count of files considered (excludes invalid paths).
    """
    return Event(
        signal="rag.watch.initial.complete",
        payload={
            "path": path,
            "files": files,
            "reindexed": reindexed,
            "unchanged": unchanged,
            "errors": errors,
        },
    )


@event_factory
def rag_watch_reindex_complete(
    *,
    file: str,
    deleted: int,
    indexed: int,
    unchanged: bool,
) -> Event:
    return Event(
        signal="rag.watch.reindex.complete",
        payload={
            "file": file,
            "deleted": deleted,
            "indexed": indexed,
            "unchanged": unchanged,
        },
    )


@event_factory
def rag_watch_file_deleted(
    *,
    file: str,
    deleted: int,
) -> Event:
    """Emitted when watcher deletion removes all chunks for a source file."""
    return Event(
        signal="rag.watch.file.deleted",
        payload={"file": file, "deleted": deleted},
    )


@event_factory
def rag_watch_reconcile_complete(
    *,
    path: str,
    recovered: int,
    unchanged: int,
) -> Event:
    """Emitted after a reconciliation sweep indexes files absent from the store."""
    return Event(
        signal="rag.watch.reconcile.complete",
        payload={"path": path, "recovered": recovered, "unchanged": unchanged},
    )


@event_factory
def rag_watch_stopped(*, watchers: int) -> Event:
    return Event(signal="rag.watch.stopped", payload={"watchers": watchers})


@event_factory
def rag_pending_reconciled(
    *,
    reconciled: int,
    cleared: int,
    failed_transient: int,
    failed_permanent: int,
) -> Event:
    """Emitted after startup reconciliation of files interrupted mid-index."""
    return Event(
        signal="rag.pending.reconciled",
        payload={
            "reconciled": reconciled,
            "cleared": cleared,
            "failed_transient": failed_transient,
            "failed_permanent": failed_permanent,
        },
    )


@event_factory
def rag_article_registry_loaded(*, path: str, article_count: int) -> Event:
    """Emitted when article registry is successfully loaded at startup."""
    return Event(
        signal="rag.article.registry.loaded",
        payload={"path": path, "article_count": article_count},
    )


@event_factory
def rag_article_registry_failed(*, path: str, error: str) -> Event:
    """Emitted when article registry load fails at startup."""
    return Event(
        signal="rag.article.registry.failed",
        payload={"path": path, "error": error},
    )


@event_factory
def rag_article_registry_write_failed(*, path: str, filename: str, error: str) -> Event:
    """Emitted when writing an entry to article registry fails during ingest."""
    return Event(
        signal="rag.article.registry.write.failed",
        payload={"path": path, "filename": filename, "error": error},
    )


@event_factory
def rag_orphan_purged(
    *,
    files: int,
    chunks: int,
) -> Event:
    """Emitted once at startup after purging chunks for files deleted while service was down.

    ∀ source ∈ ChromaDB ∩ watched_prefixes: ¬Path(source).exists() ⟹ purged before watcher starts.
    Emitted even when files=0 so startup sequence is always observable.
    """
    return Event(
        signal="rag.orphan.purged",
        payload={"files": files, "chunks": chunks},
    )


@event_factory
def rag_post_index_stale(*, stale_steps: list[str]) -> Event:
    """Emitted on startup when post-index enrichment steps are older than the last reindex.

    ∀ step ∈ stale_steps: watermarks[step].completed_at < watermarks['reindex'].completed_at
    or watermark is missing entirely. Operator should run the post-index refresh runbook.
    """
    return Event(
        signal="rag.post_index.stale",
        payload={"stale_steps": stale_steps},
    )
