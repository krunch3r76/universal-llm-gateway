"""RAG article metadata event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


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
