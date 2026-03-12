"""Load and format corpus hints for RAG suggest_terms prompt injection.

Hints are scope → comma-separated vocabulary strings, read from a YAML file.
Used at query time to give the suggest_terms step current corpus vocabulary
without hardcoding domain terms. update_corpus_hints() populates the file
from the property index using discriminative IDF scoring.
"""

from __future__ import annotations

import fcntl
import logging
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml
from universal_event_bus import EventBus

from services.rag.events import rag_corpus_hints_updated
from services.rag.property_index import PropertyIndex

logger = logging.getLogger(__name__)

_DEFAULT_KEY_PREFIXES = ["prop.name@@", "prop.topic@@"]

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


def load_corpus_hints(path: Path) -> dict[str, str]:
    """Read corpus hints from a YAML file.

    Expected top-level key: corpus_hints. Each value is scope name → string
    (comma-separated terms). Returns {} if path is missing or invalid.
    """
    if not path or not path.exists():
        return {}
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        hints_obj = raw.get("corpus_hints")
        if not isinstance(hints_obj, dict):
            return {}
        result: dict[str, str] = {}
        for k, v in hints_obj.items():
            if isinstance(k, str) and isinstance(v, str):
                result[k] = v.strip()
            elif isinstance(k, str) and isinstance(v, list):
                result[k] = ", ".join(
                    str(x).strip() for x in v if isinstance(x, str) and x
                )
        return result
    except (yaml.YAMLError, OSError) as e:
        logger.warning("Failed to load corpus hints from %s: %s", path, e)
        return {}
    except Exception as e:
        logger.error(
            "Unexpected error loading corpus hints from %s: %s", path, e, exc_info=True
        )
        return {}


_DEFAULT_VOCABULARY_PATH = Path.home() / ".rag" / "scope_vocabulary.yaml"


def load_scope_vocabulary(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """Load register-structured vocabulary from scope_vocabulary.yaml.

    Expected format:
        scope_vocabulary:
          knowledge_systems:
            practitioner: ["Obsidian", "Zettelkasten", ...]
            academic: ["PKG", "personal knowledge graph", ...]
            specification: ["RDF", "OWL", ...]

    Returns {scope: {register: [terms]}} or {} if missing/invalid.
    """
    if path is None:
        path = _DEFAULT_VOCABULARY_PATH
    if not path or not path.exists():
        return {}
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        vocab = raw.get("scope_vocabulary")
        if not isinstance(vocab, dict):
            return {}
        result: dict[str, dict[str, list[str]]] = {}
        for scope, registers in vocab.items():
            if not isinstance(scope, str) or not isinstance(registers, dict):
                continue
            scope_regs: dict[str, list[str]] = {}
            for reg, terms in registers.items():
                if isinstance(reg, str) and isinstance(terms, list):
                    scope_regs[reg] = [
                        str(t) for t in terms if isinstance(t, str) and t.strip()
                    ]
            if scope_regs:
                result[scope] = scope_regs
        return result
    except (yaml.YAMLError, OSError) as e:
        logger.warning("Failed to load scope vocabulary from %s: %s", path, e)
        return {}


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
    if not scopes or scopes == ["both"]:
        return ", ".join(v for v in hints.values() if v)
    parts = [hints[scope] for scope in scopes if scope in hints and hints[scope]]
    return ", ".join(parts) if parts else ", ".join(v for v in hints.values() if v)


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
        db_path = Path.home() / ".rag" / "store" / "property_index.db"
    if not db_path.exists():
        logger.debug("Property index DB not found at %s", db_path)
        return []

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
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
                _ = conn.execute(
                    "CREATE TEMP TABLE query_chunks AS"
                    " SELECT DISTINCT source, chunk_id FROM properties"
                    f" WHERE source != '' AND ({where_clause})",
                    wb_params,
                )

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
    except sqlite3.OperationalError:
        logger.debug("Cannot open property index DB read-only: %s", db_path)
        return []
    except Exception:
        logger.debug("Co-occurrence query failed", exc_info=True)
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
        if norm and norm not in normalized_to_original:
            normalized_to_original[norm] = hint

    scored: list[tuple[str, int]] = []
    for norm_term, score in term_scores.items():
        if norm_term in normalized_to_original:
            scored.append((normalized_to_original[norm_term], score))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return [h for h, _ in scored]


async def update_corpus_hints(
    property_index: PropertyIndex,
    hints_path: Path,
    *,
    names_budget: int = 10,
    topics_budget: int = 8,
    min_chunks_name: int = 2,
    min_chunks_topic: int = 3,
    max_chunks_name: int = 50,
    max_chunks_topic: int = 30,
    min_docs: int = 2,
    key_prefixes: list[str] | None = None,
    event_bus: EventBus | None = None,
) -> dict[str, str]:
    """Select discriminative terms per scope from the property index into corpus_hints.yaml.

    Scores terms with hybrid IDF + chunk-boost, applies band limits per prefix
    type, filters a generic-terms blocklist, requires minimum document spread
    (min_docs), and selects per-type budgets. Returns the generated hints dict.
    """
    prefixes = key_prefixes if key_prefixes is not None else _DEFAULT_KEY_PREFIXES
    if property_index.get_total_chunks() == 0:
        logger.warning("PropertyIndex has 0 chunks — skipping corpus hints update")
        return {}

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
            scope,
            term,
            chunk_count,
            doc_count,
        ) in property_index.get_term_counts_by_scope(prefix):
            if term:
                scope_prefix_terms[scope][prefix].append((term, chunk_count, doc_count))

    result: dict[str, str] = {}
    for scope, prefix_terms in scope_prefix_terms.items():
        winners: list[tuple[str, float]] = []
        for prefix, term_counts in prefix_terms.items():
            min_c, max_c = band_limits.get(prefix, (min_chunks_name, max_chunks_name))
            budget = budgets.get(prefix, names_budget)
            scored: list[tuple[str, float]] = []
            for term, chunk_count, doc_count in term_counts:
                if chunk_count < min_c or chunk_count > max_c:
                    continue
                if doc_count > 0 and doc_count < min_docs:
                    continue
                if term.lower() in _GENERIC_BLOCKLIST:
                    continue
                scored.append((term, _score_term(chunk_count, doc_count, total_docs)))
            scored.sort(key=lambda x: (-x[1], x[0]))
            winners.extend(scored[:budget])

        seen: set[str] = set()
        deduped: list[tuple[str, float]] = []
        for term, score in sorted(winners, key=lambda x: (-x[1], x[0])):
            key = term.lower()
            if key not in seen:
                seen.add(key)
                deduped.append((term, score))
        result[scope] = ", ".join(t for t, _ in deduped if t)

    _write_hints_file(hints_path, result)
    if event_bus is not None:
        await event_bus.publish_async_nowait(
            rag_corpus_hints_updated(
                path=str(hints_path),
                scopes_updated=sorted(result),
                timestamp=datetime.now(UTC).isoformat(),
            )
        )
    return result


def _write_hints_file(hints_path: Path, hints: dict[str, str]) -> None:
    """Atomically write corpus_hints.yaml with file locking."""
    hints_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"corpus_hints": hints}
    with open(hints_path, "w", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yaml.safe_dump(payload, f, default_flow_style=False, allow_unicode=True)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


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
    index from Chroma metadata before generating hints.
    """
    import asyncio
    import sys

    do_backfill = "--backfill" in sys.argv
    hints_path = Path.home() / ".rag" / "corpus_hints.yaml"

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

            # Same defaults as update_corpus_hints for consistent diagnostic output.
            min_chunks_name, max_chunks_name = 2, 50
            min_chunks_topic, max_chunks_topic = 3, 30
            band_limits: dict[str, tuple[int, int]] = {
                "prop.name@@": (min_chunks_name, max_chunks_name),
                "prop.topic@@": (min_chunks_topic, max_chunks_topic),
            }
            for prefix in _DEFAULT_KEY_PREFIXES:
                all_terms = idx.get_term_counts_by_scope(prefix)
                min_c, max_c = band_limits.get(prefix, (2, 50))
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

            return await update_corpus_hints(idx, hints_path)
        finally:
            await idx.stop()

    result = asyncio.run(_run())
    if not result:
        print("No hints generated (empty property index?)", file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerated hints for {len(result)} scope(s):")
    for scope, terms in sorted(result.items()):
        term_list = [t.strip() for t in terms.split(",") if t.strip()]
        print(f"  {scope}: {len(term_list)} terms")

    print(f"\nWritten to: {hints_path}")
    print("\n--- YAML output ---")
    yaml.safe_dump(
        {"corpus_hints": result},
        sys.stdout,
        default_flow_style=False,
        allow_unicode=True,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    _cli_generate_hints()
