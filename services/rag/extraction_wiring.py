"""Wire knowledge extraction into the indexing pipeline.

Extracted from rag_service.py to keep that module under the SLOC limit.
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

from services.rag.chunkers import Chunk
from services.rag.config import KnowledgeExtractionConfig
from services.rag.events import (
    rag_extraction_batch_completed,
    rag_extraction_batch_skipped,
    rag_extraction_batch_started,
    rag_extraction_completed,
    rag_extraction_failed,
    rag_extraction_permanently_skipped,
)
from services.rag.knowledge_extractor import ExtractedKnowledge, extract_knowledge_batch
from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)

# ≥90% success → write partial results (matches MapExecutor failure_threshold=0.1)
_FAILURE_THRESHOLD = 0.1


@dataclass(slots=True, kw_only=True)
class ExtractionResult:
    """Result of a knowledge extraction run for one file (batch of chunks).

    entities/topics are counts; property_entries are (key, chunk_id, scope, source)
    quads for the property index; success is True when partial or full write was done.
    batch_start_ts: ISO-8601 timestamp when extraction batch started (for per-file duration).
    """

    entities: int = 0
    topics: int = 0
    property_entries: list[tuple[str, str, str, str]] = field(default_factory=list)
    success: bool = field(default=False)
    batch_start_ts: str | None = None


def build_property_entries(
    knowledge: ExtractedKnowledge, chunk_id: str, scope: str = "all", source: str = ""
) -> list[tuple[str, str, str, str]]:
    """Build (key, chunk_id, scope, source) quads from extracted knowledge.

    Keys use prefixes: prop.name@@ (entity names), prop.type@@ (entity types),
    prop.facet@@ (facets), prop.rel@@ (relations), prop.topic@@ (topics).
    """
    return [
        (f"prop.name@@{entity.name}", chunk_id, scope, source)
        for entity in knowledge.entities
    ] + [
        (f"prop.type@@{etype}", chunk_id, scope, source)
        for entity in knowledge.entities
        for etype in entity.type
    ] + [
        (f"prop.facet@@{facet.name}:{facet.value}", chunk_id, scope, source)
        for entity in knowledge.entities
        for facet in entity.facets
    ] + [
        (
            f"prop.rel@@{entity.name}>{relation.predicate}>{relation.target}",
            chunk_id,
            scope,
            source,
        )
        for entity in knowledge.entities
        for relation in entity.relations
    ] + [
        (f"prop.topic@@{topic}", chunk_id, scope, source)
        for topic in knowledge.topics
    ]


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
        id_to_idx = {cid: i for i, cid in enumerate(ids)}
    # --- end permanent failure gate ---

    if event_bus is not None:
        result.batch_start_ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _publish_event_nonblocking(
            event_bus,
            rag_extraction_batch_started(file=file, chunk_count=len(ids)),
        )

    start = time.monotonic()
    knowledge_list = await extract_knowledge_batch(
        chunk_ids=ids,
        chunk_texts=[c.text for c in chunks],
        config=config,
    )
    duration = time.monotonic() - start

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
    failure_ratio = len(failed_ids) / len(ids) if ids else 0.0
    accept_partial = bool(failed_ids) and failure_ratio <= _FAILURE_THRESHOLD

    if failed_ids:
        if accept_partial:
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
            new_attempt_count = (
                property_index.get_failure_counts(file).get(chunk_id, 0) + 1
            )
            is_permanent = new_attempt_count >= config.max_extraction_attempts
            await property_index.record_failure(
                chunk_id=chunk_id,
                source=file,
                error="missing or invalid result after batch parsing",
                permanent=is_permanent,
            )
            if event_bus is not None:
                _publish_event_nonblocking(
                    event_bus,
                    rag_extraction_failed(
                        chunk_id=chunk_id,
                        error="missing or invalid result after batch parsing",
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
        # Clear failure records for this file — successful extraction supersedes them.
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
    ∀ chunk ∈ existing_ids: extraction_schema_version present ⟹ return None.
    """
    needs_recovery = any(
        "extraction_schema_version" not in m for m in existing_metadatas
    ) or (
        bool(config.extraction_model)
        and any(
            m.get("extraction_model") != config.extraction_model
            for m in existing_metadatas
        )
    )
    if not needs_recovery:
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
        return ExtractionResult()

    with_docs = collection.get(ids=existing_ids, include=["documents", "metadatas"])
    docs: list[str] = with_docs.get("documents") or []
    metadatas_from_db: list[Any] = with_docs.get("metadatas") or []
    metadatas: list[dict[str, Any]] = [
        m for m in metadatas_from_db if isinstance(m, dict)
    ]
    ids: list[str] = with_docs.get("ids") or []

    if not docs:
        logger.warning("Recovery: no documents found in ChromaDB for %s", source)
        return ExtractionResult()

    # run_extraction only uses chunk.text — minimal Chunk objects are sufficient.
    chunks = [Chunk(text=doc, metadata={}) for doc in docs]

    await property_index.mark_pending(source)

    ext_result = await run_extraction(
        file=source,
        ids=ids,
        chunks=chunks,
        metadatas=metadatas,
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
        return ext_result

    # Patch ChromaDB metadata in-place — embeddings and documents are unchanged.
    collection.update(ids=ids, metadatas=metadatas)
    if ext_result.property_entries:
        await property_index.add_batch_with_scope(ext_result.property_entries)
    await property_index.clear_pending(source)

    logger.info(
        "Recovery complete: file=%s entities=%d topics=%d",
        source,
        ext_result.entities,
        ext_result.topics,
    )
    return ext_result
