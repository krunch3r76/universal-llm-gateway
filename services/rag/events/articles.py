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
    pipeline_stage: str = "registered",
    queue_state: str | None = None,
    queue_depth: int = 0,
    frontier_status: str = "unknown",
) -> Event:
    """Emitted after an article metadata row is inserted or updated.

    pipeline_stage: coarse stage label (registered|queued|chunked|contextualized).
    queue_state: precise extraction_queue state when pipeline_stage == "queued", else None.
    Subscribers filter stalled items via:
      pipeline_stage == "queued" and queue_state in ("cooling_off", "capacity_blocked", "exhausted")
    """
    return Event(
        signal="rag.article.upserted",
        payload={
            "source_path": source_path,
            "created": created,
            "title": title,
            "content_hash": content_hash,
            "pipeline_stage": pipeline_stage,
            "queue_state": queue_state,
            "queue_depth": queue_depth,
            "frontier_status": frontier_status,
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
def rag_article_content_hash_mismatch(
    *,
    file: str,
    expected_hash: str,
    actual_hash: str,
) -> Event:
    """Emitted when a file's content hash differs from the article registry record."""
    return Event(
        signal="rag.article.content.hash.mismatch",
        payload={
            "file": file,
            "expected_hash": expected_hash,
            "actual_hash": actual_hash,
        },
    )


@event_factory
def rag_article_path_moved(
    *,
    old_path: str,
    new_path: str,
    content_hash: str,
) -> Event:
    """Emitted when indexing detects a file move by content hash.

    Covers both SQLite article row migration (when a matching orphan row exists)
    and Chroma-only migration (when the old path has chunks in Chroma but no
    article row). Emitted once per move detection.
    """
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
