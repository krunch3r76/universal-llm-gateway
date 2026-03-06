from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def RagStarted() -> Event:  # noqa: N802
    return Event(signal="rag.started", payload={})


@event_factory
def RagShutdown() -> Event:  # noqa: N802
    return Event(signal="rag.shutdown", payload={})


@event_factory
def RagWatchStarted(  # noqa: N802
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
def RagWatchDirectoryMissing(*, path: str) -> Event:  # noqa: N802
    return Event(signal="rag.watch.directory.missing", payload={"path": path})


@event_factory
def RagWatchInitialComplete(  # noqa: N802
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
def RagWatchReindexComplete(  # noqa: N802
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
def RagWatchReconcileComplete(  # noqa: N802
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
def RagWatchStopped(*, watchers: int) -> Event:  # noqa: N802
    return Event(signal="rag.watch.stopped", payload={"watchers": watchers})


@event_factory
def RagScopeResolved(  # noqa: N802
    *,
    scope: str,
    prefix_count: int,
) -> Event:
    return Event(
        signal="rag.scope.resolved",
        payload={"scope": scope, "prefix_count": prefix_count},
    )


@event_factory
def RagScopeRejected(  # noqa: N802
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
def RagScopesListed(*, count: int) -> Event:  # noqa: N802
    return Event(signal="rag.scopes.listed", payload={"count": count})


@event_factory
def RagExtractionCompleted(  # noqa: N802
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
def RagExtractionFailed(  # noqa: N802
    *,
    chunk_id: str,
    error: str,
) -> Event:
    return Event(
        signal="rag.extraction.failed",
        payload={"chunk_id": chunk_id, "error": error},
    )


@event_factory
def RagExtractionBatchStarted(  # noqa: N802
    *,
    file: str,
    chunk_count: int,
) -> Event:
    return Event(
        signal="rag.extraction.batch.started",
        payload={"file": file, "chunk_count": chunk_count},
    )


@event_factory
def RagExtractionBatchCompleted(  # noqa: N802
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
def RagPropertyIndexRebuilt(  # noqa: N802
    *,
    collection: str,
    count: int,
) -> Event:
    return Event(
        signal="rag.property.index.rebuilt",
        payload={"collection": collection, "count": count},
    )
