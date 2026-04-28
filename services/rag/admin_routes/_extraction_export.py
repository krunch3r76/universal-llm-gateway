"""Bulk extraction export route: GET /extraction_export."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from collections.abc import Callable

    import chromadb

from services.rag.admin_routes._helpers import _align_list_length
from services.rag.models import ExtractionExportItem, ExtractionExportResponse

logger = logging.getLogger(__name__)


def register_extraction_export_route(
    router: APIRouter,
    *,
    get_collection_fn: Callable[[], chromadb.Collection],
    **_kwargs: object,
) -> None:
    """Register GET /extraction_export onto router."""

    @router.get("/extraction_export", response_model=ExtractionExportResponse)
    def extraction_export(
        prefix: str | None = None, include_text: bool = False
    ) -> ExtractionExportResponse:
        """Bulk export chunk extractions, optionally filtered by source prefix.

        Queries ChromaDB directly so chunks without any extraction (missing field)
        are also included — unlike /sources which is property-index-only.
        Set include_text=true to include the chunk document text in the response.
        """
        collection = get_collection_fn()
        include = ["metadatas"] if not include_text else ["documents", "metadatas"]
        results = collection.get(include=include)
        ids: list[str] = results.get("ids") or []
        n = len(ids)
        docs = _align_list_length(results.get("documents"), n, lambda: "")
        metas = _align_list_length(results.get("metadatas"), n, dict)

        items: list[ExtractionExportItem] = []
        sources_seen: set[str] = set()
        malformed_count = 0
        for chunk_id, text, meta in zip(ids, docs, metas):
            if not isinstance(meta, dict):
                logger.warning(
                    "Skipping chunk_id %s due to malformed metadata in extraction_export",
                    chunk_id,
                )
                malformed_count += 1
                continue
            source = meta.get("source") or ""
            if not source:
                continue
            if prefix and not source.startswith(prefix):
                continue
            sources_seen.add(source)
            ext = meta.get("extraction")
            em = meta.get("extraction_model")
            items.append(
                ExtractionExportItem(
                    source=source,
                    chunk_id=chunk_id,
                    chunk_index=int(meta.get("chunk_index", 0)),
                    text=text or "",
                    extraction=ext if isinstance(ext, str) else None,
                    extraction_model=em if isinstance(em, str) else None,
                    extraction_schema_version=(
                        str(v) if (v := meta.get("extraction_schema_version")) else None
                    ),
                )
            )
        if malformed_count:
            logger.warning(
                "extraction_export omitted %d chunks with malformed metadata",
                malformed_count,
            )
        items.sort(key=lambda x: (x.source, x.chunk_index))
        return ExtractionExportResponse(
            total_chunks=len(items),
            total_sources=len(sources_seen),
            items=items,
        )
