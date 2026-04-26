"""Async background worker for decoupled knowledge extraction.

Extraction is decoupled from the indexing hot path: files become searchable
immediately after chunk/embed/upsert. This worker processes the extraction
queue independently — model contention, timeouts, and retries never block
indexing or search.

Architecture:
  indexing._index_file_impl  →  ChromaDB upsert  →  enqueue_extraction(source)
  extraction_worker (this)   →  dequeue  →  extract  →  patch ChromaDB metadata
                                                     →  write property index

The worker runs as a single asyncio task started by lifecycle.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from services.rag.chunk_filters import chunk_metadata_is_noise
from services.rag.events.extraction_admission import rag_extraction_admission_timeout
from services.rag.extraction_admission import ExtractionAdmissionGate
from services.rag.knowledge_extractor import (
    ExtractedKnowledge,
    configure_timeouts,
    extract_file_chunks,
    wait_until_extraction_ready,
)

if TYPE_CHECKING:
    import chromadb
    from universal_event_bus import EventBus

    from services.rag.config import KnowledgeExtractionConfig, RagConfig
    from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 15.0
_IDLE_INTERVAL_S = 60.0
_ERROR_BACKOFF_S = 30.0
_MAX_QUEUE_ATTEMPTS = 5

# Cap on time spent waiting for admission before optimistically proceeding.
# Coordination only; the per-chunk client timeout is the correctness backstop.
_ADMISSION_WAIT_TIMEOUT_S: float = 60.0

# Envelope codes that indicate capacity/routability pressure rather than
# deterministic source defects.
_CAPACITY_CLASS_ENVELOPE_CODES: frozenset[str] = frozenset(
    {
        "REQUEST_TIMEOUT",
        "INFERENCE_TIMEOUT",
        "LOAD_TIMEOUT",
        "NO_FEASIBLE_GATEWAY",
        "MODEL_LOADING",
        "RESOURCE_UNAVAILABLE",
        "GATEWAY_DISCONNECTED",
    }
)


def _is_capacity_class_envelope(exc: httpx.HTTPStatusError) -> bool:
    """Return True iff an HTTP error body carries a capacity-class code."""
    try:
        body = exc.response.json()
    except (ValueError, TypeError):
        return False
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return False
    code = detail.get("code")
    return isinstance(code, str) and code in _CAPACITY_CLASS_ENVELOPE_CODES


def build_property_entries(
    knowledge: ExtractedKnowledge,
    chunk_id: str,
    scope: str = "all",
    source: str = "",
) -> list[tuple[str, str, str, str]]:
    """Build (key, chunk_id, scope, source) quads from extracted knowledge."""
    return (
        [
            (f"prop.name@@{entity.name}", chunk_id, scope, source)
            for entity in knowledge.entities
        ]
        + [
            (f"prop.type@@{etype}", chunk_id, scope, source)
            for entity in knowledge.entities
            for etype in entity.type
        ]
        + [
            (f"prop.facet@@{facet.name}:{facet.value}", chunk_id, scope, source)
            for entity in knowledge.entities
            for facet in entity.facets
        ]
        + [
            (
                f"prop.rel@@{entity.name}>{relation.predicate}>{relation.target}",
                chunk_id,
                scope,
                source,
            )
            for entity in knowledge.entities
            for relation in entity.relations
        ]
        + [
            (f"prop.topic@@{topic}", chunk_id, scope, source)
            for topic in knowledge.topics
        ]
    )


async def _extract_source(
    source: str,
    *,
    collection: chromadb.Collection,
    config: KnowledgeExtractionConfig,
    rag_config: RagConfig,
    property_index: PropertyIndex,
    event_bus: EventBus | None,
) -> tuple[bool, bool]:
    """Extract one source.

    Returns ``(all_done, increment_attempt)`` where ``increment_attempt`` is
    only meaningful when ``all_done`` is False.
    """
    existing = collection.get(
        where={"source": source}, include=["documents", "metadatas"]
    )
    raw_ids: list[str] = existing.get("ids") or []
    raw_docs: list[str] = existing.get("documents") or []
    raw_metas: list[Any] = existing.get("metadatas") or []

    if not raw_ids:
        logger.info("Extraction: source removed from index, skipping: %s", source)
        return True, True

    if len(raw_ids) != len(raw_docs) or len(raw_ids) != len(raw_metas):
        logger.warning(
            "Extraction: inconsistent ChromaDB payload for %s (ids=%d docs=%d metas=%d)",
            source,
            len(raw_ids),
            len(raw_docs),
            len(raw_metas),
        )
        return False, True

    need_idx = [
        i
        for i, m in enumerate(raw_metas)
        if isinstance(m, dict)
        and not chunk_metadata_is_noise(m)
        and "extraction_schema_version" not in m
        and not m.get("extraction_skipped")
    ]

    if not need_idx:
        return True, True

    ext_ids = [raw_ids[i] for i in need_idx]
    ext_texts = [raw_docs[i] for i in need_idx]
    ext_metas = [raw_metas[i] for i in need_idx]

    start = time.monotonic()
    parsed, timing = await extract_file_chunks(ext_ids, ext_texts, config)
    duration = time.monotonic() - start

    staged: dict[str, ExtractedKnowledge] = {}
    for knowledge in parsed:
        if knowledge is None:
            continue
        if knowledge.chunk_id in {raw_ids[i] for i in need_idx}:
            staged[knowledge.chunk_id] = knowledge

    if staged:
        scope = rag_config.get_scope_for_path(source)
        update_ids: list[str] = []
        update_metas: list[dict[str, Any]] = []
        all_prop_entries: list[tuple[str, str, str, str]] = []

        for chunk_id, knowledge in staged.items():
            idx_in_ext = ext_ids.index(chunk_id)
            meta = ext_metas[idx_in_ext]
            meta["extraction"] = json.dumps(knowledge.to_dict())
            meta["extraction_schema_version"] = config.schema_version
            meta["extraction_model"] = config.extraction_model
            update_ids.append(chunk_id)
            update_metas.append(meta)
            all_prop_entries.extend(
                build_property_entries(knowledge, chunk_id, scope, source)
            )

        collection.update(ids=update_ids, metadatas=update_metas)
        if all_prop_entries:
            await property_index.add_batch_with_scope(all_prop_entries)

    failed_ids = [cid for cid in ext_ids if cid not in staged]
    succeeded = len(staged)
    logger.info(
        "Extraction: %s — %d/%d chunks succeeded (%.1fs)",
        source,
        succeeded,
        len(ext_ids),
        duration,
    )

    all_done = not failed_ids
    increment_attempt = succeeded > 0
    return all_done, increment_attempt


async def run_extraction_worker(
    *,
    config: RagConfig,
    collection_fn: Any,
    property_index: PropertyIndex,
    event_bus: EventBus | None,
    shutdown_event: asyncio.Event,
    admission_gate: ExtractionAdmissionGate | None = None,
) -> None:
    """Main extraction worker loop. Runs until shutdown_event is set.

    Waits for the extraction pipeline to register, then processes the
    extraction queue. When ``admission_gate`` is supplied, blocks at most
    ``_ADMISSION_WAIT_TIMEOUT_S`` before each dequeue; the gate is
    advisory and the loop proceeds on timeout (Phase 1's classification
    prevents budget bleed if the gate was closed for a real reason).
    """
    ke = config.knowledge_extraction
    configure_timeouts(ke)

    if not ke.pipeline:
        logger.info("Extraction worker: no pipeline configured, exiting")
        return

    try:
        await wait_until_extraction_ready(ke.pipeline)
    except TimeoutError:
        logger.error(
            "Extraction worker: pipeline '%s' not available, will retry in loop",
            ke.pipeline,
        )

    logger.info(
        "Extraction worker started (pipeline=%s, admission_gate=%s)",
        ke.pipeline,
        "enabled" if admission_gate is not None else "disabled",
    )

    while not shutdown_event.is_set():
        if admission_gate is not None and admission_gate.is_closed():
            logger.info(
                "Extraction worker: admission CLOSED (reasons=%s); waiting up to %.0fs",
                admission_gate.active_reasons(),
                _ADMISSION_WAIT_TIMEOUT_S,
            )
            wait_start = time.monotonic()
            opened = await admission_gate.wait_for_admission(_ADMISSION_WAIT_TIMEOUT_S)
            waited = time.monotonic() - wait_start
            if not opened:
                if event_bus is not None:
                    await event_bus.publish_nowait(
                        rag_extraction_admission_timeout(
                            pipeline_id=ke.pipeline,
                            waited_seconds=waited,
                            active_reasons=admission_gate.active_reasons(),
                        )
                    )
                if shutdown_event.is_set():
                    break

        try:
            sources = await property_index.dequeue_extraction(
                limit=1, max_attempts=_MAX_QUEUE_ATTEMPTS
            )
        except Exception:
            logger.error("Extraction worker: queue read failed", exc_info=True)
            await _sleep_or_shutdown(shutdown_event, _ERROR_BACKOFF_S)
            continue

        if not sources:
            await _sleep_or_shutdown(shutdown_event, _IDLE_INTERVAL_S)
            continue

        source = sources[0]
        try:
            collection = collection_fn()
            all_done, increment_attempt = await _extract_source(
                source,
                collection=collection,
                config=ke,
                rag_config=config,
                property_index=property_index,
                event_bus=event_bus,
            )
            if all_done:
                await property_index.complete_extraction(source)
            else:
                await property_index.fail_extraction(
                    source,
                    increment_attempt=increment_attempt,
                )
                await _sleep_or_shutdown(shutdown_event, _POLL_INTERVAL_S)

        except httpx.TimeoutException:
            logger.warning(
                "Extraction worker: timeout for %s, will retry later "
                "(capacity-class; budget held)",
                source,
            )
            await property_index.fail_extraction(source, increment_attempt=False)
            await _sleep_or_shutdown(shutdown_event, _ERROR_BACKOFF_S)

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (503, 429):
                logger.info(
                    "Extraction worker: %d for %s — model busy, backing off",
                    status,
                    source,
                )
                await _sleep_or_shutdown(shutdown_event, _ERROR_BACKOFF_S)
            else:
                capacity_class = _is_capacity_class_envelope(exc)
                logger.warning(
                    "Extraction worker: HTTP %d for %s (capacity_class=%s)",
                    status,
                    source,
                    capacity_class,
                    exc_info=True,
                )
                await property_index.fail_extraction(
                    source,
                    increment_attempt=not capacity_class,
                )
                await _sleep_or_shutdown(shutdown_event, _POLL_INTERVAL_S)

        except Exception:
            logger.error(
                "Extraction worker: unexpected error for %s "
                "(treated as capacity-class; budget held)",
                source,
                exc_info=True,
            )
            await property_index.fail_extraction(source, increment_attempt=False)
            await _sleep_or_shutdown(shutdown_event, _ERROR_BACKOFF_S)

    logger.info("Extraction worker shutting down")


async def _sleep_or_shutdown(event: asyncio.Event, seconds: float) -> None:
    """Sleep for up to *seconds*, returning early if shutdown is signaled."""
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except TimeoutError:
        pass
