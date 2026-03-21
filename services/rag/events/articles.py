"""RAG article metadata event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_article_auto_created(
    *,
    source_path: str,
    content_hash: str,
    scope: str,
) -> Event:
    """Emitted when indexing creates a minimal article row for a source."""
    return Event(
        signal="rag.article.auto.created",
        payload={
            "source_path": source_path,
            "content_hash": content_hash,
            "scope": scope,
        },
    )


@event_factory
def rag_article_upserted(
    *,
    source_path: str,
    created: bool,
    title: str = "",
    content_hash: str = "",
) -> Event:
    return Event(
        signal="rag.article.upserted",
        payload={
            "source_path": source_path,
            "created": created,
            "title": title,
            "content_hash": content_hash,
        },
    )


@event_factory
def rag_source_deleted(
    *,
    source: str,
    chunks_deleted: int,
    article_deleted: bool,
) -> Event:
    """Emitted after DELETE /source removes a file from all storage surfaces."""
    return Event(
        signal="rag.source.deleted",
        payload={
            "source": source,
            "chunks_deleted": chunks_deleted,
            "article_deleted": article_deleted,
        },
    )


@event_factory
def rag_article_path_moved(
    *,
    old_path: str,
    new_path: str,
    content_hash: str,
) -> Event:
    """Emitted when indexing migrates an article row to a new source path."""
    return Event(
        signal="rag.article.path.moved",
        payload={
            "old_path": old_path,
            "new_path": new_path,
            "content_hash": content_hash,
        },
    )


@event_factory
def rag_directory_sources_deleted(
    *,
    path: str,
    sources_deleted: int,
    chunks_deleted: int,
    articles_deleted: int,
) -> Event:
    """Emitted after DELETE /directory removes all sources under a prefix."""
    return Event(
        signal="rag.directory.sources.deleted",
        payload={
            "path": path,
            "sources_deleted": sources_deleted,
            "chunks_deleted": chunks_deleted,
            "articles_deleted": articles_deleted,
        },
    )
