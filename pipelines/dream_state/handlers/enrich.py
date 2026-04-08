"""Enrich assertion batches with entity metadata and impact cascade data.

For each batch of assertions, groups by entity_id, fetches entity metadata
and C1 impact cascade counts via cortex-api REST, then attaches the context
to each assertion. Outputs pre-formatted JSON strings suitable for the LLM
assessment prompt.
"""

from __future__ import annotations

import json
import logging
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_CORTEX_URL, make_async_client

logger = logging.getLogger(__name__)


class EnrichHandler(BaseHandler):
    step_type = "dream_state_enrich_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        collect_out = context.get_output("collect_assertions")
        batches: list[list[dict[str, Any]]] = []
        if collect_out and collect_out.json:
            batches = collect_out.json.get("batches", [])

        if not batches:
            result: dict[str, Any] = {"enriched_batches": []}
            return StepOutput(raw="[]", json=result)

        all_entity_ids: set[str] = set()
        for batch in batches:
            for a in batch:
                eid = a.get("entity_id", "")
                if eid:
                    all_entity_ids.add(eid)

        entity_cache: dict[str, dict[str, Any]] = {}
        impact_cache: dict[str, int] = {}

        async with make_async_client(DEFAULT_CORTEX_URL, timeout=30.0) as client:
            for eid in all_entity_ids:
                entity_cache[eid] = await self._fetch_entity(client, eid)
                impact_cache[eid] = await self._fetch_impact(client, eid)

        enriched_batches: list[str] = []
        for batch in batches:
            enriched_items: list[dict[str, Any]] = []
            for a in batch:
                eid = a.get("entity_id", "")
                entity_meta = entity_cache.get(eid, {})
                enriched = {
                    **a,
                    "entity_type": entity_meta.get("type", "unknown"),
                    "entity_description": entity_meta.get("description", ""),
                    "impact_cascade_count": impact_cache.get(eid, 0),
                }
                enriched_items.append(enriched)
            enriched_batches.append(json.dumps(enriched_items, indent=2, default=str))

        result = {"enriched_batches": enriched_batches}

        logger.info(
            "Dream state enriched %d batches with %d unique entities",
            len(enriched_batches),
            len(all_entity_ids),
        )
        return StepOutput(raw=json.dumps(result, default=str), json=result)

    async def _fetch_entity(self, client: Any, entity_id: str) -> dict[str, Any]:
        try:
            resp = await client.get(f"/entities/{entity_id}")
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.warning("Failed to fetch entity %s", entity_id)
            return {}

    async def _fetch_impact(self, client: Any, entity_id: str) -> int:
        try:
            resp = await client.get(
                "/edges/impact",
                params={"entity_id": entity_id, "depth": 2},
            )
            if resp.status_code == 404:
                return 0
            resp.raise_for_status()
            return resp.json().get("total_impacted_assertions", 0)
        except Exception:
            logger.warning("Impact analysis failed for %s", entity_id)
            return 0
