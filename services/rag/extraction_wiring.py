"""Wire knowledge extraction into the indexing pipeline.

Extracted from rag_service.py to keep that module under the SLOC limit.
Called between chunk_file() and embed_chunks() when extraction is enabled.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from universal_event_bus import EventBus

from services.rag.chunkers import Chunk
from services.rag.config import ExtractionConfig
from services.rag.events import RagExtractionCompleted, RagExtractionFailed
from services.rag.knowledge_extractor import ExtractedKnowledge, extract_knowledge
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
    for topic in knowledge.topics:
        entries.append((f"prop.topic@@{topic}", chunk_id))
    return entries


async def run_extraction(
    *,
    ids: list[str],
    chunks: list[Chunk],
    metadatas: list[dict[str, Any]],
    config: ExtractionConfig,
    property_index: PropertyIndex,
    event_bus: EventBus | None,
) -> ExtractionResult:
    """Run knowledge extraction on all chunks and populate the property index.

    Modifies metadatas in-place: adds 'extraction' and 'extraction_schema_version'.
    """
    result = ExtractionResult()
    for chunk_id, chunk in zip(ids, chunks, strict=True):
        text = chunk.text
        knowledge = await extract_knowledge(text, config, chunk_id)
        if knowledge is not None:
            result.entities += len(knowledge.entities)
            result.topics += len(knowledge.topics)
            prop_entries = build_property_entries(knowledge, chunk_id)
            await property_index.add_batch(prop_entries)
            idx = ids.index(chunk_id)
            metadatas[idx]["extraction"] = str(knowledge.to_dict())
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
        elif event_bus is not None:
            asyncio.create_task(
                event_bus.publish_async_nowait(
                    RagExtractionFailed(
                        chunk_id=chunk_id,
                        error="extraction returned None",
                    )
                )
            )
    return result
