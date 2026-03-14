"""Shared state and helpers for the RAG service package.

This module centralizes mutable service-level objects so sibling modules can
coordinate without circular imports. It owns startup-populated resources,
indexing locks, and small helper functions used across lifecycle, indexing,
and API routes.
"""

from __future__ import annotations

import asyncio
import logging

import chromadb
from universal_event_bus import EventBus, MinimalEventDebugBroadcaster

from services.rag.article_registry import ArticleEntry
from services.rag.article_registry import get_entry as get_article_entry
from services.rag.config import RagConfig
from services.rag.corpus_hints import update_corpus_hints
from services.rag.events.query import rag_corpus_hints_update_failed
from services.rag.property_index import PropertyIndex
from services.rag.watcher_manager import WatcherManager

logger = logging.getLogger(__name__)

# ChromaDB collection name for knowledge chunks.
COLLECTION_NAME = "knowledge"

_chroma: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None
_watcher_manager: WatcherManager | None = None
_event_bus: EventBus | None = None
_broadcaster: MinimalEventDebugBroadcaster | None = None
_config: RagConfig | None = None
_init_task: asyncio.Task[None] | None = None
_property_index: PropertyIndex | None = None
_registry: dict[str, ArticleEntry] | None = None
_background_tasks: set[asyncio.Task[None]] = set()

# Serialize concurrent indexing of the same file path (watcher + API can race).
_file_index_locks: dict[str, asyncio.Lock] = {}
_post_index_stale: bool = False


def _article_event_kwargs(
    registry: dict[str, ArticleEntry], source: str
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
    return {
        key: str(value)
        for attr, key in field_map.items()
        if (value := getattr(entry, attr))
    }


async def _maybe_update_corpus_hints() -> None:
    """Refresh corpus hints from property index when configured.

    This helper must never fail indexing operations; failures are logged and
    emitted as events for observability while allowing the request path to
    continue.
    """
    if _config is None or _config.corpus_hints_path is None or _property_index is None:
        return
    try:
        await update_corpus_hints(
            _property_index,
            _config.corpus_hints_path,
            event_bus=_event_bus,
        )
    except Exception as e:
        logger.warning(
            "Failed to update corpus hints at %s: %s",
            _config.corpus_hints_path,
            e,
            exc_info=True,
        )
        if _event_bus is not None:
            await _event_bus.publish_async_nowait(
                rag_corpus_hints_update_failed(
                    path=str(_config.corpus_hints_path),
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
    """Return the initialized event bus when startup has completed successfully."""
    return _event_bus
