"""LLM-based structured knowledge extraction via the rag-extraction pipeline.

At index time, this module calls the ``rag-extraction`` pipeline (via Stargate)
to extract structured knowledge from each document chunk.  The extraction schema
captures:

  Entities:    named concepts with typed categories (component, protocol, config key, …)
               and key-value facets (e.g. port: 9999, role: master).
  Relations:   directed edges scoped to an entity (subject → predicate → target),
               recording how components interact (e.g. Stargate → routes_to → Edge).
  Topics:      free-form thematic labels for the chunk (federation, routing, telemetry).

The pipeline uses a MapExecutor to extract all chunks of a file in one call,
ensuring consistent entity naming across chunks (same model state, same session).
Results are returned as a list of ``ExtractedKnowledge`` objects, one per chunk.

Parsed results flow to two destinations:
  1. ``extraction_wiring.py`` writes property index entries (``prop.name@@``,
     ``prop.type@@``, ``prop.facet@@``, ``prop.rel@@``, ``prop.topic@@``) to the
     SQLite property inverted index for hybrid search at query time.
  2. The ``extraction`` and ``extraction_schema_version`` fields are stored in
     ChromaDB chunk metadata for cross-chunk merging at query time
     (``entity_merging.py``).

Invariant: ∀ file: (∀ chunk extracted in one call) ∨ (∀ chunk unextracted).
Partial writes were implemented twice and reverted twice — see lessons.
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

# Pipeline timeout is 3600s (rag-extraction-v2.yaml timeout_seconds: 3600).
# The HTTP client must exceed that ceiling to avoid cutting the connection
# before the pipeline finishes a large batch. Add 30s drain buffer.
_PIPELINE_TIMEOUT_S = 3600.0
_CLIENT_TIMEOUT_S = _PIPELINE_TIMEOUT_S + 30.0  # 3630s: pipeline ceiling + drain buffer
_client = httpx.AsyncClient(timeout=_CLIENT_TIMEOUT_S)

_MAX_RETRIES = 3
_BACKOFF_BASE_S = 2.0

# Pipeline registration window: pipelines register 5–10s after Stargate starts
# accepting requests. MODEL_NOT_FOUND 404s during this window are transient.
_PIPELINE_REGISTRATION_TIMEOUT_S = 60.0
_PIPELINE_REGISTRATION_POLL_S = 3.0


def _is_pipeline_not_registered(exc: Exception, pipeline_model: str) -> bool:
    """Detect a transient MODEL_NOT_FOUND 404 for the configured pipeline model."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    if exc.response.status_code != 404:
        return False
    try:
        body = exc.response.json()
    except Exception:
        return False
    # OpenAI-compatible error shape: {"error": {"code": "MODEL_NOT_FOUND", "message": ...}}
    error = body.get("error", {})
    if isinstance(error, dict):
        return error.get("code") == "MODEL_NOT_FOUND" and pipeline_model in error.get(
            "message", ""
        )
    return False


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


def _parse_one(
    data: dict[str, object],
    *,
    chunk_id: str,
) -> ExtractedKnowledge | None:
    """Parse a single extraction result dict using caller-owned chunk identity."""
    returned_chunk_id = data.get("chunk_id")
    if isinstance(returned_chunk_id, str) and returned_chunk_id != chunk_id:
        logger.warning(
            "Extraction result chunk_id mismatch: expected %s got %s; using expected id",
            chunk_id,
            returned_chunk_id,
        )

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
    """Parse map output by iteration order instead of model-supplied chunk IDs."""
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

    if len(items) != len(chunk_ids):
        logger.warning(
            "Map response count mismatch: expected %d items, got %d",
            len(chunk_ids),
            len(items),
        )

    parsed: list[ExtractedKnowledge | None] = []
    for idx, chunk_id in enumerate(chunk_ids):
        if idx >= len(items):
            parsed.append(None)
            continue
        item = items[idx]
        if not isinstance(item, dict):
            logger.warning(
                "Map response item %d for chunk %s is %s, expected object",
                idx,
                chunk_id,
                type(item).__name__,
            )
            parsed.append(None)
            continue
        parsed.append(_parse_one(item, chunk_id=chunk_id))
    return parsed


async def _call_extraction(
    chunks: list[dict[str, str]],
    chunk_ids: list[str],
    config: KnowledgeExtractionConfig,
) -> list[ExtractedKnowledge | None]:
    """Single extraction HTTP call. Raises on non-2xx."""
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


async def _await_pipeline_registration(
    chunks: list[dict[str, str]],
    chunk_ids: list[str],
    config: KnowledgeExtractionConfig,
) -> list[ExtractedKnowledge | None]:
    """Poll until the pipeline model registers or the deadline expires.

    Called when the first MODEL_NOT_FOUND 404 is seen — the pipeline has not
    yet registered with Stargate. This is transient at startup (5–10s typical).
    """
    deadline = asyncio.get_event_loop().time() + _PIPELINE_REGISTRATION_TIMEOUT_S
    logger.info(
        "Pipeline '%s' not yet registered; waiting up to %.0fs",
        config.pipeline,
        _PIPELINE_REGISTRATION_TIMEOUT_S,
    )
    while True:
        await asyncio.sleep(_PIPELINE_REGISTRATION_POLL_S)
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            logger.error(
                "Pipeline '%s' did not register within %.0fs",
                config.pipeline,
                _PIPELINE_REGISTRATION_TIMEOUT_S,
            )
            return [None] * len(chunk_ids)
        try:
            result = await _call_extraction(chunks, chunk_ids, config)
            logger.info(
                "Pipeline '%s' registered — extraction succeeded (%.0fs remaining)",
                config.pipeline,
                remaining,
            )
            return result
        except Exception as exc:
            if not _is_pipeline_not_registered(exc, config.pipeline):
                logger.warning(
                    "Non-registration error while waiting for pipeline: %s",
                    exc,
                    exc_info=True,
                )
                return [None] * len(chunk_ids)
            logger.debug(
                "Pipeline '%s' still not registered; %.0fs remaining",
                config.pipeline,
                remaining,
            )


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
            return await _call_extraction(chunks, chunk_ids, config)
        except Exception as exc:
            if _is_pipeline_not_registered(exc, config.pipeline):
                return await _await_pipeline_registration(chunks, chunk_ids, config)
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
