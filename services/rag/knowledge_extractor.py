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

Per-batch HTTP timeouts scale with chunk count to fail fast when the backend
model is saturated by concurrent index workers.  Concurrency is bounded at the
queue/worker level (``index_workers`` in config), not here — this project uses
queues for concurrency control, not semaphores or locks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from services.rag.extraction_model_tracker import ExtractionModelTracker

if TYPE_CHECKING:
    from services.rag.config import KnowledgeExtractionConfig

logger = logging.getLogger(__name__)

STARGATE_URL = "http://localhost:9999"

# Pipeline YAML ceiling (rag-extraction-v2.yaml timeout_seconds: 3600).
# The default client timeout remains a safety net; per-batch timeouts override it.
_PIPELINE_CEILING_S = 3600.0
_client = httpx.AsyncClient(timeout=_PIPELINE_CEILING_S + 30.0)

_MAX_RETRIES = 3
_BACKOFF_BASE_S = 2.0
_batch_overhead_s: float = 30.0

# Pipeline registration window: pipelines register 5–10s after Stargate starts
# accepting requests. MODEL_NOT_FOUND 404s during this window are transient.
_PIPELINE_REGISTRATION_TIMEOUT_S = 60.0
_PIPELINE_REGISTRATION_POLL_S = 3.0

_tracker: ExtractionModelTracker | None = None


class BatchTimeoutError(Exception):
    """Raised when a batch exceeds the configured dynamic timeout budget.

    The timeout budget is attached so callers can emit observability events
    with the actual limit that was exceeded for this specific batch.
    """

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(f"Extraction batch timed out after {timeout_seconds:.0f}s")
        self.timeout_seconds = timeout_seconds


_EXTRACTION_PROBE_TIMEOUT_S = 300.0
_EXTRACTION_PROBE_INTERVAL_S = 5.0


async def wait_until_extraction_ready(
    pipeline: str,
    timeout_s: float = _EXTRACTION_PROBE_TIMEOUT_S,
    interval_s: float = _EXTRACTION_PROBE_INTERVAL_S,
) -> None:
    """Block until the extraction pipeline is registered in Stargate's model list.

    Polls GET /v1/models at interval_s. Raises TimeoutError if not found
    within timeout_s.  Gates watcher start on extraction readiness so
    indexing doesn't burn retry budgets against an unreachable model.
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
    """Set batch timeout overhead from config at RAG startup.

    Per-iteration inference timeout is enforced server-side by Stargate via
    X-Request-Timeout (handler_timeout_seconds in pipeline YAML). The RAG
    client timeout is pipeline ceiling + overhead only — no per-chunk scaling.
    """
    global _batch_overhead_s
    _batch_overhead_s = config.batch_timeout_overhead_s
    logger.info(
        "Extraction timeouts configured: overhead=%.0fs"
        " (per-chunk enforcement via Stargate X-Request-Timeout)",
        _batch_overhead_s,
    )


def configure_tracker(
    config: KnowledgeExtractionConfig,
) -> ExtractionModelTracker:
    """Create the extraction model tracker from config at RAG startup.

    Returns the tracker so the caller can start() it as a background task.
    The tracker subscribes to Event Service model.loaded / model.unloaded
    events and gates extraction workers on model availability.
    """
    global _tracker
    _tracker = ExtractionModelTracker(
        extraction_model=config.extraction_model,
        pipeline_id=config.pipeline,
        wait_timeout_s=config.model_load_wait_s,
    )
    logger.info(
        "Extraction model tracker configured for '%s' (pipeline='%s', wait=%.0fs)",
        config.extraction_model,
        config.pipeline,
        config.model_load_wait_s,
    )
    return _tracker


def get_tracker() -> ExtractionModelTracker | None:
    """Expose tracker for external observability."""
    return _tracker


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
class StargateError:
    code: str
    retryable: bool
    message: str


def _parse_stargate_error(exc: httpx.HTTPStatusError) -> StargateError | None:
    """Parse Stargate's structured error envelope from a 4xx/5xx response."""
    try:
        body = exc.response.json()
    except Exception:
        return None

    error_block = body.get("error", {})
    if not isinstance(error_block, dict):
        error_block = {}

    code = body.get("code", "")
    if not isinstance(code, str) or not code:
        code = error_block.get("code", "")
    if not isinstance(code, str) or not code:
        return None

    retryable = body.get("retryable", False)
    if not isinstance(retryable, bool):
        retryable = False
    if not retryable:
        nested_retryable = error_block.get("retryable", False)
        if isinstance(nested_retryable, bool):
            retryable = nested_retryable

    message = body.get("message", "")
    if not isinstance(message, str) or not message:
        nested_message = error_block.get("message", "")
        message = nested_message if isinstance(nested_message, str) else ""

    return StargateError(code=code, retryable=retryable, message=message)


@dataclass(slots=True, kw_only=True)
class Facet:
    """Represents a key-value attribute or property of an Entity, providing additional detail. For example, a 'port' facet with value '9999' for a 'Service' entity."""

    name: str
    value: str


@dataclass(slots=True, kw_only=True)
class Relation:
    """Represents a directed relationship or interaction between two entities, where the current entity is the subject, and 'target' is the object of the 'predicate'."""

    predicate: str
    target: str


@dataclass(slots=True, kw_only=True)
class Entity:
    """Represents a named concept extracted from a document chunk, characterized by its type(s), descriptive facets, and relationships to other entities."""

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
    """Parse one extraction payload item for a specific chunk identifier.

    The chunk identity comes from caller-owned ordering, not model output.
    Returns None only when the caller should treat the item as invalid.
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
) -> list[ExtractedKnowledge | None]:
    """Parse map output into per-chunk knowledge aligned by input order.

    Model-supplied chunk IDs are not trusted for alignment; caller-provided
    chunk_ids define the result ordering contract.
    """
    try:
        items = json.loads(content)
    except json.JSONDecodeError:
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
) -> tuple[list[ExtractedKnowledge | None], dict[str, object]]:
    """Execute one extraction HTTP call.

    Per-iteration inference timeout is enforced server-side by Stargate via
    X-Request-Timeout (handler_timeout_seconds in pipeline YAML). This client
    timeout is a pipeline-ceiling safety net only — not scaled per chunk so
    queue-wait time does not consume the inference budget.
    Raises on non-2xx HTTP responses.
    """
    batch_timeout = _PIPELINE_CEILING_S + _batch_overhead_s
    response = await _client.post(
        f"{STARGATE_URL}/v1/chat/completions",
        json={
            "model": config.pipeline,
            "messages": [{"role": "user", "content": "extract"}],
            "pipeline_options": {"chunks": chunks},
        },
        timeout=batch_timeout,
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    parsed = _parse_map_response(content, chunk_ids)
    timing = body.get("pipeline_timing") or {}
    return parsed, timing


async def _await_pipeline_registration(
    chunks: list[dict[str, str]],
    chunk_ids: list[str],
    config: KnowledgeExtractionConfig,
) -> tuple[list[ExtractedKnowledge | None], dict[str, object]]:
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
            return [None] * len(chunk_ids), {}
        try:
            result = await _call_extraction(chunks, chunk_ids, config)
            logger.info(
                "Pipeline '%s' registered — extraction succeeded (%.0fs remaining)",
                config.pipeline,
                remaining,
            )
            return result
        except httpx.HTTPStatusError as exc:
            if not _is_pipeline_not_registered(exc, config.pipeline):
                logger.warning(
                    "Unexpected HTTP error while waiting for pipeline: %s",
                    exc,
                    exc_info=True,
                )
                return [None] * len(chunk_ids), {}
            logger.debug(
                "Pipeline '%s' still not registered; %.0fs remaining",
                config.pipeline,
                remaining,
            )
        except httpx.RequestError as exc:
            logger.error(
                "Network error during pipeline registration wait: %s",
                exc,
                exc_info=True,
            )
            return [None] * len(chunk_ids), {}
        except Exception as exc:
            logger.error(
                "Unexpected non-HTTP error during pipeline registration wait: %s",
                exc,
                exc_info=True,
            )
            # Consider re-raising if this is truly unexpected and should halt registration
            # For now, returning None to indicate failure to register.
            return [None] * len(chunk_ids), {}


async def extract_knowledge_batch(
    chunk_ids: list[str],
    chunk_texts: list[str],
    config: KnowledgeExtractionConfig,
) -> tuple[list[ExtractedKnowledge | None], dict[str, object]]:
    """Extract structured knowledge from all chunks in a file via one pipeline call.

    HTTP timeout scales with chunk count so small batches fail fast under model
    saturation.  Timeout exceptions are translated to BatchTimeoutError so callers
    can emit a dedicated event and preserve per-file all-or-nothing behavior.
    """
    if not chunk_ids:
        return [], {}

    if _tracker is not None and not await _tracker.wait_for_model():
        logger.info(
            "Extraction model not available — skipping batch (%d chunks)",
            len(chunk_ids),
        )
        return [None] * len(chunk_ids), {"model_unavailable": True}

    chunks = [
        {"id": cid, "text": text}
        for cid, text in zip(chunk_ids, chunk_texts, strict=True)
    ]
    batch_timeout = _PIPELINE_CEILING_S + _batch_overhead_s

    last_exc: Exception | None = None
    capacity_retries = 0

    async def _retry_with_backoff(exc: Exception, attempt: int) -> None:
        nonlocal last_exc
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

    for attempt in range(_MAX_RETRIES):
        try:
            parsed, timing = await _call_extraction(chunks, chunk_ids, config)
            if capacity_retries > 0:
                timing = {**timing, "capacity_retries": capacity_retries}
            return parsed, timing
        except httpx.TimeoutException:
            logger.warning(
                "Batch extraction timed out (%d chunks, budget %.0fs); not retrying",
                len(chunk_ids),
                batch_timeout,
            )
            raise BatchTimeoutError(batch_timeout)
        except httpx.HTTPStatusError as exc:
            if _is_pipeline_not_registered(exc, config.pipeline):
                return await _await_pipeline_registration(chunks, chunk_ids, config)
            parsed = _parse_stargate_error(exc)
            if parsed is not None:
                if not parsed.retryable:
                    logger.warning(
                        "Non-retryable Stargate error for extraction: code=%s message=%s",
                        parsed.code,
                        parsed.message,
                    )
                    return [None] * len(chunk_ids), {"stargate_error": parsed.code}

                capacity_retries += 1
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = min(
                        60.0, _BACKOFF_BASE_S ** (attempt + 1)
                    ) + random.uniform(0.0, 3.0)
                    logger.info(
                        "Stargate retryable extraction error (attempt %d/%d, code=%s); "
                        "backing off %.1fs",
                        attempt + 1,
                        _MAX_RETRIES,
                        parsed.code,
                        delay,
                    )
                    await asyncio.sleep(delay)
                continue
            await _retry_with_backoff(exc, attempt)
        except httpx.RequestError as exc:
            await _retry_with_backoff(exc, attempt)
        except Exception as exc:
            await _retry_with_backoff(exc, attempt)

    if last_exc is not None:
        logger.warning(
            "Batch extraction failed after %d attempts: %s",
            _MAX_RETRIES,
            last_exc,
            exc_info=True,
        )
    else:
        # This case should ideally not be reached if all attempts failed without an exception.
        # If it is, it indicates a logic error where an exception was not captured.
        logger.error(
            "Batch extraction finished without success and without captured exception. This indicates an unhandled error path."
        )
    timing: dict[str, Any] = {}  # Or a more specific TypedDict if structure is fixed
    return [None] * len(chunk_ids), timing
