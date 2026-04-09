"""Shared state and helpers for the RAG service package.

This module centralizes mutable service-level objects so sibling modules can
coordinate without circular imports. It owns startup-populated resources,
indexing locks, and small helper functions used across lifecycle, indexing,
and API routes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from services.rag.article_registry import ArticleEntry
from services.rag.article_registry import get_entry as get_article_entry
from services.rag.corpus_hints import update_corpus_hints
from services.rag.events.query import rag_corpus_hints_update_failed

if TYPE_CHECKING:
    import asyncio

    import chromadb
    from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex
    from services.rag.watcher_manager import WatcherManager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DependencyActivationState:
    """Track whether Stargate-backed dependencies are still activating after core boot."""

    phase: str = "booting"
    attempts: int = 0
    waiting_on: str | None = None
    last_error: str | None = None


# ChromaDB collection name for knowledge chunks.
COLLECTION_NAME = "knowledge"

_chroma: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None
_watcher_manager: WatcherManager | None = None
# Legacy extraction tracker removed — use model_availability_tracker singleton.
_event_bus: EventBus | None = None
_broadcaster: MinimalEventDebugBroadcaster | None = None
_config: RagConfig | None = None
_init_task: asyncio.Task[None] | None = None
_property_index: PropertyIndex | None = None
_registry: dict[str, ArticleEntry] | None = None
_background_tasks: set[asyncio.Task[None]] = set()
_dependency_activation = DependencyActivationState()

# Serialize concurrent indexing of the same file path (watcher + API can race).
_file_index_locks: dict[str, asyncio.Lock] = {}
_post_index_stale: bool = False
_extraction_shutdown: asyncio.Event | None = None


def _article_event_kwargs(
    registry: dict[str, ArticleEntry] | None, source: str
) -> dict[str, str]:
    """Return optional article metadata for rag_file_indexed document metadata."""
    entry = get_article_entry(registry, source)
    if entry is None:
        return {}
    field_map = {
        "title": "article_title",
        "authors": "article_authors",
        "venue": "article_venue",
        "published_date": "published_date",
        "doi": "article_doi",
    }
    result = {}
    for attr, key in field_map.items():
        value = getattr(entry, attr, None)
        if value is not None:
            result[key] = str(value)
    return result


async def _maybe_update_corpus_hints() -> None:
    """Refresh corpus hints from property index after successful indexing.

    This helper must never fail indexing operations; failures are logged and
    emitted as events for observability while allowing the request path to
    continue.
    """
    if _property_index is None:
        return
    try:
        await update_corpus_hints(
            _property_index,
            event_bus=_event_bus,
        )
    except Exception as e:
        logger.warning("Failed to update corpus hints: %s", e, exc_info=True)
        if _event_bus is not None:
            await _event_bus.publish_async_nowait(
                rag_corpus_hints_update_failed(
                    path=str(_property_index.db_path),
                    error=str(e),
                )
            )


def _get_collection() -> chromadb.Collection:
    """Return the initialized ChromaDB collection.

    Callers depend on startup ordering: this raises if collection setup has not
    completed.
    """
    assert _collection is not None, "Collection not initialized"
    return _collection


def get_event_bus() -> EventBus | None:
    """Returns the initialized global EventBus instance.

    This function should only be called after the RAG service has completed its
    initialization phase. If called prematurely, it may return None.
    """
    return _event_bus


def refresh_article_registry_from_row(row: dict[str, str] | None) -> None:
    """Refreshes or adds an ArticleEntry in the global registry from a database row.

    The registry is keyed by article basename (filename). If the registry is not
    initialized or the provided row is invalid/empty, no action is taken.

    Args:
        row: A dictionary representing a row from the article database, expected
             to contain keys like 'filename', 'title', 'authors', etc.
    """
    global _registry
    if _registry is None or row is None:
        return
    filename = row.get("filename", "")
    if not filename:
        return
    _registry[filename] = ArticleEntry(
        title=row.get("title", ""),
        authors=row.get("authors", ""),
        venue=row.get("venue", ""),
        published_date=row.get("published_date", ""),
        doi=row.get("doi", ""),
        abstract=row.get("abstract", ""),
        content_hash=row.get("content_hash", ""),
        subdirectory=row.get("subdirectory", ""),
        comments=row.get("comments", ""),
    )


def reconcile_article_registry_delete(
    *,
    source_path: str,
    fallback_row: dict[str, str] | None,
) -> None:
    """Removes or updates an ArticleEntry in the global registry after an article deletion.

    If `fallback_row` is None, the entry corresponding to `source_path` is removed.
    Otherwise, the entry is refreshed using the provided `fallback_row`, which is
    useful in scenarios where an article might be replaced rather than simply deleted.

    Args:
        source_path: The full path to the article file that was deleted.
        fallback_row: An optional dictionary representing a replacement article's
                      database row, if the deletion is part of an update or move.
    """
    global _registry
    if _registry is None:
        return
    filename = Path(source_path).name
    if fallback_row is None:
        _registry.pop(filename, None)
        return
    refresh_article_registry_from_row(fallback_row)
