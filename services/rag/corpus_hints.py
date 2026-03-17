"""Load and format corpus hints for RAG prompt injection.

Runtime readers and writers use normalized rows in the metadata database so
retrieval hot paths avoid YAML parsing and file-level artifact drift.
"""

from __future__ import annotations

import contextlib
import logging
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from universal_event_bus import EventBus

from services.rag.events.query import (
    rag_corpus_hints_filter_failed,
    rag_corpus_hints_load_failed,
    rag_corpus_hints_skipped,
    rag_corpus_hints_updated,
    rag_scope_vocabulary_load_failed,
)
from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)

_DEFAULT_KEY_PREFIXES = ["prop.name@@", "prop.topic@@"]
_DEFAULT_MIN_CHUNKS_NAME = 2
_DEFAULT_MAX_CHUNKS_NAME = 50
_DEFAULT_MIN_CHUNKS_TOPIC = 3
_DEFAULT_MAX_CHUNKS_TOPIC = 30
_DEFAULT_METADATA_DB_PATH = Path.home() / ".rag" / "store" / "rag_metadata.db"

_GENERIC_BLOCKLIST: frozenset[str] = frozenset(
    {
        "llm",
        "llms",
        "large language models",
        "language models",
        "gpt-4",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-3.5",
        "gpt-3.5-turbo",
        "rag",
        "retrieval-augmented generation",
        "bert",
        "openai",
        "chatgpt",
        "datasets",
        "benchmarks",
        "models",
        "tools",
        "services",
        "arxiv",
        "institutions",
        "libraries",
        "pipeline",
        "stargate",
        "gateway",
        "language agents",
        "task allocation",
        "schema discovery",
        "provenance analysis",
        "prompt transfer",
        "efficient llm reasoning",
        "system 2 reasoning",
        "knowledge graph storage and retrieval",
        "ai agentic programming",
        "knowledge measures",
        "knowledge representation",
    }
)


def _entity_shape_boost(
    term: str,
    *,
    hyphen_boost: float = 1.3,
    single_token_boost: float = 1.2,
) -> float:
    """Return a multiplicative boost based on term shape.

    Hyphenated terms (e.g. "chain-of-thought", "bge-m3") get *hyphen_boost*.
    Single-token terms (e.g. "NEPOMUK") get *single_token_boost*.
    Multi-word phrases get 1.0 (no boost).
    """
    if "-" in term:
        return hyphen_boost
    if " " not in term:
        return single_token_boost
    return 1.0


def _score_term(chunk_count: int, doc_count: int, total_docs: int) -> float:
    """Hybrid IDF + chunk-boost score using document frequency.

    IDF rewards rarity: log(total_docs / doc_count) is highest for terms
    appearing in few documents. Band filters enforce minimum spread externally.
    chunk_boost rewards thorough coverage within documents (0.3 weight).
    Falls back to chunk-only scoring when doc_count is 0 (un-backfilled).
    """
    if doc_count == 0:
        return math.log(1 + chunk_count) if chunk_count > 0 else 0.0
    idf = math.log(total_docs / doc_count)
    chunk_boost = math.log(1 + chunk_count / doc_count) * 0.3
    return idf + chunk_boost


def load_corpus_hints(
    db_path: Path | None = None, event_bus: EventBus | None = None
) -> dict[str, str]:
    """Read corpus hints from the metadata database.

    Returns ``{scope: "term1, term2, ..."}`` ordered by scope ASC, score DESC,
    term ASC. Missing DB files, absent tables, or read errors return an empty
    mapping.
    """
    resolved = db_path or _DEFAULT_METADATA_DB_PATH
    if not resolved.exists():
        return {}
    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        ) as conn:
            rows = conn.execute(
                "SELECT scope, term FROM corpus_hints "
                "ORDER BY scope ASC, score DESC, term ASC"
            ).fetchall()
    except sqlite3.Error as e:
        logger.warning("Failed to load corpus hints from DB %s: %s", resolved, e)
        if event_bus is not None:
            event_bus.publish_async_nowait(
                rag_corpus_hints_load_failed(path=str(resolved), error=str(e))
            )
        return {}
    except Exception as e:
        logger.error(
            "Unexpected error loading corpus hints from DB %s: %s",
            resolved,
            e,
            exc_info=True,
        )
        return {}
    grouped: dict[str, list[str]] = defaultdict(list)
    for scope, term in rows:
        if isinstance(scope, str) and isinstance(term, str) and term.strip():
            grouped[scope].append(term.strip())
    return {scope: ", ".join(terms) for scope, terms in grouped.items()}


def load_scope_vocabulary(
    db_path: Path | None = None, event_bus: EventBus | None = None
) -> dict[str, dict[str, list[str]]]:
    """Load register-structured vocabulary from the metadata database.

    Returns ``{scope: {register: [terms]}}`` with deterministic ordering and
    empty mapping fallback on missing DB files or read failures.
    """
    resolved = db_path or _DEFAULT_METADATA_DB_PATH
    if not resolved.exists():
        return {}
    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        ) as conn:
            rows = conn.execute(
                "SELECT scope, register, term FROM scope_vocabulary "
                "ORDER BY scope ASC, register ASC, term ASC"
            ).fetchall()
    except sqlite3.Error as e:
        logger.warning("Failed to load scope vocabulary from DB %s: %s", resolved, e)
        if event_bus is not None:
            event_bus.publish_async_nowait(
                rag_scope_vocabulary_load_failed(path=str(resolved), error=str(e))
            )
        return {}
    result: defaultdict[str, defaultdict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for scope, register, term in rows:
        if (
            isinstance(scope, str)
            and isinstance(register, str)
            and isinstance(term, str)
            and term.strip()
        ):
            result[scope][register].append(term.strip())
    return {scope: dict(registers) for scope, registers in result.items()}


def format_register_hints(
    vocabulary: dict[str, dict[str, list[str]]],
    scopes: list[str] | None = None,
) -> str:
    """Format register-structured vocabulary for prompt injection.

    Output example:
      [knowledge_systems] practitioner: Obsidian, Zettelkasten, PKM | academic: PKG, entity-centric | specification: RDF, OWL
      [graph_modeling] practitioner: Neo4j, Cypher | academic: property graph, RDF | specification: SPARQL, Gremlin

    If scopes is None, format all scopes. Skips scopes with no terms.
    """
    if not vocabulary:
        return ""
    target = (
        vocabulary
        if not scopes
        else {s: vocabulary[s] for s in scopes if s in vocabulary}
    )
    if not target:
        return ""
    lines: list[str] = []
    for scope, registers in sorted(target.items()):
        parts: list[str] = []
        for reg, terms in sorted(registers.items()):
            if terms:
                parts.append(f"{reg}: {', '.join(terms)}")
        if parts:
            lines.append(f"[{scope}] {' | '.join(parts)}")
    return "\n".join(lines)


def get_hints_for_scopes(
    hints: dict[str, str],
    scopes: list[str] | None = None,
) -> str:
    """Format hints for the given scope(s) as a single comma-separated line.

    If scopes is None or empty, or equals ["both"], concatenate all scope
    hints (broad default for suggest_terms before scope is known). Otherwise
    concatenate only the requested scopes' hints. Skips missing scopes.
    """
    if not hints:
        return ""
    all_hints = ", ".join(v for v in hints.values() if v)
    if not scopes or scopes == ["both"]:
        return all_hints
    parts = [hints[scope] for scope in scopes if scope in hints and hints[scope]]
    return ", ".join(parts) if parts else all_hints


def _build_word_boundary_conditions(
    query_terms: list[str],
) -> tuple[list[str], list[str]]:
    """Build word-boundary SQL conditions for query terms.

    Returns (conditions, params) where each condition matches a query term
    against keys prefixed with 'prop.name@@' or 'prop.topic@@' using exact
    match or word-boundary LIKE patterns (e.g. key = ?, key LIKE ? %).
    Terms are matched as whole words within the key, not substrings.
    """
    conditions: list[str] = []
    params: list[str] = []
    for term in query_terms:
        norm = term.lower().strip()
        if not norm:
            continue
        for prefix in ("prop.name@@", "prop.topic@@"):
            conditions.append("key = ?")
            params.append(f"{prefix}{norm}")
            conditions.append("key LIKE ?")
            params.append(f"{prefix}{norm} %")
            conditions.append("key LIKE ?")
            params.append(f"{prefix}% {norm} %")
            conditions.append("key LIKE ?")
            params.append(f"{prefix}% {norm}")
    return conditions, params


def filter_hints_by_cooccurrence(
    query_terms: list[str],
    hint_terms: list[str],
    db_path: Path | None = None,
    *,
    min_chunk_cooccurrence: int = 2,
    event_bus: EventBus | None = None,
) -> list[str]:
    """Return hint terms that co-occur with query terms at chunk level.

    Uses chunk-weighted scoring: counts how many chunks contain both a
    query-term key and a hint-term key for the same (source, chunk_id).
    Hints with overlap_count >= min_chunk_cooccurrence survive.

    Fallback chain: chunk-weighted → doc-level (N=1) → empty list.
    Results sorted by overlap_count descending (highest co-occurrence first).
    """
    if not query_terms or not hint_terms:
        return []

    if db_path is None:
        db_path = _DEFAULT_METADATA_DB_PATH
    if not db_path.exists():
        logger.debug("Property index DB not found at %s", db_path)
        return []

    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        ) as conn:
            wb_conditions, wb_params = _build_word_boundary_conditions(query_terms)
            if not wb_conditions:
                return []

            hint_keys: list[str] = []
            for hint in hint_terms:
                norm = hint.lower().strip()
                if norm:
                    hint_keys.append(f"prop.name@@{norm}")
                    hint_keys.append(f"prop.topic@@{norm}")
            if not hint_keys:
                return []

            where_clause = " OR ".join(wb_conditions)
            try:
                _ = conn.execute("DROP TABLE IF EXISTS query_chunks")
                _ = conn.execute(
                    "CREATE TEMP TABLE query_chunks AS"
                    " SELECT DISTINCT source, chunk_id FROM properties"
                    f" WHERE source != '' AND ({where_clause})",
                    wb_params,
                )

                _ = conn.execute("DROP TABLE IF EXISTS hint_chunks")
                key_ph = ",".join("?" for _ in hint_keys)
                _ = conn.execute(
                    "CREATE TEMP TABLE hint_chunks AS"
                    " SELECT DISTINCT key, source, chunk_id FROM properties"
                    f" WHERE key IN ({key_ph})",
                    hint_keys,
                )

                key_overlaps = _chunk_level_overlap(conn, min_chunk_cooccurrence)

                if not key_overlaps:
                    key_overlaps = _doc_level_overlap(conn)

                if not key_overlaps:
                    return []

                return _order_hints_by_overlap(hint_terms, key_overlaps)
            finally:
                _ = conn.execute("DROP TABLE IF EXISTS query_chunks")
                _ = conn.execute("DROP TABLE IF EXISTS hint_chunks")
    except sqlite3.OperationalError as exc:
        logger.debug("Cannot open property index DB read-only: %s", db_path)
        if event_bus is not None:
            event_bus.publish_async_nowait(
                rag_corpus_hints_filter_failed(error=str(exc))
            )
        return []
    except Exception as exc:
        logger.debug("Co-occurrence query failed", exc_info=True)
        if event_bus is not None:
            event_bus.publish_async_nowait(
                rag_corpus_hints_filter_failed(error=str(exc))
            )
        return []


def _chunk_level_overlap(
    conn: sqlite3.Connection, min_threshold: int
) -> dict[str, int]:
    """Join query_chunks ⋈ hint_chunks on (source, chunk_id); return key→count."""
    rows = conn.execute(
        "SELECT h.key, COUNT(DISTINCT h.chunk_id) AS overlap_count"
        " FROM hint_chunks h"
        " INNER JOIN query_chunks q ON h.source = q.source AND h.chunk_id = q.chunk_id"
        " GROUP BY h.key"
        " HAVING overlap_count >= ?",
        (min_threshold,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _doc_level_overlap(conn: sqlite3.Connection) -> dict[str, int]:
    """Fallback: join on source only (document-level), threshold=1."""
    rows = conn.execute(
        "SELECT h.key, COUNT(DISTINCT h.source) AS doc_overlap"
        " FROM hint_chunks h"
        " INNER JOIN query_chunks q ON h.source = q.source"
        " GROUP BY h.key"
        " HAVING doc_overlap >= 1",
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _order_hints_by_overlap(
    hint_terms: list[str], key_overlaps: dict[str, int]
) -> list[str]:
    """Map matched keys back to original hint terms, sorted by overlap desc."""
    term_scores: dict[str, int] = {}
    for key, count in key_overlaps.items():
        for prefix in ("prop.name@@", "prop.topic@@"):
            if key.startswith(prefix):
                term = key[len(prefix) :]
                term_scores[term] = max(term_scores.get(term, 0), count)

    normalized_to_original: dict[str, str] = {}
    for hint in hint_terms:
        norm = hint.lower().strip()
        if norm:
            normalized_to_original.setdefault(norm, hint)

    scored: list[tuple[str, int]] = []
    for norm_term, score in term_scores.items():
        if norm_term in normalized_to_original:
            scored.append((normalized_to_original[norm_term], score))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return [h for h, _ in scored]


async def update_corpus_hints(
    property_index: PropertyIndex,
    *,
    scope: str | None = None,
    names_budget: int = 10,
    topics_budget: int = 8,
    min_chunks_name: int = _DEFAULT_MIN_CHUNKS_NAME,
    min_chunks_topic: int = _DEFAULT_MIN_CHUNKS_TOPIC,
    max_chunks_name: int = _DEFAULT_MAX_CHUNKS_NAME,
    max_chunks_topic: int = _DEFAULT_MAX_CHUNKS_TOPIC,
    min_docs: int = 2,
    entity_boost_hyphen: float = 1.3,
    entity_boost_single: float = 1.2,
    extra_blocklist: frozenset[str] = frozenset(),
    blocklist_override: frozenset[str] | None = None,
    key_prefixes: list[str] | None = None,
    event_bus: EventBus | None = None,
) -> dict[str, str]:
    """Persist discriminative scope hints to metadata SQLite tables.

    The function computes per-scope winners for configured key prefixes, writes
    normalized rows into ``corpus_hints``, and returns ``scope -> CSV terms`` for
    prompt-oriented call sites that need an in-memory representation.

    When *scope* is set, only that scope is refreshed and only its rows are
    replaced in the database — other scopes' hints remain untouched.

    *entity_boost_hyphen* / *entity_boost_single* control shape-based score
    multipliers (set both to 1.0 to disable).

    *blocklist_override* replaces the default generic blocklist when set.
    *extra_blocklist* adds terms to the active blocklist.
    """
    prefixes = key_prefixes if key_prefixes is not None else _DEFAULT_KEY_PREFIXES
    if property_index.get_total_chunks() == 0:
        logger.warning("PropertyIndex has 0 chunks — skipping corpus hints update")
        if event_bus is not None:
            await event_bus.publish_async_nowait(
                rag_corpus_hints_skipped(reason="property index has zero chunks")
            )
        return {}

    active_blocklist = (
        blocklist_override if blocklist_override is not None else _GENERIC_BLOCKLIST
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
    for prefix in prefixes:
        for (
            scope_name,
            term,
            chunk_count,
            doc_count,
        ) in property_index.get_term_counts_by_scope(prefix):
            if term:
                if scope is not None and scope_name != scope:
                    continue
                scope_prefix_terms[scope_name][prefix].append(
                    (term, chunk_count, doc_count)
                )

    rows_for_db: list[tuple[str, str, float, str]] = []
    result: dict[str, str] = {}
    for scope_name, prefix_terms in scope_prefix_terms.items():
        winners: list[tuple[str, float, str]] = []
        for prefix, term_counts in prefix_terms.items():
            min_c, max_c = band_limits.get(prefix, (min_chunks_name, max_chunks_name))
            budget = budgets.get(prefix, names_budget)
            scored: list[tuple[str, float, str]] = []
            for term, chunk_count, doc_count in term_counts:
                if chunk_count < min_c or chunk_count > max_c:
                    continue
                if doc_count > 0 and doc_count < min_docs:
                    continue
                if term.lower() in active_blocklist:
                    continue
                base_score = _score_term(chunk_count, doc_count, total_docs)
                boost = _entity_shape_boost(
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
    else:
        await property_index.replace_corpus_hints_rows(rows_for_db)

    if event_bus is not None:
        update_timestamp = datetime.now(UTC).isoformat()
        await event_bus.publish_async_nowait(
            rag_corpus_hints_updated(
                path=str(property_index.db_path),
                scopes_updated=sorted(result),
                timestamp=update_timestamp,
            )
        )
    return result


def _build_chunk_source_map() -> dict[str, str]:
    """Build chunk_id→source mapping from Chroma metadata.

    Loads all chunk IDs and metadata entries from the Chroma ``knowledge``
    collection, extracts non-empty ``source`` values, and returns a mapping
    used by the property-index backfill path.
    """
    import chromadb

    store_path = Path.home() / ".rag" / "store"
    client = chromadb.PersistentClient(path=str(store_path))
    collection = client.get_collection("knowledge")
    all_data = collection.get(include=["metadatas"])
    ids: list[str] = all_data.get("ids") or []
    metas: list[dict[str, object]] = all_data.get("metadatas") or []

    chunk_to_source: dict[str, str] = {}
    for cid, meta in zip(ids, metas, strict=True):
        src = meta.get("source") if isinstance(meta, dict) else None
        if isinstance(src, str) and src:
            chunk_to_source[cid] = src

    print(f"ChromaDB chunks with source metadata: {len(chunk_to_source)}/{len(ids)}")
    return chunk_to_source


def _cli_generate_hints() -> None:
    """Run one-shot corpus-hints generation from the local property index.

    Supports ``--backfill`` to populate missing source values in the property
    index from Chroma metadata before generating hints, and ``--scope NAME``
    to refresh a single scope with optional tuning via ``--no-entity-boost``
    and ``--no-blocklist``.
    """
    import asyncio
    import sys

    args = sys.argv[1:]
    do_backfill = "--backfill" in args

    cli_scope: str | None = None
    if "--scope" in args:
        idx_pos = args.index("--scope")
        if idx_pos + 1 < len(args):
            cli_scope = args[idx_pos + 1]

    no_entity_boost = "--no-entity-boost" in args
    no_blocklist = "--no-blocklist" in args

    chunk_to_source: dict[str, str] | None = None
    if do_backfill:
        chunk_to_source = _build_chunk_source_map()

    async def _run() -> dict[str, str]:
        idx = PropertyIndex()
        await idx.start()
        try:
            if chunk_to_source is not None:
                updated = await idx.backfill_source(chunk_to_source)
                print(f"Property rows backfilled: {updated}\n")

            total_chunks = idx.get_total_chunks()
            total_docs = idx.get_total_docs()
            print(f"Total distinct chunks: {total_chunks}")
            print(f"Total distinct docs (source files): {total_docs}")

            for prefix in _DEFAULT_KEY_PREFIXES:
                all_terms = idx.get_term_counts_by_scope(prefix)
                if prefix == "prop.topic@@":
                    min_c, max_c = _DEFAULT_MIN_CHUNKS_TOPIC, _DEFAULT_MAX_CHUNKS_TOPIC
                else:
                    min_c, max_c = _DEFAULT_MIN_CHUNKS_NAME, _DEFAULT_MAX_CHUNKS_NAME
                in_band = 0
                blocked = 0
                out = 0
                has_doc_count = 0
                for _scope, term, chunk_count, doc_count in all_terms:
                    if chunk_count < min_c or chunk_count > max_c:
                        out += 1
                    elif term.lower() in _GENERIC_BLOCKLIST:
                        blocked += 1
                    else:
                        in_band += 1
                    if doc_count > 0:
                        has_doc_count += 1
                print(
                    f"  {prefix}: {in_band} candidates, {blocked} blocklisted,"
                    f" {out} out-of-band, {has_doc_count}/{len(all_terms)} with doc freq"
                )

            tuning_kwargs: dict[str, object] = {}
            if cli_scope is not None:
                tuning_kwargs["scope"] = cli_scope
            if no_entity_boost:
                tuning_kwargs["entity_boost_hyphen"] = 1.0
                tuning_kwargs["entity_boost_single"] = 1.0
            if no_blocklist:
                tuning_kwargs["blocklist_override"] = frozenset()

            result = await update_corpus_hints(idx, **tuning_kwargs)  # type: ignore[arg-type]
            if result:
                await idx.stamp_watermark("corpus_hints")
            return result
        finally:
            await idx.stop()

    result = asyncio.run(_run())
    if not result:
        print("No hints generated (empty property index?)", file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerated hints for {len(result)} scope(s):")
    for scope_name, terms in sorted(result.items()):
        term_list = [t.strip() for t in terms.split(",") if t.strip()]
        print(f"  {scope_name}: {len(term_list)} terms")

    print(f"\nWritten DB rows to: {Path.home() / '.rag' / 'store' / 'rag_metadata.db'}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    _cli_generate_hints()
