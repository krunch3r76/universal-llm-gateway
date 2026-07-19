"""Persist discriminative corpus hints from property-index term statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from universal_logging import get_logger

from services.rag.corpus_hints.constants import (
    DEFAULT_KEY_PREFIXES,
    DEFAULT_MAX_CHUNKS_NAME,
    DEFAULT_MAX_CHUNKS_TOPIC,
    DEFAULT_MIN_CHUNKS_NAME,
    DEFAULT_MIN_CHUNKS_TOPIC,
    GENERIC_BLOCKLIST,
)
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
    """Persist discriminative scope hints to metadata SQLite tables."""
    prefixes = key_prefixes if key_prefixes is not None else DEFAULT_KEY_PREFIXES
    if property_index.get_total_chunks() == 0:
        logger.warning("PropertyIndex has 0 chunks — skipping corpus hints update")
        if event_bus is not None:
            await event_bus.publish_nowait(
                rag_corpus_hints_skipped(reason="property index has zero chunks")
            )
        return {}

    active_blocklist = (
        blocklist_override if blocklist_override is not None else GENERIC_BLOCKLIST
    )
    if extra_blocklist:
        active_blocklist = active_blocklist | extra_blocklist

    total_docs = property_index.get_total_docs()

    band_limits: dict[str, tuple[int, int]] = {
        "prop.name@@": (min_chunks_name, max_chunks_name),
        "prop.topic@@": (min_chunks_topic, max_chunks_topic),
    }
    budgets: dict[str, int] = {
        "prop.name@@": names_budget,
        "prop.topic@@": topics_budget,
    }

    scope_prefix_terms: dict[str, dict[str, list[tuple[str, int, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    scope_doc_counts: dict[str, int] = {}

    if configured_scopes is not None:
        for scope_name, source_prefixes in configured_scopes.items():
            if scope is not None and scope_name != scope:
                continue
            scope_doc_counts[scope_name] = property_index.count_docs_for_prefixes(
                source_prefixes
            )
            for prefix in prefixes:
                for (
                    term,
                    chunk_count,
                    doc_count,
                ) in property_index.get_term_counts_for_source_prefixes(
                    prefix, source_prefixes
                ):
                    if term:
                        scope_prefix_terms[scope_name][prefix].append(
                            (term, chunk_count, doc_count)
                        )
    else:
        for prefix in prefixes:
            for (
                scope_name,
                term,
                chunk_count,
                doc_count,
            ) in property_index.get_term_counts_by_scope(prefix):
                if scope is not None and scope_name != scope:
                    continue
                if term:
                    scope_prefix_terms[scope_name][prefix].append(
                        (term, chunk_count, doc_count)
                    )

    rows_for_db: list[tuple[str, str, float, str]] = []
    result: dict[str, str] = {}
    for scope_name, prefix_terms in scope_prefix_terms.items():
        scope_docs = scope_doc_counts.get(scope_name, 0)
        effective_min_docs = min_docs
        if scope_docs > 0:
            effective_min_docs = max(1, min(min_docs, scope_docs))
        winners: list[tuple[str, float, str]] = []
        for prefix, term_counts in prefix_terms.items():
            min_c, max_c = band_limits.get(prefix, (min_chunks_name, max_chunks_name))
            budget = budgets.get(prefix, names_budget)
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
                base_score = score_term(chunk_count, doc_count, total_docs)
                boost = entity_shape_boost(
                    term,
                    hyphen_boost=entity_boost_hyphen,
                    single_token_boost=entity_boost_single,
                )
                score = base_score * boost
                scored.append((term, score, prefix))
            scored.sort(key=lambda x: (-x[1], x[0]))
            winners.extend(scored[:budget])

        seen: set[str] = set()
        deduped_terms: list[str] = []
        for term, score, prefix in sorted(winners, key=lambda x: (-x[1], x[0])):
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped_terms.append(term)
            rows_for_db.append((scope_name, term, score, prefix))
        result[scope_name] = ", ".join(t for t in deduped_terms if t)

    if scope is not None:
        await property_index.replace_corpus_hints_for_scope(scope, rows_for_db)
    elif configured_scopes is not None:
        for cs_name in configured_scopes:
            cs_rows = [r for r in rows_for_db if r[0] == cs_name]
            await property_index.replace_corpus_hints_for_scope(cs_name, cs_rows)
    else:
        await property_index.replace_corpus_hints_rows(rows_for_db)

    if event_bus is not None:
        update_timestamp = datetime.now(UTC).isoformat()
        await event_bus.publish_nowait(
            rag_corpus_hints_updated(
                path=str(property_index.db_path),
                scopes_updated=sorted(result),
                timestamp=update_timestamp,
            )
        )
    return result
