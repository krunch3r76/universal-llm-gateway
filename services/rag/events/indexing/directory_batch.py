"""RAG indexing event factories — directory batch and property index rebuild."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


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
