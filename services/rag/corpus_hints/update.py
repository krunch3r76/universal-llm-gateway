"""Persist discriminative corpus hints from property-index term statistics.

Computes, per RAG scope, the best ``prop.name@@`` and ``prop.topic@@`` terms
(chunk-band filtered, blocklisted, noise-filtered, IDF-scored) and writes them
to the ``corpus_hints`` table via ``PropertyIndex``. Called by the corpus hints
CLI, ``rag_service/state.py`` after indexing, the admin articles route and
vocabulary repair. Emits ``rag_corpus_hints_updated`` or
``rag_corpus_hints_skipped`` when an event bus is supplied.

Term statistics and scoring run on a worker thread (``read_corpus_hint_stats``).
Running that scan on the asyncio thread blocks ``GET /scopes`` for the whole
rebuild — about 60s on the live index — and scoped search then reports the
catalog as unavailable. Overlapping callers share one scan: a later request
dirties the in-flight rebuild and is folded into a single follow-up, so an
older snapshot cannot commit after a newer one.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from services.rag.corpus_hints.constants import (
    DEFAULT_KEY_PREFIXES,
    DEFAULT_MAX_CHUNKS_NAME,
    DEFAULT_MAX_CHUNKS_TOPIC,
    DEFAULT_MIN_CHUNKS_NAME,
    DEFAULT_MIN_CHUNKS_TOPIC,
    GENERIC_BLOCKLIST,
)
from services.rag.corpus_hints.rebuild_gate import (
    HintRebuildRequest,
    gate_for,
)
from services.rag.corpus_hints.stats_read import CorpusHintStats, read_corpus_hint_stats
from services.rag.corpus_hints.term_scoring import (
    entity_shape_boost,
    is_structural_noise,
    score_term,
)
from services.rag.events.query import rag_corpus_hints_skipped, rag_corpus_hints_updated
from services.rag.property_index import PropertyIndex

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)

__all__ = ["update_corpus_hints"]


async def update_corpus_hints(
    property_index: PropertyIndex,
    *,
    scope: str | None = None,
    names_budget: int = 15,
    topics_budget: int = 12,
    min_chunks_name: int = DEFAULT_MIN_CHUNKS_NAME,
    min_chunks_topic: int = DEFAULT_MIN_CHUNKS_TOPIC,
    max_chunks_name: int = DEFAULT_MAX_CHUNKS_NAME,
    max_chunks_topic: int = DEFAULT_MAX_CHUNKS_TOPIC,
    min_docs: int = 2,
    entity_boost_hyphen: float = 1.3,
    entity_boost_single: float = 1.2,
    extra_blocklist: frozenset[str] = frozenset(),
    blocklist_override: frozenset[str] | None = None,
    key_prefixes: list[str] | None = None,
    configured_scopes: dict[str, list[str]] | None = None,
    event_bus: EventBus | None = None,
) -> dict[str, str]:
    """Select top-scoring hint terms per scope and replace the stored hint rows.

    Terms come from ``configured_scopes`` source prefixes when given, otherwise
    from the property index's own scope column. Each key prefix keeps terms
    within its chunk band, spanning ``min_docs`` docs (capped by scope size),
    not noise or blocklisted, and keeps its best ``names_budget`` /
    ``topics_budget`` terms by ``score_term * entity_shape_boost``; winners
    are deduped case-insensitively.

    Side effects: replaces rows for ``scope``, for each configured scope, or
    the whole table; publishes ``rag_corpus_hints_updated`` (or
    ``rag_corpus_hints_skipped`` when the index has zero chunks).

    Returns:
        Mapping of scope name to comma-joined hint terms; {} when skipped.
    """
    request = HintRebuildRequest(
        scope=scope,
        configured_scopes=configured_scopes,
        key_prefixes=key_prefixes if key_prefixes is not None else DEFAULT_KEY_PREFIXES,
        names_budget=names_budget,
        topics_budget=topics_budget,
        min_chunks_name=min_chunks_name,
        min_chunks_topic=min_chunks_topic,
        max_chunks_name=max_chunks_name,
        max_chunks_topic=max_chunks_topic,
        min_docs=min_docs,
        entity_boost_hyphen=entity_boost_hyphen,
        entity_boost_single=entity_boost_single,
        extra_blocklist=extra_blocklist,
        blocklist_override=blocklist_override,
        event_bus=event_bus,
    )
    return await gate_for(Path(property_index.db_path)).run(
        request,
        lambda current: _rebuild(property_index, current),
    )


def _read_and_score(
    db_path: Path,
    request: HintRebuildRequest,
) -> tuple[dict[str, str], list[tuple[str, str, float, str]]] | None:
    """Scan and score off the event loop. None means the index has no chunks."""
    stats = read_corpus_hint_stats(
        db_path,
        configured_scopes=request.configured_scopes,
        key_prefixes=request.key_prefixes,
        only_scope=request.scope,
    )
    if stats.total_chunks == 0:
        return None
    return _score_hint_rows(stats, request)


def _score_hint_rows(
    stats: CorpusHintStats,
    request: HintRebuildRequest,
) -> tuple[dict[str, str], list[tuple[str, str, float, str]]]:
    active_blocklist = (
        request.blocklist_override
        if request.blocklist_override is not None
        else GENERIC_BLOCKLIST
    )
    if request.extra_blocklist:
        active_blocklist = active_blocklist | request.extra_blocklist
    band_limits: dict[str, tuple[int, int]] = {
        "prop.name@@": (request.min_chunks_name, request.max_chunks_name),
        "prop.topic@@": (request.min_chunks_topic, request.max_chunks_topic),
    }
    budgets: dict[str, int] = {
        "prop.name@@": request.names_budget,
        "prop.topic@@": request.topics_budget,
    }
    rows_for_db: list[tuple[str, str, float, str]] = []
    result: dict[str, str] = {}
    for scope_name, prefix_terms in stats.scope_prefix_terms.items():
        scope_docs = stats.scope_doc_counts.get(scope_name, 0)
        effective_min_docs = request.min_docs
        if scope_docs > 0:
            effective_min_docs = max(1, min(request.min_docs, scope_docs))
        winners: list[tuple[str, float, str]] = []
        for prefix, term_counts in prefix_terms.items():
            min_c, max_c = band_limits.get(
                prefix, (request.min_chunks_name, request.max_chunks_name)
            )
            budget = budgets.get(prefix, request.names_budget)
            scored: list[tuple[str, float, str]] = []
            for term, chunk_count, doc_count in term_counts:
                if chunk_count < min_c or chunk_count > max_c:
                    continue
                if doc_count > 0 and doc_count < effective_min_docs:
                    continue
                if is_structural_noise(term):
                    continue
                if term.lower() in active_blocklist:
                    continue
                base_score = score_term(chunk_count, doc_count, stats.total_docs)
                boost = entity_shape_boost(
                    term,
                    hyphen_boost=request.entity_boost_hyphen,
                    single_token_boost=request.entity_boost_single,
                )
                scored.append((term, base_score * boost, prefix))
            scored.sort(key=lambda item: (-item[1], item[0]))
            winners.extend(scored[:budget])
        seen: set[str] = set()
        deduped_terms: list[str] = []
        for term, score, prefix in sorted(winners, key=lambda item: (-item[1], item[0])):
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped_terms.append(term)
            rows_for_db.append((scope_name, term, score, prefix))
        result[scope_name] = ", ".join(term for term in deduped_terms if term)
    return result, rows_for_db


async def _rebuild(
    property_index: PropertyIndex,
    request: HintRebuildRequest,
) -> dict[str, str]:
    payload = await asyncio.to_thread(
        _read_and_score, Path(property_index.db_path), request
    )
    if payload is None:
        logger.warning("PropertyIndex has 0 chunks — skipping corpus hints update")
        bus: Any = request.event_bus
        if bus is not None:
            await bus.publish_nowait(
                rag_corpus_hints_skipped(reason="property index has zero chunks")
            )
        return {}
    result, rows_for_db = payload
    scope = request.scope
    configured_scopes = request.configured_scopes
    if scope is not None:
        await property_index.replace_corpus_hints_for_scope(scope, rows_for_db)
    elif configured_scopes is not None:
        for cs_name in configured_scopes:
            cs_rows = [row for row in rows_for_db if row[0] == cs_name]
            await property_index.replace_corpus_hints_for_scope(cs_name, cs_rows)
    else:
        await property_index.replace_corpus_hints_rows(rows_for_db)
    bus: Any = request.event_bus
    if bus is not None:
        await bus.publish_nowait(
            rag_corpus_hints_updated(
                path=str(property_index.db_path),
                scopes_updated=sorted(result),
                timestamp=datetime.now(UTC).isoformat(),
            )
        )
    return result
