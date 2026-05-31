"""LLM-based structured knowledge extraction via the rag-extraction pipeline.

Provides the HTTP client and parsing for calling the extraction pipeline.
The pipeline uses MapExecutor to process all chunks of a file in one call.

Data flow:
  1. extraction_worker.py picks a source from the extraction queue
  2. This module makes one HTTP call to Stargate (pipeline execution)
  3. Model output is parsed into ExtractedKnowledge objects
  4. Results returned to the worker for metadata patching + property writes

Retry and scheduling policy lives in extraction_worker — this module is a
single-attempt client with no defensive state machinery.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from services.rag.config import KnowledgeExtractionConfig

logger = logging.getLogger(__name__)

STARGATE_URL = "http://localhost:9999"

_PIPELINE_CEILING_S = 3600.0
_POLL_WAIT_S = 60.0
_POLL_INTERVAL_S = 5.0
_client = httpx.AsyncClient(timeout=_PIPELINE_CEILING_S + 30.0)

_batch_overhead_s: float = 30.0

_EXTRACTION_PROBE_TIMEOUT_S = 300.0
_EXTRACTION_PROBE_INTERVAL_S = 5.0


async def wait_until_extraction_ready(
    pipeline: str,
    timeout_s: float = _EXTRACTION_PROBE_TIMEOUT_S,
    interval_s: float = _EXTRACTION_PROBE_INTERVAL_S,
) -> None:
    """Block until the extraction pipeline is registered in Stargate's model list.

    Polls GET /v1/models at interval_s. Raises TimeoutError if not found
    within timeout_s. Called by extraction_worker at startup to avoid burning
    retries against an unregistered pipeline.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = await _client.get(f"{STARGATE_URL}/v1/models", timeout=5.0)
            resp.raise_for_status()
            models = {m["id"] for m in resp.json().get("data", [])}
            if pipeline in models:
                logger.info(
                    "Extraction pipeline '%s' ready after %d probe(s)",
                    pipeline,
                    attempt,
                )
                return
        except Exception as exc:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"Extraction pipeline '{pipeline}' not available after {timeout_s}s"
                ) from exc
            logger.debug(
                "Extraction probe %d: %s; retrying in %.0fs (%.0fs left)",
                attempt,
                exc,
                interval_s,
                remaining,
            )
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError(
                f"Extraction pipeline '{pipeline}' not registered after {timeout_s}s"
            )
        await asyncio.sleep(min(interval_s, remaining))


def configure_timeouts(config: KnowledgeExtractionConfig) -> None:
    """Set batch timeout overhead from config at RAG startup."""
    global _batch_overhead_s
    _batch_overhead_s = config.batch_timeout_overhead_s
    logger.info(
        "Extraction timeouts configured: overhead=%.0fs",
        _batch_overhead_s,
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Facet:
    """Key-value attribute of an Entity (e.g. port: 9999)."""

    name: str
    value: str


@dataclass(slots=True, kw_only=True)
class Relation:
    """Directed edge: current entity → predicate → target."""

    predicate: str
    target: str


@dataclass(slots=True, kw_only=True)
class Entity:
    """Named concept with typed categories, facets, and relations."""

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


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_one(
    data: dict[str, object],
    *,
    chunk_id: str,
) -> ExtractedKnowledge | None:
    """Parse one extraction payload item for a specific chunk identifier.

    Chunk identity comes from caller-owned ordering, not model output.
    Returns None only when the item is structurally invalid.
    """
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
) -> tuple[list[ExtractedKnowledge | None], dict[str, str]]:
    """Parse map output into per-chunk knowledge aligned by input order.

    Model-supplied chunk IDs are not trusted for alignment; caller-provided
    chunk_ids define the result ordering contract.
    """
    failure_reasons: dict[str, str] = {}
    try:
        items = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error(
            "Map response is not valid JSON: %s (line=%d col=%d pos=%d)",
            exc.msg,
            exc.lineno,
            exc.colno,
            exc.pos,
        )
        return [None] * len(chunk_ids), {
            chunk_id: "invalid_json" for chunk_id in chunk_ids
        }

    if not isinstance(items, list):
        logger.warning(
            "Expected JSON array from map output, got %s", type(items).__name__
        )
        return [None] * len(chunk_ids), {
            chunk_id: "non_array_response" for chunk_id in chunk_ids
        }

    if len(items) != len(chunk_ids):
        logger.warning(
            "Map response count mismatch: expected %d items, got %d",
            len(chunk_ids),
            len(items),
        )

    parsed: list[ExtractedKnowledge | None] = []
    for idx, chunk_id in enumerate(chunk_ids):
        if idx >= len(items):
            failure_reasons[chunk_id] = "count_mismatch"
            parsed.append(None)
            continue
        item = items[idx]
        if item is None:
            failure_reasons[chunk_id] = "missing_iteration_output"
            parsed.append(None)
            continue
        if not isinstance(item, dict):
            logger.warning(
                "Map response item %d for chunk %s is %s, expected object",
                idx,
                chunk_id,
                type(item).__name__,
            )
            failure_reasons[chunk_id] = "non_object_item"
            parsed.append(None)
            continue
        parsed.append(_parse_one(item, chunk_id=chunk_id))
    return parsed, failure_reasons


# ---------------------------------------------------------------------------
# HTTP calls — async dispatch + poll (no retries; caller owns retry policy)
# ---------------------------------------------------------------------------


async def submit_extraction_pipeline(
    chunk_ids: list[str],
    chunk_texts: list[str],
    config: KnowledgeExtractionConfig,
) -> str:
    """Submit extraction to the async dispatch endpoint and return execution_id.

    Raises httpx.TimeoutException or httpx.HTTPStatusError on failure.
    """
    chunks = [
        {"id": cid, "text": text}
        for cid, text in zip(chunk_ids, chunk_texts, strict=True)
    ]
    response = await _client.post(
        f"{STARGATE_URL}/api/v1/pipelines/dispatch",
        json={
            "model": config.pipeline,
            "messages": [{"role": "user", "content": "extract"}],
            "pipeline_options": {"chunks": chunks},
            "caller_agent": "rag",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["execution_id"]


async def poll_extraction_result(
    execution_id: str,
    chunk_ids: list[str],
) -> tuple[list[ExtractedKnowledge | None], dict[str, Any]]:
    """Poll until the execution reaches a terminal state and parse the output.

    Raises httpx.TimeoutException or httpx.HTTPStatusError on transport failure.
    Raises RuntimeError if the execution fails on Stargate's side.
    """
    deadline = (
        asyncio.get_running_loop().time() + _PIPELINE_CEILING_S + _batch_overhead_s
    )
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise httpx.TimeoutException(
                f"Extraction execution {execution_id} exceeded ceiling"
            )
        wait = min(_POLL_WAIT_S, remaining)
        response = await _client.get(
            f"{STARGATE_URL}/api/v1/pipelines/executions/{execution_id}",
            params={"wait": wait},
            timeout=wait + 10.0,
        )
        response.raise_for_status()
        record = response.json()
        status = record.get("status")
        if status == "running":
            continue  # server-side wait already provided backoff
        if status == "failed":
            err = record.get("error") or {}
            raise RuntimeError(
                f"Extraction pipeline failed: {err.get('code')} — {err.get('message')}"
            )
        # completed
        content = (record.get("result") or {}).get("content", "")
        parsed, parse_failure_reasons = _parse_map_response(content, chunk_ids)
        timing: dict[str, Any] = {"execution_id": execution_id}
        if parse_failure_reasons:
            timing["parse_failure_reasons"] = parse_failure_reasons
        return parsed, timing


async def cancel_extraction_execution(execution_id: str) -> None:
    """Cancel an in-flight Stargate extraction execution. Best-effort."""
    try:
        response = await _client.delete(
            f"{STARGATE_URL}/api/v1/pipelines/executions/{execution_id}",
            timeout=10.0,
        )
        response.raise_for_status()
        logger.info("Cancelled orphaned extraction execution %s", execution_id)
    except Exception as exc:
        logger.warning(
            "Could not cancel extraction execution %s: %s", execution_id, exc
        )
