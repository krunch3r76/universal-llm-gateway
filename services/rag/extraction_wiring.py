"""Wire knowledge extraction into the indexing pipeline.

Extracted during the rag_service module split to keep files under SLOC limits.
Called between chunk_file() and embed_chunks() when extraction is enabled.
One pipeline call per file — MapExecutor fans out over all chunks in parallel.

Partial-write threshold: when ≥90% of chunks succeed, extraction metadata
is written for the successful chunks; the remainder retry on the next sweep.
This matches the pipeline MapExecutor's failure_threshold=0.1 convention.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from universal_event_bus import Event, EventBus

if TYPE_CHECKING:
    import chromadb

from services.rag.chunk_filters import chunk_metadata_is_noise
from services.rag.chunkers import Chunk
from services.rag.config import KnowledgeExtractionConfig
from services.rag.events.extraction import (
    rag_extraction_batch_completed,
    rag_extraction_batch_skipped,
    rag_extraction_batch_started,
    rag_extraction_batch_timed_out,
    rag_extraction_completed,
    rag_extraction_failed,
    rag_extraction_permanently_skipped,
    rag_extraction_recovery_completed,
    rag_extraction_recovery_failed,
    rag_extraction_recovery_skipped,
)
from services.rag.knowledge_extractor import (
    BatchTimeoutError,
    ExtractedKnowledge,
    extract_knowledge_batch,
)
from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)

# Accept partial writes when failure ratio is within 10%.
_FAILURE_THRESHOLD = 0.1


def _chunk_needs_extraction_for_recovery(
    m: dict[str, Any], *, extraction_model: str | None
) -> bool:
    """True when this chunk should receive extraction metadata (not noise, missing/stale)."""
    if chunk_metadata_is_noise(m):
        return False
    if "extraction_schema_version" not in m:
        return True
    return bool(extraction_model and m.get("extraction_model") != extraction_model)


@dataclass(slots=True, kw_only=True)
class ExtractionResult:
    """Result of a knowledge extraction run for one file (batch of chunks).

    entities/topics are counts; property_entries are (key, chunk_id, scope, source)
    quads for the property index; success is True only when extraction metadata
    was written (partial or full write) and false for all-or-nothing rollback.
    batch_start_ts: ISO-8601 timestamp when extraction batch started (for per-file duration).
    processing_seconds: Optional Stargate-derived work time (post-queue).
    queue_wait_seconds: Optional time from step start to first inference started.
    """

    entities: int = 0
    topics: int = 0
    property_entries: list[tuple[str, str, str, str]] = field(default_factory=list)
    success: bool = field(default=False)
    batch_start_ts: str | None = None
    processing_seconds: float | None = None
    queue_wait_seconds: float | None = None


def build_property_entries(
    knowledge: ExtractedKnowledge, chunk_id: str, scope: str = "all", source: str = ""
) -> list[tuple[str, str, str, str]]:
    """Build (key, chunk_id, scope, source) quads from extracted knowledge.

    Keys use prefixes: prop.name@@{entity.name} (entity names),
    prop.type@@{etype} (entity types),
    prop.facet@@{facet.name}:{facet.value} (facets),
    prop.rel@@{entity.name}>{relation.predicate}>{relation.target} (relations),
    prop.topic@@{topic} (topics).
    """
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


def _publish_event_nonblocking(event_bus: EventBus, event: Event) -> None:
    """Publish event in background and surface task failures in logs."""
    task: asyncio.Task[None] = asyncio.create_task(
        event_bus.publish_async_nowait(event)
    )

    def _on_done(done_task: asyncio.Task[None]) -> None:
        if done_task.cancelled():
            return
        exc = done_task.exception()
        if exc is not None:
            logger.warning(
                "Non-blocking event publish failed for '%s': %s",
                event.signal,
                exc,
                exc_info=True,
            )

    task.add_done_callback(_on_done)


def _describe_infrastructure_failure(timing: dict[str, object]) -> str:
    """Build a bounded error string from infrastructure failure timing metadata.

    Produces descriptive but short error messages for model availability,
    Stargate errors, and capacity exhaustion so operators can distinguish
    infrastructure failures from extraction-quality failures in failed_extractions.
    """
    if "model_unavailable" in timing:
        return "infrastructure: extraction model not loaded"
    if "stargate_error" in timing:
        msg = str(timing["stargate_error"])[:120]
        return f"infrastructure: stargate error — {msg}"
    if "capacity_retries" in timing:
        return "infrastructure: capacity retries exhausted"
    return "infrastructure: unknown"


async def run_extraction(
    *,
    file: str,
    ids: list[str],
    chunks: list[Chunk],
    metadatas: list[dict[str, Any]],
    config: KnowledgeExtractionConfig,
    property_index: PropertyIndex,
    event_bus: EventBus | None,
    apply_property_index: bool = True,
    scope: str = "all",
) -> ExtractionResult:
    """Run knowledge extraction on all chunks and populate the property index.

    Makes one pipeline call per file; MapExecutor fans out over chunks in parallel.
    Modifies metadatas in-place: adds 'extraction' and 'extraction_schema_version'.

    Write policy: when failure_ratio ≤ _FAILURE_THRESHOLD (10%), successful
    chunks are written and failures retry on the next sweep. Above threshold,
    nothing is written (full retry). This aligns with the MapExecutor's own
    failure_threshold=0.1 convention.
    ∀ file: (successful/total ≥ 0.9 ⟹ write successful) ∨ (write nothing)
    """
    result = ExtractionResult()
    id_to_idx = {chunk_id: i for i, chunk_id in enumerate(ids)}

    # --- Permanent failure gate ---
    max_attempts = config.max_extraction_attempts
    permanent = property_index.get_permanent_failures(file, max_attempts)
    if permanent:
        active_ids = [cid for cid in ids if cid not in permanent]
        skipped_count = len(ids) - len(active_ids)
        if not active_ids:
            logger.warning(
                "Extraction permanently skipped for %s: all %d chunks exceeded"
                " max_attempts=%d",
                file,
                skipped_count,
                max_attempts,
            )
            if event_bus is not None:
                _publish_event_nonblocking(
                    event_bus,
                    rag_extraction_batch_skipped(
                        file=file,
                        chunk_count=len(ids),
                        skipped_count=skipped_count,
                        max_attempts=max_attempts,
                    ),
                )
            return result
        logger.warning(
            "Extraction for %s: skipping %d permanently-failed chunks"
            " (attempt_count >= %d); running %d remaining",
            file,
            skipped_count,
            max_attempts,
            len(active_ids),
        )
        active_chunks = [chunks[id_to_idx[cid]] for cid in active_ids]
        ids = active_ids
        chunks = active_chunks
        # Keep id_to_idx mapping to original metadatas indices; do not rebuild
    # --- end permanent failure gate ---

    if event_bus is not None:
        result.batch_start_ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _publish_event_nonblocking(
            event_bus,
            rag_extraction_batch_started(file=file, chunk_count=len(ids)),
        )

    start = time.monotonic()
    try:
        extract_return = await extract_knowledge_batch(
            chunk_ids=ids,
            chunk_texts=[c.text for c in chunks],
            config=config,
        )
    except BatchTimeoutError as exc:
        duration = time.monotonic() - start
        timeout_error = f"batch extraction timeout ({exc.timeout_seconds:.0f}s)"
        logger.warning(
            "Extraction batch timed out for %s (%d chunks, %.0fs budget)",
            file,
            len(ids),
            exc.timeout_seconds,
        )
        for chunk_id in ids:
            await property_index.record_failure(
                chunk_id=chunk_id,
                source=file,
                error=timeout_error,
                permanent=False,
                increment_attempt=False,
            )
            if event_bus is not None:
                _publish_event_nonblocking(
                    event_bus,
                    rag_extraction_failed(
                        chunk_id=chunk_id,
                        error=timeout_error,
                    ),
                )
        if event_bus is not None:
            _publish_event_nonblocking(
                event_bus,
                rag_extraction_batch_timed_out(
                    file=file,
                    chunk_count=len(ids),
                    timeout_seconds=exc.timeout_seconds,
                    duration_seconds=duration,
                ),
            )
        return result
    duration = time.monotonic() - start
    if not isinstance(extract_return, tuple) or len(extract_return) != 2:
        logger.error(
            "extract_knowledge_batch returned unexpected type %s for file %s",
            type(extract_return).__name__,
            file,
        )
        for chunk_id in ids:
            await property_index.record_failure(
                chunk_id=chunk_id,
                source=file,
                error="invalid extraction response shape",
                permanent=False,
            )
            if event_bus is not None:
                _publish_event_nonblocking(
                    event_bus,
                    rag_extraction_failed(
                        chunk_id=chunk_id,
                        error="invalid extraction response shape",
                    ),
                )
        return result
    knowledge_list, timing = extract_return
    if not isinstance(knowledge_list, list):
        logger.error(
            "extract_knowledge_batch returned invalid list type %s for file %s",
            type(knowledge_list).__name__,
            file,
        )
        for chunk_id in ids:
            await property_index.record_failure(
                chunk_id=chunk_id,
                source=file,
                error="invalid extraction list payload",
                permanent=False,
            )
            if event_bus is not None:
                _publish_event_nonblocking(
                    event_bus,
                    rag_extraction_failed(
                        chunk_id=chunk_id,
                        error="invalid extraction list payload",
                    ),
                )
        return result
    if isinstance(timing, dict):
        processing_seconds = timing.get("processing_seconds")
        if isinstance(processing_seconds, int | float):
            result.processing_seconds = float(processing_seconds)
        queue_wait_seconds = timing.get("queue_wait_seconds")
        if isinstance(queue_wait_seconds, int | float):
            result.queue_wait_seconds = float(queue_wait_seconds)

    staged: dict[str, ExtractedKnowledge] = {}
    for knowledge in knowledge_list:
        if knowledge is None:
            continue
        chunk_id = knowledge.chunk_id
        if id_to_idx.get(chunk_id) is None:
            logger.warning("Extraction result has unknown chunk_id %s", chunk_id)
            continue
        staged[chunk_id] = knowledge

    failed_ids = [cid for cid in ids if cid not in staged]
    successful = len(staged)
    is_infrastructure_failure = isinstance(timing, dict) and (
        "stargate_error" in timing
        or "model_unavailable" in timing
        or ("capacity_retries" in timing and successful == 0)
    )
    # Threshold applied to the active (non-permanently-failed) chunk set.
    failure_ratio = len(failed_ids) / len(ids) if ids else 0.0
    accept_partial = bool(failed_ids) and failure_ratio <= _FAILURE_THRESHOLD

    failure_counts_snapshot = property_index.get_failure_counts(file)

    if failed_ids:
        if is_infrastructure_failure:
            logger.info(
                "Extraction deferred for %s due to Stargate infrastructure state"
                " — will retry on next sweep (%d chunks)",
                file,
                len(failed_ids),
            )
            infra_error = _describe_infrastructure_failure(timing)
        elif accept_partial:
            logger.info(
                "Partial extraction for %s: %d/%d chunks failed (within threshold)"
                " — writing %d successful",
                file,
                len(failed_ids),
                len(ids),
                successful,
            )
        else:
            logger.warning(
                "Extraction skipped for %s: %d/%d chunks failed"
                " — will retry on next sweep",
                file,
                len(failed_ids),
                len(ids),
            )
        for chunk_id in failed_ids:
            error_msg = (
                infra_error
                if is_infrastructure_failure
                else "missing or invalid result after batch parsing"
            )
            new_attempt_count = failure_counts_snapshot.get(chunk_id, 0) + (
                0 if is_infrastructure_failure else 1
            )
            is_permanent = (
                not is_infrastructure_failure
                and new_attempt_count >= config.max_extraction_attempts
            )
            await property_index.record_failure(
                chunk_id=chunk_id,
                source=file,
                error=error_msg,
                permanent=is_permanent,
                increment_attempt=not is_infrastructure_failure,
            )
            if event_bus is not None:
                _publish_event_nonblocking(
                    event_bus,
                    rag_extraction_failed(
                        chunk_id=chunk_id,
                        error=error_msg,
                    ),
                )
                if is_permanent:
                    _publish_event_nonblocking(
                        event_bus,
                        rag_extraction_permanently_skipped(
                            chunk_id=chunk_id,
                            source=file,
                            attempt_count=new_attempt_count,
                        ),
                    )

    should_write = not failed_ids or accept_partial
    if should_write and staged:
        all_property_entries: list[tuple[str, str, str, str]] = []
        for chunk_id, knowledge in staged.items():
            idx = id_to_idx[chunk_id]
            result.entities += len(knowledge.entities)
            result.topics += len(knowledge.topics)
            prop_entries = build_property_entries(knowledge, chunk_id, scope, file)
            all_property_entries.extend(prop_entries)
            metadatas[idx]["extraction"] = json.dumps(knowledge.to_dict())
            metadatas[idx]["extraction_schema_version"] = config.schema_version
            metadatas[idx]["extraction_model"] = config.extraction_model
            if event_bus is not None:
                _publish_event_nonblocking(
                    event_bus,
                    rag_extraction_completed(
                        chunk_id=chunk_id,
                        entities=len(knowledge.entities),
                        topics=len(knowledge.topics),
                    ),
                )
        if apply_property_index and all_property_entries:
            await property_index.add_batch_with_scope(all_property_entries)
        result.property_entries = all_property_entries
        written = len(staged)
        result.success = True
        # Clear failure records: full write clears all; partial write clears only
        # successful chunk IDs so failed chunks keep their attempt count for retry.
        if accept_partial:
            await property_index.clear_failures_for_ids(file, list(staged.keys()))
        else:
            await property_index.clear_failures_for(file)
    else:
        written = 0

    if event_bus is not None:
        _publish_event_nonblocking(
            event_bus,
            rag_extraction_batch_completed(
                file=file,
                chunk_count=len(ids),
                successful=successful,
                written=written,
                duration_seconds=duration,
                extraction_model=config.extraction_model,
            ),
        )

    return result


async def recover_missing_extraction(
    *,
    collection: chromadb.Collection,
    source: str,
    existing_ids: list[str],
    existing_metadatas: list[dict[str, Any]],
    config: KnowledgeExtractionConfig,
    property_index: PropertyIndex,
    event_bus: EventBus | None,
    scope: str = "all",
) -> ExtractionResult | None:
    """Re-run extraction for chunks that are indexed but missing extraction metadata.

    Called when all_ids_match_prefix is True but extraction_schema_version is absent
    from one or more chunks — the file was indexed successfully but extraction timed out.

    Fetches documents from ChromaDB, re-runs extraction (partial writes accepted
    per _FAILURE_THRESHOLD), and patches chunk metadata via collection.update().
    Property index is also populated for successful chunks.

    Returns ExtractionResult if recovery was attempted, None if no recovery needed.
    ∀ chunk ∈ existing_ids: extraction_schema_version present ⟹ return None
    (noise chunks are excluded from extraction and do not trigger recovery).
    """
    if not existing_metadatas:
        return None
    if not any(
        _chunk_needs_extraction_for_recovery(
            m, extraction_model=config.extraction_model
        )
        for m in existing_metadatas
    ):
        return None

    # Gate: if all chunks are permanently failed, skip recovery entirely.
    max_attempts = config.max_extraction_attempts
    permanent = property_index.get_permanent_failures(source, max_attempts)
    if permanent.issuperset(set(existing_ids)):
        logger.warning(
            "Recovery skipped for %s: all %d chunks permanently failed"
            " (attempt_count >= %d)",
            source,
            len(existing_ids),
            max_attempts,
        )
        if event_bus is not None:
            _publish_event_nonblocking(
                event_bus,
                rag_extraction_batch_skipped(
                    file=source,
                    chunk_count=len(existing_ids),
                    skipped_count=len(existing_ids),
                    max_attempts=max_attempts,
                ),
            )
            _publish_event_nonblocking(
                event_bus,
                rag_extraction_recovery_skipped(
                    file=source, reason="all chunks permanently failed"
                ),
            )
        return None

    with_docs = collection.get(ids=existing_ids, include=["documents", "metadatas"])
    raw_ids: list[str] = with_docs.get("ids") or []
    raw_docs: list[str] = with_docs.get("documents") or []
    raw_metadatas: list[Any] = with_docs.get("metadatas") or []
    if len(raw_ids) != len(raw_docs) or len(raw_ids) != len(raw_metadatas):
        logger.warning(
            "Recovery skipped for %s: inconsistent ChromaDB payload lengths "
            "(ids=%d docs=%d metadatas=%d)",
            source,
            len(raw_ids),
            len(raw_docs),
            len(raw_metadatas),
        )
        if event_bus is not None:
            _publish_event_nonblocking(
                event_bus,
                rag_extraction_recovery_skipped(
                    file=source, reason="inconsistent ChromaDB payload lengths"
                ),
            )
        return None
    aligned: list[tuple[str, str, dict[str, Any]]] = [
        (i, d, m)
        for i, d, m in zip(raw_ids, raw_docs, raw_metadatas, strict=True)
        if isinstance(m, dict)
    ]
    if len(aligned) != len(raw_ids):
        logger.warning(
            "Recovery for %s dropped %d/%d chunks due to invalid metadata rows",
            source,
            len(raw_ids) - len(aligned),
            len(raw_ids),
        )
    ids: list[str] = [t[0] for t in aligned]
    docs: list[str] = [t[1] for t in aligned]
    metadatas: list[dict[str, Any]] = [t[2] for t in aligned]

    if not docs:
        logger.warning("Recovery: no documents found in ChromaDB for %s", source)
        if event_bus is not None:
            _publish_event_nonblocking(
                event_bus,
                rag_extraction_recovery_skipped(
                    file=source, reason="no documents in ChromaDB"
                ),
            )
        return None

    if not any(
        _chunk_needs_extraction_for_recovery(
            m, extraction_model=config.extraction_model
        )
        for m in metadatas
    ):
        return None

    extract_indices = [
        i for i, m in enumerate(metadatas) if not chunk_metadata_is_noise(m)
    ]
    ext_ids = [ids[i] for i in extract_indices]
    ext_docs = [docs[i] for i in extract_indices]
    ext_metadatas = [metadatas[i] for i in extract_indices]

    if not ext_ids:
        logger.info(
            "Recovery skipped for %s: all chunks are noise — no extraction targets",
            source,
        )
        if event_bus is not None:
            _publish_event_nonblocking(
                event_bus,
                rag_extraction_recovery_skipped(
                    file=source, reason="all chunks are noise"
                ),
            )
        return None

    # run_extraction only uses chunk.text — minimal Chunk objects are sufficient.
    chunks = [Chunk(text=doc, metadata={}) for doc in ext_docs]

    await property_index.mark_pending(source)

    ext_result = await run_extraction(
        file=source,
        ids=ext_ids,
        chunks=chunks,
        metadatas=ext_metadatas,
        config=config,
        property_index=property_index,
        event_bus=event_bus,
        apply_property_index=False,
        scope=scope,
    )

    if not ext_result.success:
        # Soft failure — all-or-nothing fired, property index untouched, safe to clear.
        await property_index.clear_pending(source)
        logger.warning(
            "Recovery extraction failed for %s; will retry on next sweep", source
        )
        if event_bus is not None:
            _publish_event_nonblocking(
                event_bus,
                rag_extraction_recovery_failed(
                    file=source, reason="recovery extraction returned unsuccessful"
                ),
            )
        return ext_result

    # Patch ChromaDB metadata in-place — embeddings and documents are unchanged.
    collection.update(ids=ext_ids, metadatas=ext_metadatas)
    if ext_result.property_entries:
        await property_index.add_batch_with_scope(ext_result.property_entries)
    await property_index.clear_pending(source)

    logger.info(
        "Recovery complete: file=%s entities=%d topics=%d",
        source,
        ext_result.entities,
        ext_result.topics,
    )
    if event_bus is not None:
        _publish_event_nonblocking(
            event_bus,
            rag_extraction_recovery_completed(
                file=source,
                entities=ext_result.entities,
                topics=ext_result.topics,
            ),
        )
    return ext_result
