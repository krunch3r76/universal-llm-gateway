"""Run LLM knowledge extraction for every pending chunk of one ChromaDB source.

``extract_source`` is called by ``extraction.worker_loop`` for each claimed
queue row. It selects non-noise chunks lacking ``extraction_schema_version``,
submits them to the Stargate extraction pipeline, patches chunk metadata in
ChromaDB and writes property-index quads via ``build_property_entries``. If
recording the execution id fails, the Stargate execution is cancelled so no
orphan run exists without a database pointer.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from services.rag.chroma_writer import (
    chroma_get_where,
    chroma_update_metadatas,
    run_chroma,
)
from services.rag.chunk_filters import chunk_metadata_is_noise
from services.rag.extraction.oversize_skip import (
    all_non_oversize_failures_backend_unavailable,
    classify_iteration_results,
    hint_error_by_index,
    is_prompt_too_large,
    oversize_indexes_from_message,
)
from services.rag.extraction.property_entries import build_property_entries
from services.rag.knowledge_extractor import (
    CancelExtractionOutcome,
    ExtractedKnowledge,
    cancel_extraction_execution,
    poll_extraction_result,
    submit_extraction_pipeline,
)

if TYPE_CHECKING:
    import chromadb

    from services.rag.config import KnowledgeExtractionConfig, RagConfig
    from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)


async def reclaim_cancel_stored_execution(
    *,
    source: str,
    prior_execution_id: str | None,
    property_index: PropertyIndex,
) -> None:
    """Cancel a stale Stargate run at claim time; clear the pointer only if confirmed.

    After force-cut or map stall, reclaim must not stack duplicate pipelines.
    When Stargate is down, leave ``active_execution_id`` so startup recovery can
    retry the cancel. Submit (in ``extract_source``) proceeds regardless.
    """
    if not prior_execution_id:
        return
    outcome = await cancel_extraction_execution(prior_execution_id)
    if outcome in (
        CancelExtractionOutcome.CANCELLED,
        CancelExtractionOutcome.ALREADY_GONE,
    ):
        await property_index.clear_extraction_execution_id(source)


async def _write_successful_extractions(
    *,
    staged: dict[str, ExtractedKnowledge],
    ext_ids: list[str],
    ext_metas: list[Any],
    source: str,
    collection: chromadb.Collection,
    config: KnowledgeExtractionConfig,
    rag_config: RagConfig,
    property_index: PropertyIndex,
) -> None:
    if not staged:
        return
    scope = rag_config.get_scope_for_path(source)
    update_ids: list[str] = []
    update_metas: list[dict[str, Any]] = []
    all_prop_entries: list[tuple[str, str, str, str]] = []

    for chunk_id, knowledge in staged.items():
        idx_in_ext = ext_ids.index(chunk_id)
        meta = ext_metas[idx_in_ext]
        if not isinstance(meta, dict):
            continue
        meta["extraction"] = json.dumps(knowledge.to_dict())
        meta["extraction_schema_version"] = config.schema_version
        meta["extraction_model"] = config.extraction_model
        update_ids.append(chunk_id)
        update_metas.append(meta)
        all_prop_entries.extend(
            build_property_entries(knowledge, chunk_id, scope, source)
        )

    await run_chroma(chroma_update_metadatas, collection, update_ids, update_metas)
    if all_prop_entries:
        await property_index.add_batch_with_scope(all_prop_entries)


async def _mark_extraction_skipped(
    *,
    collection: chromadb.Collection,
    ext_ids: list[str],
    ext_metas: list[Any],
    indexes: set[int],
) -> bool:
    if not indexes:
        return True
    update_ids: list[str] = []
    update_metas: list[dict[str, Any]] = []
    for idx in sorted(indexes):
        if idx < 0 or idx >= len(ext_ids):
            continue
        meta = ext_metas[idx]
        if not isinstance(meta, dict):
            meta = {}
            ext_metas[idx] = meta
        meta = dict(meta)
        meta["extraction_skipped"] = True
        ext_metas[idx] = meta
        update_ids.append(ext_ids[idx])
        update_metas.append(meta)
    try:
        await run_chroma(
            chroma_update_metadatas, collection, update_ids, update_metas
        )
    except Exception:
        logger.exception("Extraction: failed to write extraction_skipped")
        return False
    return True


def _stage_parsed(
    parsed: list[ExtractedKnowledge | None],
    ext_ids: list[str],
) -> dict[str, ExtractedKnowledge]:
    staged: dict[str, ExtractedKnowledge] = {}
    for idx, knowledge in enumerate(parsed):
        if knowledge is None or idx >= len(ext_ids):
            continue
        chunk_id = ext_ids[idx]
        staged[chunk_id] = knowledge
    return staged


def _partial_extraction_error(
    failed_count: int,
    total: int,
    timing: dict[str, Any],
) -> str:
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
            f"{failed_count}/{total} chunks failed extraction ({compact_reasons})"
        )
    else:
        error = f"{failed_count}/{total} chunks failed extraction"
    finish_reason = timing.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason:
        error = f"{error}; finish_reason={finish_reason}"
    return error


async def _finalize_claim(
    *,
    source: str,
    ext_ids: list[str],
    ext_metas: list[Any],
    staged: dict[str, ExtractedKnowledge],
    skipped_indexes: set[int],
    duration: float,
    timing: dict[str, Any],
    increment_attempt: bool | None = None,
) -> tuple[bool, bool, str, str, str]:
    skipped_ids = {ext_ids[i] for i in skipped_indexes if 0 <= i < len(ext_ids)}
    failed_ids = [
        cid
        for cid in ext_ids
        if cid not in staged and cid not in skipped_ids
    ]
    succeeded = len(staged)
    logger.info(
        "Extraction: %s — %d/%d chunks succeeded (%.1fs)",
        source,
        succeeded,
        len(ext_ids),
        duration,
    )
    all_done = not failed_ids
    if increment_attempt is None:
        increment_attempt = succeeded > 0 and not all_done
    elif all_done:
        increment_attempt = False
    if all_done:
        return True, increment_attempt, "", "", ""
    error = _partial_extraction_error(len(failed_ids), len(ext_ids), timing)
    return False, increment_attempt, "pipeline_output", error, "PartialExtraction"


async def _handle_completed_poll(
    *,
    source: str,
    collection: chromadb.Collection,
    config: KnowledgeExtractionConfig,
    rag_config: RagConfig,
    property_index: PropertyIndex,
    ext_ids: list[str],
    ext_metas: list[Any],
    parsed: list[ExtractedKnowledge | None],
    timing: dict[str, Any],
    duration: float,
) -> tuple[bool, bool, str, str, str]:
    hint_by_index = hint_error_by_index(timing.get("result_hints") or [])
    skip_indexes: set[int] = set()
    for idx in range(len(ext_ids)):
        if idx < len(parsed) and parsed[idx] is not None:
            continue
        if is_prompt_too_large(hint_by_index.get(idx)):
            skip_indexes.add(idx)

    if not await _mark_extraction_skipped(
        collection=collection,
        ext_ids=ext_ids,
        ext_metas=ext_metas,
        indexes=skip_indexes,
    ):
        return False, True, "pipeline_output", "extraction_skipped write failed", (
            "PartialExtraction"
        )

    staged = _stage_parsed(parsed, ext_ids)
    await _write_successful_extractions(
        staged=staged,
        ext_ids=ext_ids,
        ext_metas=ext_metas,
        source=source,
        collection=collection,
        config=config,
        rag_config=rag_config,
        property_index=property_index,
    )
    return await _finalize_claim(
        source=source,
        ext_ids=ext_ids,
        ext_metas=ext_metas,
        staged=staged,
        skipped_indexes=skip_indexes,
        duration=duration,
        timing=timing,
    )


async def _handle_failed_poll(
    *,
    source: str,
    collection: chromadb.Collection,
    config: KnowledgeExtractionConfig,
    rag_config: RagConfig,
    property_index: PropertyIndex,
    ext_ids: list[str],
    ext_texts: list[str],
    ext_metas: list[Any],
    timing: dict[str, Any],
    duration: float,
    on_execution_id: Callable[[str], Awaitable[None]] | None,
) -> tuple[bool, bool, str, str, str]:
    error_data = timing.get("error_data")
    iteration_results: list[dict[str, Any]] = []
    if isinstance(error_data, dict):
        raw_results = error_data.get("iteration_results")
        if isinstance(raw_results, list):
            iteration_results = [r for r in raw_results if isinstance(r, dict)]

    if iteration_results:
        oversize_failed, completed, has_non_oversize = classify_iteration_results(
            iteration_results
        )
    else:
        message = timing.get("error_message")
        oversize_failed = oversize_indexes_from_message(
            message if isinstance(message, str) else "", len(ext_ids)
        )
        completed = set()
        pending = set(range(len(ext_ids))) - oversize_failed
        has_non_oversize = bool(pending)

    if not await _mark_extraction_skipped(
        collection=collection,
        ext_ids=ext_ids,
        ext_metas=ext_metas,
        indexes=oversize_failed,
    ):
        return False, True, "pipeline_output", "extraction_skipped write failed", (
            "PartialExtraction"
        )

    if has_non_oversize:
        burn_attempt = True
        if iteration_results and all_non_oversize_failures_backend_unavailable(
            iteration_results
        ):
            burn_attempt = False
        return await _finalize_claim(
            source=source,
            ext_ids=ext_ids,
            ext_metas=ext_metas,
            staged={},
            skipped_indexes=oversize_failed,
            duration=duration,
            timing=timing,
            increment_attempt=burn_attempt,
        )

    resubmit_indexes = sorted(completed)
    if not resubmit_indexes:
        return await _finalize_claim(
            source=source,
            ext_ids=ext_ids,
            ext_metas=ext_metas,
            staged={},
            skipped_indexes=oversize_failed,
            duration=duration,
            timing=timing,
        )

    resubmit_ids = [ext_ids[i] for i in resubmit_indexes]
    resubmit_texts = [ext_texts[i] for i in resubmit_indexes]
    execution_id = await submit_extraction_pipeline(
        resubmit_ids, resubmit_texts, config
    )
    if on_execution_id is not None:
        with contextlib.suppress(Exception):
            await on_execution_id(execution_id)
    parsed, resubmit_timing = await poll_extraction_result(execution_id, resubmit_ids)

    if resubmit_timing.get("pipeline_failed"):
        message = resubmit_timing.get("error_message")
        second_oversize = oversize_indexes_from_message(
            message if isinstance(message, str) else "", len(resubmit_ids)
        )
        error_data2 = resubmit_timing.get("error_data")
        if isinstance(error_data2, dict):
            raw2 = error_data2.get("iteration_results")
            if isinstance(raw2, list):
                second_batch_oversize, _, _ = classify_iteration_results(raw2)
                second_oversize = {
                    resubmit_indexes[i]
                    for i in second_batch_oversize
                    if i < len(resubmit_indexes)
                }
        else:
            second_oversize = {
                resubmit_indexes[i]
                for i in second_oversize
                if i < len(resubmit_indexes)
            }
        combined_skip = set(oversize_failed) | second_oversize
        if not await _mark_extraction_skipped(
            collection=collection,
            ext_ids=ext_ids,
            ext_metas=ext_metas,
            indexes=combined_skip,
        ):
            return False, True, "pipeline_output", "extraction_skipped write failed", (
                "PartialExtraction"
            )
        return await _finalize_claim(
            source=source,
            ext_ids=ext_ids,
            ext_metas=ext_metas,
            staged={},
            skipped_indexes=combined_skip,
            duration=duration,
            timing=resubmit_timing,
        )

    aligned = _align_resubmit_parsed(parsed, ext_ids, resubmit_ids)
    hint_by_index = hint_error_by_index(resubmit_timing.get("result_hints") or [])
    extra_skip: set[int] = set()
    for res_idx, chunk_id in enumerate(resubmit_ids):
        ext_idx = ext_ids.index(chunk_id)
        if res_idx < len(parsed) and parsed[res_idx] is not None:
            continue
        if is_prompt_too_large(hint_by_index.get(res_idx)):
            extra_skip.add(ext_idx)

    skip_indexes = set(oversize_failed) | extra_skip
    if not await _mark_extraction_skipped(
        collection=collection,
        ext_ids=ext_ids,
        ext_metas=ext_metas,
        indexes=extra_skip,
    ):
        return False, True, "pipeline_output", "extraction_skipped write failed", (
            "PartialExtraction"
        )

    staged = _stage_parsed(aligned, ext_ids)
    await _write_successful_extractions(
        staged=staged,
        ext_ids=ext_ids,
        ext_metas=ext_metas,
        source=source,
        collection=collection,
        config=config,
        rag_config=rag_config,
        property_index=property_index,
    )
    return await _finalize_claim(
        source=source,
        ext_ids=ext_ids,
        ext_metas=ext_metas,
        staged=staged,
        skipped_indexes=skip_indexes,
        duration=duration,
        timing=resubmit_timing,
    )


def _align_resubmit_parsed(
    parsed: list[ExtractedKnowledge | None],
    ext_ids: list[str],
    resubmit_ids: list[str],
) -> list[ExtractedKnowledge | None]:
    """Merge a resubmit batch result back into full ext_ids order."""
    aligned: list[ExtractedKnowledge | None] = [None] * len(ext_ids)
    for res_idx, chunk_id in enumerate(resubmit_ids):
        if res_idx >= len(parsed):
            break
        ext_idx = ext_ids.index(chunk_id)
        aligned[ext_idx] = parsed[res_idx]
    return aligned


async def extract_source(
    source: str,
    *,
    collection: chromadb.Collection,
    config: KnowledgeExtractionConfig,
    rag_config: RagConfig,
    property_index: PropertyIndex,
    on_execution_id: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[bool, bool, str, str, str]:
    """Extract one source.

    Returns ``(all_done, increment_attempt, category, error, error_type)`` where
    failure details are populated only when ``all_done`` is False.
    """
    existing = await run_chroma(
        chroma_get_where,
        collection,
        where={"source": source},
        include=["documents", "metadatas"],
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
    execution_id = await submit_extraction_pipeline(ext_ids, ext_texts, config)
    if on_execution_id is not None:
        try:
            await on_execution_id(execution_id)
        except Exception:
            # Recording failed (executor stopped, disk full, etc.) — cancel the
            # Stargate execution so no orphan runs without a DB pointer, then re-raise.
            with contextlib.suppress(Exception):
                await cancel_extraction_execution(execution_id)
            raise
    parsed, timing = await poll_extraction_result(execution_id, ext_ids)
    duration = time.monotonic() - start

    if timing.get("pipeline_failed"):
        return await _handle_failed_poll(
            source=source,
            collection=collection,
            config=config,
            rag_config=rag_config,
            property_index=property_index,
            ext_ids=ext_ids,
            ext_texts=ext_texts,
            ext_metas=ext_metas,
            timing=timing,
            duration=duration,
            on_execution_id=on_execution_id,
        )

    return await _handle_completed_poll(
        source=source,
        collection=collection,
        config=config,
        rag_config=rag_config,
        property_index=property_index,
        ext_ids=ext_ids,
        ext_metas=ext_metas,
        parsed=parsed,
        timing=timing,
        duration=duration,
    )
