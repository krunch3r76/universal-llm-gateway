"""Extraction queue admin routes — operator reset of exhausted sources."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

if TYPE_CHECKING:
    from collections.abc import Callable

    from universal_event_bus import EventBus

    from services.rag.property_index import PropertyIndex

from services.rag.events.extraction_queue import rag_extraction_source_reset
from services.rag.models.extraction_admin import (
    ExtractionQueueResetResponse,
    ExtractionQueueResetResultItem,
)

logger = logging.getLogger(__name__)

_INVALID_SOURCES_DETAIL = (
    "sources must be a non-empty list of absolute path strings"
)


def register_extraction_queue_routes(
    router: APIRouter,
    *,
    get_property_index_fn: Callable[[], PropertyIndex | None],
    get_event_bus_fn: Callable[[], EventBus | None] | None = None,
    **_kwargs: object,
) -> None:
    """Register extraction queue reset route onto router."""

    @router.post("/extraction/queue/reset", response_model=ExtractionQueueResetResponse)
    async def reset_extraction_queue(request: Request) -> ExtractionQueueResetResponse:
        try:
            raw = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=_INVALID_SOURCES_DETAIL
            ) from exc
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=_INVALID_SOURCES_DETAIL)
        sources_raw = raw.get("sources")
        if not isinstance(sources_raw, list) or len(sources_raw) == 0:
            raise HTTPException(status_code=400, detail=_INVALID_SOURCES_DETAIL)
        if not all(isinstance(item, str) for item in sources_raw):
            raise HTTPException(status_code=400, detail=_INVALID_SOURCES_DETAIL)

        seen: set[str] = set()
        sources: list[str] = []
        for source in sources_raw:
            if source not in seen:
                seen.add(source)
                sources.append(source)

        prop_idx = get_property_index_fn()
        if prop_idx is None:
            raise HTTPException(
                status_code=503, detail="Property index not available"
            )

        rows, reset_count = await prop_idx.reset_extraction_queue_sources(sources)
        eb = get_event_bus_fn() if get_event_bus_fn else None
        if eb:
            for row in rows:
                if row.outcome == "reset" and row.previous_attempts is not None:
                    await eb.publish_nowait(
                        rag_extraction_source_reset(
                            source=row.source,
                            previous_attempts=row.previous_attempts,
                            reason="operator_reset",
                        )
                    )

        return ExtractionQueueResetResponse(
            results=[
                ExtractionQueueResetResultItem(
                    source=row.source,
                    outcome=row.outcome,
                    attempts=row.attempts,
                )
                for row in rows
            ],
            reset_count=reset_count,
        )
