"""LLM-based knowledge extraction via the rag-extraction pipeline.

Calls the extraction pipeline as a virtual model ID through Stargate.
The pipeline handles prompt, JSON schema, profile, and generation params —
this module is a thin client that sends all file chunks in one call and
parses the map step output.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

from services.rag.config import KnowledgeExtractionConfig

logger = logging.getLogger(__name__)

STARGATE_URL = "http://localhost:9999"

# Pipeline map step has timeout_seconds=120. After that fires, Stargate needs
# a few seconds to cancel pending iterations and serialize the partial response.
# Keeping the httpx timeout close to that ceiling (not 180s) ensures we retry
# promptly rather than sitting idle while cancelled connections drain.
_PIPELINE_TIMEOUT_S = 300.0
_CLIENT_TIMEOUT_S = _PIPELINE_TIMEOUT_S + 15.0  # 315s: pipeline ceiling + drain buffer
_client = httpx.AsyncClient(timeout=_CLIENT_TIMEOUT_S)

_MAX_RETRIES = 3
_BACKOFF_BASE_S = 2.0


@dataclass(slots=True, kw_only=True)
class Facet:
    name: str
    value: str


@dataclass(slots=True, kw_only=True)
class Relation:
    predicate: str
    target: str


@dataclass(slots=True, kw_only=True)
class Entity:
    name: str
    type: list[str]
    facets: list[Facet] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ExtractedKnowledge:
    entities: list[Entity]
    topics: list[str]
    chunk_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "entities": [
                {
                    "name": e.name,
                    "type": e.type,
                    "facets": [{"name": f.name, "value": f.value} for f in e.facets],
                    "relations": [
                        {"predicate": r.predicate, "target": r.target}
                        for r in e.relations
                    ],
                }
                for e in self.entities
            ],
            "topics": self.topics,
        }


def _parse_one(data: dict[str, object]) -> ExtractedKnowledge | None:
    """Parse a single extraction result dict into ExtractedKnowledge."""
    chunk_id = data.get("chunk_id")
    if not isinstance(chunk_id, str):
        logger.warning("Extraction result missing chunk_id field")
        return None
    raw_entities = data.get("entities", [])
    entities: list[Entity] = []
    if isinstance(raw_entities, list):
        for raw_ent in raw_entities:
            if not isinstance(raw_ent, dict):
                continue
            name = raw_ent.get("name")
            etype = raw_ent.get("type")
            if not isinstance(name, str) or not isinstance(etype, list):
                continue
            facets: list[Facet] = []
            for raw_facet in raw_ent.get("facets") or []:
                if isinstance(raw_facet, dict):
                    fname = raw_facet.get("name")
                    fval = raw_facet.get("value")
                    if isinstance(fname, str) and isinstance(fval, str | int | float):
                        facets.append(Facet(name=fname, value=str(fval)))
            relations: list[Relation] = []
            for raw_rel in raw_ent.get("relations") or []:
                if isinstance(raw_rel, dict):
                    predicate = raw_rel.get("predicate")
                    target = raw_rel.get("target")
                    if isinstance(predicate, str) and isinstance(target, str):
                        relations.append(Relation(predicate=predicate, target=target))
            entities.append(
                Entity(
                    name=name,
                    type=[t for t in etype if isinstance(t, str)],
                    facets=facets,
                    relations=relations,
                )
            )
    raw_topics = data.get("topics", [])
    topics = (
        [t for t in raw_topics if isinstance(t, str)]
        if isinstance(raw_topics, list)
        else []
    )
    return ExtractedKnowledge(entities=entities, topics=topics, chunk_id=chunk_id)


def _parse_map_response(
    content: str,
    chunk_ids: list[str],
) -> list[ExtractedKnowledge | None]:
    """Parse pipeline map step output (json_array format) into per-chunk results.

    Returns list indexed to match input chunk_ids order; None for missing/failed chunks.
    """
    try:
        items = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        logger.error("Map response is not valid JSON")
        return [None] * len(chunk_ids)

    if not isinstance(items, list):
        logger.warning(
            "Expected JSON array from map output, got %s", type(items).__name__
        )
        return [None] * len(chunk_ids)

    by_id: dict[str, dict[str, object]] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("chunk_id"), str):
            by_id[item["chunk_id"]] = item

    return [_parse_one(by_id[cid]) if cid in by_id else None for cid in chunk_ids]


async def extract_knowledge_batch(
    chunk_ids: list[str],
    chunk_texts: list[str],
    config: KnowledgeExtractionConfig,
) -> list[ExtractedKnowledge | None]:
    """Extract structured knowledge from all chunks in a file via one pipeline call.

    Returns list indexed to match input order; None for failed/missing chunks.
    """
    if not chunk_ids:
        return []

    chunks = [
        {"id": cid, "text": text}
        for cid, text in zip(chunk_ids, chunk_texts, strict=True)
    ]

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await _client.post(
                f"{STARGATE_URL}/v1/chat/completions",
                json={
                    "model": config.pipeline,
                    "messages": [{"role": "user", "content": "extract"}],
                    "pipeline_options": {"chunks": chunks},
                },
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            return _parse_map_response(content, chunk_ids)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                delay = _BACKOFF_BASE_S ** (attempt + 1)
                logger.warning(
                    "Batch extraction attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    type(exc).__name__,
                    delay,
                    exc_info=True,
                )
                await asyncio.sleep(delay)

    logger.warning(
        "Batch extraction failed after %d attempts: %s",
        _MAX_RETRIES,
        last_exc,
        exc_info=True,
    )
    return [None] * len(chunk_ids)
