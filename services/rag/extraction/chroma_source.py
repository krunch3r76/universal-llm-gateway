"""Run extraction for one Chroma source."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from services.rag.chunk_filters import chunk_metadata_is_noise
from services.rag.extraction.property_entries import build_property_entries
from services.rag.knowledge_extractor import (
    ExtractedKnowledge,
    extract_file_chunks,
)

if TYPE_CHECKING:
    import chromadb

    from services.rag.config import KnowledgeExtractionConfig, RagConfig
    from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)


async def extract_source(
    source: str,
    *,
    collection: chromadb.Collection,
    config: KnowledgeExtractionConfig,
    rag_config: RagConfig,
    property_index: PropertyIndex,
) -> tuple[bool, bool, str, str, str]:
    """Extract one source.

    Returns ``(all_done, increment_attempt, category, error, error_type)`` where
    failure details are populated only when ``all_done`` is False.
    """
    existing = collection.get(
        where={"source": source}, include=["documents", "metadatas"]
    )
    raw_ids: list[str] = existing.get("ids") or []
    raw_docs: list[str] = existing.get("documents") or []
    raw_metas: list[Any] = existing.get("metadatas") or []

    if not raw_ids:
        logger.info("Extraction: source removed from index, skipping: %s", source)
        return True, True, "", "", ""

    if len(raw_ids) != len(raw_docs) or len(raw_ids) != len(raw_metas):
        error = (
            "inconsistent ChromaDB payload "
            f"(ids={len(raw_ids)} docs={len(raw_docs)} metas={len(raw_metas)})"
        )
        logger.warning("Extraction: %s for %s", error, source)
        return False, True, "source_state", error, "InconsistentChromaPayload"

    need_idx = [
        i
        for i, m in enumerate(raw_metas)
        if isinstance(m, dict)
        and not chunk_metadata_is_noise(m)
        and "extraction_schema_version" not in m
        and not m.get("extraction_skipped")
    ]

    if not need_idx:
        return True, True, "", "", ""

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
    if all_done:
        return True, increment_attempt, "", "", ""

    parse_failure_reasons = timing.get("parse_failure_reasons")
    if isinstance(parse_failure_reasons, dict) and parse_failure_reasons:
        reason_counts: dict[str, int] = {}
        for reason in parse_failure_reasons.values():
            key = str(reason)
            reason_counts[key] = reason_counts.get(key, 0) + 1
        compact_reasons = ", ".join(
            f"{reason}:{count}" for reason, count in sorted(reason_counts.items())
        )
        error = (
            f"{len(failed_ids)}/{len(ext_ids)} chunks failed extraction "
            f"({compact_reasons})"
        )
    else:
        error = f"{len(failed_ids)}/{len(ext_ids)} chunks failed extraction"
    finish_reason = timing.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason:
        error = f"{error}; finish_reason={finish_reason}"
    return False, increment_attempt, "pipeline_output", error, "PartialExtraction"
