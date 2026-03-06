"""Wire knowledge extraction into the indexing pipeline.

Extracted from rag_service.py to keep that module under the SLOC limit.
Called between chunk_file() and embed_chunks() when extraction is enabled.
One pipeline call per file — MapExecutor fans out over all chunks in parallel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from universal_event_bus import EventBus

from services.rag.chunkers import Chunk
from services.rag.config import KnowledgeExtractionConfig
from services.rag.events import (
    RagExtractionBatchCompleted,
    RagExtractionBatchStarted,
    RagExtractionCompleted,
    RagExtractionFailed,
)
from services.rag.knowledge_extractor import ExtractedKnowledge, extract_knowledge_batch
from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class ExtractionResult:
    entities: int = 0
    topics: int = 0


def build_property_entries(
    knowledge: ExtractedKnowledge, chunk_id: str
) -> list[tuple[str, str]]:
    """Build (key, chunk_id) pairs from extracted knowledge for the property index."""
    entries: list[tuple[str, str]] = []
    for entity in knowledge.entities:
        entries.append((f"prop.name@@{entity.name}", chunk_id))
        for etype in entity.type:
            entries.append((f"prop.type@@{etype}", chunk_id))
        for facet in entity.facets:
            entries.append((f"prop.facet@@{facet.name}:{facet.value}", chunk_id))
        for relation in entity.relations:
            entries.append(
                (f"prop.rel@@{entity.name}>{relation.predicate}>{relation.target}", chunk_id)
            )
    for topic in knowledge.topics:
        entries.append((f"prop.topic@@{topic}", chunk_id))
    return entries


async def run_extraction(
    *,
    file: str,
    ids: list[str],
    chunks: list[Chunk],
    metadatas: list[dict[str, Any]],
    config: KnowledgeExtractionConfig,
    property_index: PropertyIndex,
    event_bus: EventBus | None,
) -> ExtractionResult:
    """Run knowledge extraction on all chunks and populate the property index.

    Makes one pipeline call per file; MapExecutor fans out over chunks in parallel.
    Modifies metadatas in-place: adds 'extraction' and 'extraction_schema_version'.

    All-or-nothing per file: if any chunk fails, nothing is written. This ensures
    all chunks of a file are always extracted in a single pipeline call, keeping
    entity representations consistent across chunks (same model state, same session).
    ∀ file: (∀ chunk: extracted) ∨ (∀ chunk: unextracted)
    """
    result = ExtractionResult()
    id_to_idx = {chunk_id: i for i, chunk_id in enumerate(ids)}

    if event_bus is not None:
        asyncio.create_task(
            event_bus.publish_async_nowait(
                RagExtractionBatchStarted(file=file, chunk_count=len(ids))
            )
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

    if failed_ids:
        logger.warning(
            "Extraction skipped for %s: %d/%d chunks failed — will retry on next sweep",
            file, len(failed_ids), len(ids),
        )
        for chunk_id in failed_ids:
            if event_bus is not None:
                asyncio.create_task(
                    event_bus.publish_async_nowait(
                        RagExtractionFailed(chunk_id=chunk_id, error="no result from pipeline")
                    )
                )
        successful = 0
    else:
        for chunk_id, knowledge in staged.items():
            idx = id_to_idx[chunk_id]
            result.entities += len(knowledge.entities)
            result.topics += len(knowledge.topics)
            prop_entries = build_property_entries(knowledge, chunk_id)
            await property_index.add_batch(prop_entries)
            metadatas[idx]["extraction"] = json.dumps(knowledge.to_dict())
            metadatas[idx]["extraction_schema_version"] = config.schema_version
            if event_bus is not None:
                asyncio.create_task(
                    event_bus.publish_async_nowait(
                        RagExtractionCompleted(
                            chunk_id=chunk_id,
                            entities=len(knowledge.entities),
                            topics=len(knowledge.topics),
                        )
                    )
                )
        successful = len(staged)

    if event_bus is not None:
        asyncio.create_task(
            event_bus.publish_async_nowait(
                RagExtractionBatchCompleted(
                    file=file,
                    chunk_count=len(ids),
                    successful=successful,
                    duration_seconds=duration,
                )
            )
        )

    return result
