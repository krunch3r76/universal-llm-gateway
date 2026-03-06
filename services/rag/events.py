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
    scope: str,
    prefix_count: int,
) -> Event:
    return Event(
        signal="rag.scope.resolved",
        payload={"scope": scope, "prefix_count": prefix_count},
    )


@event_factory
def rag_scope_rejected(
    *,
    scope: str,
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
    duration_seconds: float,
) -> Event:
    return Event(
        signal="rag.extraction.batch.completed",
        payload={
            "file": file,
            "chunk_count": chunk_count,
            "successful": successful,
            "duration_seconds": duration_seconds,
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
def rag_file_indexed(
    *,
    file: str,
    deleted: int,
    indexed: int,
) -> Event:
    """Emitted after a file is fully indexed into both ChromaDB and the property index."""
    return Event(
        signal="rag.file.indexed",
        payload={"file": file, "deleted": deleted, "indexed": indexed},
    )


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
def rag_search_executed(
    *,
    query_len: int,
    top_k: int,
    results: int,
    scope: str | None,
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
    scope: str | None,
) -> Event:
    """Emitted when a search returns zero results."""
    return Event(
        signal="rag.search.no_results",
        payload={"query_len": query_len, "scope": scope},
    )
