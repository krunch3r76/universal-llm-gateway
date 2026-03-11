from __future__ import annotations

from typing import Any

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
def rag_watch_initial_complete(
    *,
    path: str,
    files: int,
    reindexed: int,
    unchanged: int,
) -> Event:
    return Event(
        signal="rag.watch.initial.complete",
        payload={
            "path": path,
            "files": files,
            "reindexed": reindexed,
            "unchanged": unchanged,
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
def rag_scope_resolved(
    *,
    scope: str | list[str],
    prefix_count: int,
) -> Event:
    return Event(
        signal="rag.scope.resolved",
        payload={"scope": scope, "prefix_count": prefix_count},
    )


@event_factory
def rag_scope_rejected(
    *,
    scope: str | list[str],
    reason: str,
    available: list[str],
) -> Event:
    return Event(
        signal="rag.scope.rejected",
        payload={"scope": scope, "reason": reason, "available": available},
    )


@event_factory
def rag_scopes_listed(*, count: int) -> Event:
    return Event(signal="rag.scopes.listed", payload={"count": count})


@event_factory
def rag_extraction_completed(
    *,
    chunk_id: str,
    entities: int,
    topics: int,
) -> Event:
    return Event(
        signal="rag.extraction.completed",
        payload={"chunk_id": chunk_id, "entities": entities, "topics": topics},
    )


@event_factory
def rag_extraction_failed(
    *,
    chunk_id: str,
    error: str,
) -> Event:
    return Event(
        signal="rag.extraction.failed",
        payload={"chunk_id": chunk_id, "error": error},
    )


@event_factory
def rag_extraction_permanently_skipped(
    *,
    chunk_id: str,
    source: str,
    attempt_count: int,
) -> Event:
    """Emitted when a chunk crosses max_extraction_attempts and is permanently abandoned.

    ∀ chunk_id: emitted exactly once, on the attempt that causes attempt_count >= max_attempts.
    Persisted in failed_extractions.permanent = 1.
    """
    return Event(
        signal="rag.extraction.permanently.skipped",
        payload={
            "chunk_id": chunk_id,
            "source": source,
            "attempt_count": attempt_count,
        },
    )


@event_factory
def rag_extraction_batch_started(
    *,
    file: str,
    chunk_count: int,
) -> Event:
    return Event(
        signal="rag.extraction.batch.started",
        payload={"file": file, "chunk_count": chunk_count},
    )


@event_factory
def rag_extraction_batch_completed(
    *,
    file: str,
    chunk_count: int,
    successful: int,
    written: int,
    duration_seconds: float,
    extraction_model: str | None = None,
) -> Event:
    """Emitted after an extraction batch finishes.

    successful: chunks for which the pipeline returned a valid result (may be
        less than chunk_count on partial pipeline failure).
    written: chunks whose extraction metadata was committed (0 when the
        all-or-nothing rule fires due to partial failure; equals successful
        when all chunks succeed).
    extraction_model: model id used for extraction when configured.
    """
    payload: dict[str, Any] = {
        "file": file,
        "chunk_count": chunk_count,
        "successful": successful,
        "written": written,
        "duration_seconds": duration_seconds,
        **({} if extraction_model is None else {"extraction_model": extraction_model}),
    }
    return Event(signal="rag.extraction.batch.completed", payload=payload)


@event_factory
def rag_extraction_model_mismatch(
    *,
    file: str,
    expected_model: str,
    chunk_count: int,
) -> Event:
    """Emitted when re-extraction is triggered because existing chunks have different or missing extraction_model."""
    return Event(
        signal="rag.extraction.model.mismatch",
        payload={
            "file": file,
            "expected_model": expected_model,
            "chunk_count": chunk_count,
        },
    )


@event_factory
def rag_extraction_batch_skipped(
    *,
    file: str,
    chunk_count: int,
    skipped_count: int,
    max_attempts: int,
) -> Event:
    """Emitted when all chunks in a batch have exceeded max_extraction_attempts.

    ∀ chunk_id ∈ batch: attempt_count >= max_attempts ⟹ batch skipped entirely.
    No pipeline call is made; extraction is permanently abandoned for these chunks.
    """
    return Event(
        signal="rag.extraction.batch.skipped",
        payload={
            "file": file,
            "chunk_count": chunk_count,
            "skipped_count": skipped_count,
            "max_attempts": max_attempts,
        },
    )


@event_factory
def rag_property_index_rebuilt(
    *,
    collection: str,
    count: int,
) -> Event:
    return Event(
        signal="rag.property.index.rebuilt",
        payload={"collection": collection, "count": count},
    )


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
def rag_file_indexed(
    *,
    file: str,
    deleted: int,
    indexed: int,
    duration_seconds: float = 0.0,
    batch_start_ts: str | None = None,
    article_title: str | None = None,
    article_authors: str | None = None,
    article_venue: str | None = None,
    published_date: str | None = None,
    article_doi: str | None = None,
) -> Event:
    """Emitted after a file is fully indexed into both ChromaDB and the property index.

    batch_start_ts: optional ISO-8601 when extraction started (enables per-file wall-clock duration).
    """
    payload: dict[str, Any] = {
        "file": file,
        "deleted": deleted,
        "indexed": indexed,
        "duration_seconds": duration_seconds,
        **({} if batch_start_ts is None else {"batch_start_ts": batch_start_ts}),
        **({} if article_title is None else {"article_title": article_title}),
        **({} if article_authors is None else {"article_authors": article_authors}),
        **({} if article_venue is None else {"article_venue": article_venue}),
        **({} if published_date is None else {"published_date": published_date}),
        **({} if article_doi is None else {"article_doi": article_doi}),
    }
    return Event(signal="rag.file.indexed", payload=payload)


@event_factory
def rag_file_deleted(
    *,
    file: str,
    deleted: int,
) -> Event:
    """Emitted when all chunks for a file are deleted with no replacement (empty file)."""
    return Event(
        signal="rag.file.deleted",
        payload={"file": file, "deleted": deleted},
    )


@event_factory
def rag_file_skipped(
    *,
    file: str,
    reason: str,
) -> Event:
    """Emitted when a file is skipped during indexing (unchanged or duplicate PDF)."""
    return Event(
        signal="rag.file.skipped",
        payload={"file": file, "reason": reason},
    )


@event_factory
def rag_file_indexing_failed(
    *,
    file: str,
    error: str,
) -> Event:
    """Emitted when an unhandled error aborts file indexing."""
    return Event(
        signal="rag.file.indexing.failed",
        payload={"file": file, "error": error},
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
def rag_search_executed(
    *,
    query_len: int,
    top_k: int,
    results: int,
    scope: str | list[str] | None,
) -> Event:
    """Emitted after a search query completes."""
    return Event(
        signal="rag.search.executed",
        payload={
            "query_len": query_len,
            "top_k": top_k,
            "results": results,
            "scope": scope,
        },
    )


@event_factory
def rag_search_no_results(
    *,
    query_len: int,
    scope: str | list[str] | None,
) -> Event:
    """Emitted when a search returns zero results."""
    return Event(
        signal="rag.search.no_results",
        payload={"query_len": query_len, "scope": scope},
    )


@event_factory
def rag_corpus_hints_updated(
    *,
    path: str,
    scopes_updated: list[str],
    timestamp: str,
) -> Event:
    """Emitted after corpus_hints.yaml is written following aggregation from the property index."""
    return Event(
        signal="rag.corpus_hints.updated",
        payload={
            "path": path,
            "scopes_updated": scopes_updated,
            "timestamp": timestamp,
        },
    )


@event_factory
def rag_directory_cleared(
    *,
    path: str,
    sources_cleared: int,
    chunks_cleared: int,
) -> Event:
    """Emitted after all chunks for sources under a directory are deleted.

    Fired by POST /clear_directory and by reindex_directory when force=True
    (upfront clear before re-indexing).
    """
    return Event(
        signal="rag.directory.cleared",
        payload={
            "path": path,
            "sources_cleared": sources_cleared,
            "chunks_cleared": chunks_cleared,
        },
    )


@event_factory
def rag_directory_index_started(
    *,
    path: str,
    total_files: int,
) -> Event:
    """Emitted before concurrent directory indexing dispatch begins.

    ∀ concurrent reindex: emitted once, listing the directory and file count
    so an interrupted session is diagnosable via the event log.
    total_files: number of files that will be dispatched (before any are processed).
    """
    return Event(
        signal="rag.directory.index.started",
        payload={"path": path, "total_files": total_files},
    )


@event_factory
def rag_directory_index_completed(
    *,
    path: str,
    total_files: int,
    indexed: int,
    deleted: int,
    unchanged: int,
    duplicates: int,
    errors: int,
) -> Event:
    """Emitted after all files in a directory index/reindex have been processed.

    Absence of this signal following rag.directory.index.started indicates
    an interrupted session — re-run reindex_directory to recover.
    errors: files that raised an exception and were passed to on_index_error.
    """
    return Event(
        signal="rag.directory.index.completed",
        payload={
            "path": path,
            "total_files": total_files,
            "indexed": indexed,
            "deleted": deleted,
            "unchanged": unchanged,
            "duplicates": duplicates,
            "errors": errors,
        },
    )
