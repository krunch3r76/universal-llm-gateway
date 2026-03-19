"""Resolve existing Cortex entities for injection into extract prompt."""

from __future__ import annotations

import logging
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils.rag_client import DEFAULT_CORTEX_URL, make_async_client

logger = logging.getLogger(__name__)

EMBED_THRESHOLD = 200


async def _fetch_all_entities() -> list[dict[str, Any]]:
    """Query cortex-api for all entities. Returns empty list on failure."""
    try:
        async with make_async_client(DEFAULT_CORTEX_URL, timeout=5.0) as client:
            resp = await client.get("/entities", params={"limit": 500})
            resp.raise_for_status()
            return resp.json().get("items", [])
    except Exception:
        logger.warning(
            "Failed to fetch entities from cortex-api — known_entities will be empty"
        )
        return []


def _format_known_entities(entities: list[dict[str, Any]]) -> str:
    """Format entities as `id — name` lines grouped by type."""
    if not entities:
        return "(none — Cortex unreachable)"
    by_type: dict[str, list[dict[str, Any]]] = {}
    for e in entities:
        etype = e.get("type", "unknown")
        by_type.setdefault(etype, []).append(e)
    lines: list[str] = []
    for etype in sorted(by_type):
        for e in sorted(by_type[etype], key=lambda x: x.get("id", "")):
            eid = e.get("id", "")
            name = e.get("name", "")
            if eid and name:
                lines.append(f"{eid} — {name}")
    return "\n".join(lines)


class ResolveHandler(BaseHandler):
    step_type = "journal_extract_resolve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        entities = await _fetch_all_entities()
        known_entities = _format_known_entities(entities)
        entity_count = len(entities)
        mode = "inject_all" if entity_count <= EMBED_THRESHOLD else "ann_topk"
        if entity_count > EMBED_THRESHOLD:
            logger.warning(
                "Entity count %d exceeds threshold %d — ANN pre-filter not yet "
                "implemented, falling back to inject_all",
                entity_count,
                EMBED_THRESHOLD,
            )
        result = {
            "known_entities": known_entities,
            "entity_count": entity_count,
            "threshold": EMBED_THRESHOLD,
            "mode": mode,
        }
        return StepOutput(raw=known_entities, json=result)
