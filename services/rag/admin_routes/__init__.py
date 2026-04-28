"""Admin/CRUD routes for the RAG service.

Provides operational endpoints for indexing and reindexing files/directories,
source lifecycle cleanup, extraction exports, and coverage/status reporting.
These routes coordinate ChromaDB data with SQLite-backed metadata surfaces
(property index, failures, articles) to keep retrieval state coherent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from collections.abc import Callable

    import chromadb
    from universal_event_bus import EventBus

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex
    from services.rag.watcher_manager import WatcherManager

from services.rag.admin_routes._helpers import ArticleRow
from services.rag.admin_routes.articles import register_article_routes
from services.rag.admin_routes.failures import register_failure_routes
from services.rag.admin_routes.indexing import register_indexing_routes
from services.rag.admin_routes.status import register_status_routes
from services.rag.directory_ops import IndexFileFn


def register_admin_routes(
    *,
    index_file_fn: IndexFileFn,
    get_collection_fn: Callable[[], chromadb.Collection],
    get_watcher_manager_fn: Callable[[], WatcherManager | None],
    get_chroma_fn: Callable[[], chromadb.PersistentClient | None],
    set_collection_fn: Callable[[chromadb.Collection], None],
    collection_name: str,
    get_property_index_fn: Callable[[], PropertyIndex | None],
    get_event_bus_fn: Callable[[], EventBus | None] | None = None,
    get_config_fn: Callable[[], RagConfig | None] | None = None,
    refresh_article_registry_from_row_fn: Callable[[ArticleRow | None], None]
    | None = None,
    reconcile_article_registry_delete_fn: Callable[[str, ArticleRow | None], None]
    | None = None,
) -> APIRouter:
    """Register all admin routes onto a new router using closures for shared state."""
    router = APIRouter()
    deps: dict = dict(
        index_file_fn=index_file_fn,
        get_collection_fn=get_collection_fn,
        get_watcher_manager_fn=get_watcher_manager_fn,
        get_chroma_fn=get_chroma_fn,
        set_collection_fn=set_collection_fn,
        collection_name=collection_name,
        get_property_index_fn=get_property_index_fn,
        get_event_bus_fn=get_event_bus_fn,
        get_config_fn=get_config_fn,
        refresh_article_registry_from_row_fn=refresh_article_registry_from_row_fn,
        reconcile_article_registry_delete_fn=reconcile_article_registry_delete_fn,
    )
    register_indexing_routes(router, **deps)
    register_status_routes(router, **deps)
    register_article_routes(router, **deps)
    register_failure_routes(router, **deps)
    return router
