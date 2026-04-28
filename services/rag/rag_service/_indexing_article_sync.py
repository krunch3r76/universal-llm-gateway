"""Article sync phase: orphan detection and content-hash mismatch check."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from services.rag.article_registry import get_entry as get_article_entry
from services.rag.events.articles import (
    rag_article_content_hash_mismatch,
    rag_article_path_moved,
)
from services.rag.rag_service._indexing_helpers import _derive_subdirectory

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from services.rag.config import RagConfig
    from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)


async def _run_article_sync_phase(
    *,
    source: str,
    source_hash: str,
    file_path: Path,
    prop_index: PropertyIndex | None,
    config: RagConfig,
    event_bus: EventBus | None,
    registry: object | None,
    refresh_article_registry_from_row: object,
) -> str | None:
    """Detect moved articles (orphan → new path) and log content-hash mismatches.

    Returns the old source path when an article row was migrated to the new path,
    so callers can also reconcile Chroma chunk metadata. Returns None otherwise.
    Raises nothing — failures are already signalled by callers.
    """
    migrated_from: str | None = None
    if prop_index is not None:
        orphan = prop_index.find_orphaned_article_by_hash(
            content_hash=source_hash,
            new_source_path=source,
        )
        if orphan is not None:
            new_scope = config.get_scope_for_path(source)
            new_subdirectory = _derive_subdirectory(source, config)
            moved = await prop_index.move_article_source_path(
                old_source_path=orphan["source_path"],
                new_source_path=source,
                new_filename=file_path.name,
                new_scope=new_scope,
                new_subdirectory=new_subdirectory,
            )
            if moved:
                migrated_from = orphan["source_path"]
                refresh_article_registry_from_row(prop_index.get_article_row(source))
                if event_bus is not None:
                    await event_bus.publish_nowait(
                        rag_article_path_moved(
                            old_path=orphan["source_path"],
                            new_path=source,
                            content_hash=source_hash,
                        )
                    )

    if registry is not None:
        entry = get_article_entry(registry, source)
        if entry and entry.content_hash and source_hash != entry.content_hash:
            logger.warning(
                "Article registry content_hash mismatch for %s: expected %s, got %s",
                source,
                entry.content_hash,
                source_hash,
            )
            if event_bus is not None:
                await event_bus.publish_nowait(
                    rag_article_content_hash_mismatch(
                        file=source,
                        expected_hash=entry.content_hash,
                        actual_hash=source_hash,
                    )
                )
    return migrated_from
