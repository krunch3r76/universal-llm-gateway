"""LLM-based knowledge extraction via the rag-extraction pipeline.

Calls the extraction pipeline as a virtual model ID through Stargate.
The pipeline handles prompt, JSON schema, profile, and generation params —
this module is a thin client that parses the structured response.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

from services.rag.config import ExtractionConfig

logger = logging.getLogger(__name__)

STARGATE_URL = "http://localhost:9999"

# Shared async HTTP client for all extraction calls; reused across requests to avoid
# connection-setup overhead. 120s timeout accommodates slow pipeline inference.
_client = httpx.AsyncClient(timeout=120.0)

_MAX_RETRIES = 2
_BACKOFF_BASE_S = 2.0


@dataclass(slots=True, kw_only=True)
class Facet:
    name: str
    value: str


@dataclass(slots=True, kw_only=True)
class Entity:
    name: str
    type: list[str]
    facets: list[Facet] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ExtractedKnowledge:
    entities: list[Entity]
    topics: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "entities": [
                {
                    "name": e.name,
                    "type": e.type,
                    "facets": [{"name": f.name, "value": f.value} for f in e.facets],
                }
                for e in self.entities
            ],
            "topics": self.topics,
        }


def _parse_extraction(data: dict[str, object]) -> ExtractedKnowledge:
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
            entities.append(
                Entity(
                    name=name,
                    type=[t for t in etype if isinstance(t, str)],
                    facets=facets,
                )
            )

    raw_topics = data.get("topics", [])
    topics = (
        [t for t in raw_topics if isinstance(t, str)]
        if isinstance(raw_topics, list)
        else []
    )
    return ExtractedKnowledge(entities=entities, topics=topics)


async def extract_knowledge(
    text: str,
    config: ExtractionConfig,
    chunk_id: str,
) -> ExtractedKnowledge | None:
    """Extract structured knowledge from a text chunk.

    Calls the rag-extraction pipeline via Stargate as a virtual model ID.
    Retries with exponential backoff on transient failures.

    Returns None on failure (chunk proceeds with vector-only indexing).
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await _client.post(
                f"{STARGATE_URL}/v1/chat/completions",
                json={
                    "model": config.pipeline,
                    "messages": [{"role": "user", "content": text}],
                },
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            if not isinstance(parsed, dict):
                logger.warning("Extraction returned non-dict for chunk %s", chunk_id)
                return None
            return _parse_extraction(parsed)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                delay = _BACKOFF_BASE_S ** (attempt + 1)
                logger.warning(
                    "Extraction attempt %d/%d failed for chunk %s (%s); "
                    "retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    chunk_id,
                    type(exc).__name__,
                    delay,
                    exc_info=True,
                )
                await asyncio.sleep(delay)

    logger.warning(
        "Extraction failed for chunk %s after %d attempts: %s",
        chunk_id,
        _MAX_RETRIES,
        last_exc,
        exc_info=True,
    )
    return None
