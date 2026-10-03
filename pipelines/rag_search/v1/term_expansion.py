"""Query factoring + IDF-weighted corpus expansion utilities.

Pure functions for phrase extraction and IDF expansion. No handler or
pipeline dependencies — usable from both the ExpandTermsHandler (step-based)
and inline computation within the retrieve handler (concurrent path).

Two complementary mechanisms, zero LLM calls:

1. **Query factoring**: Extract sub-phrases and feed each as an independent
   pool B sparse-only BM25 query. Prevents lane crowding.

2. **IDF-weighted corpus expansion**: Query the property index for terms that
   co-occur with the query's most discriminative words. Surfaces vocabulary
   the user didn't use but the corpus contains.
"""

from __future__ import annotations

import contextlib
import math
import re
import sqlite3
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

DEFAULT_METADATA_DB = Path.home() / ".rag/store/rag_metadata.db"

STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "not",
        "no",
        "nor",
        "so",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "my",
        "your",
        "his",
        "her",
        "our",
        "their",
        "we",
        "they",
        "you",
        "he",
        "she",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "very",
        "just",
        "about",
        "between",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "up",
        "down",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "once",
        "here",
        "there",
        "also",
        "into",
        "does",
        "using",
        "used",
        "use",
        "based",
        "compare",
        "exist",
        "exists",
        "handle",
        "handles",
        "approach",
        "approaches",
        "technique",
        "techniques",
        "method",
        "methods",
        "way",
        "ways",
        "work",
        "works",
        "tracking",
    }
)

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*(?:'[a-z]+)?")

SPLIT_RE = re.compile(
    r"\b(?:compare|versus|vs\.?|with|and|between|or)\b",
    re.IGNORECASE,
)


def extract_content_words(text: str, *, min_len: int = 3) -> list[str]:
    """Extract unique content words from text, filtering stopwords."""
    tokens = WORD_RE.findall(text)
    seen: set[str] = set()
    result: list[str] = []
    for t in tokens:
        low = t.lower()
        if len(low) < min_len or low in STOPWORDS or low in seen:
            continue
        seen.add(low)
        result.append(t)
    return result


def extract_phrases(text: str, *, max_phrases: int = 6) -> list[str]:
    """Extract multi-word content phrases from query text.

    Splits on comparison/conjunction markers, then extracts runs of
    consecutive content words. Single-word runs kept only for proper
    nouns / acronyms.
    """
    segments = SPLIT_RE.split(text)
    phrases: list[str] = []
    seen: set[str] = set()

    for segment in segments:
        tokens = WORD_RE.findall(segment.strip())
        run: list[str] = []
        for t in tokens:
            if t.lower() in STOPWORDS:
                _flush_run(run, phrases, seen)
                run = []
            else:
                run.append(t)
        _flush_run(run, phrases, seen)

    return phrases[:max_phrases]


def _flush_run(run: list[str], phrases: list[str], seen: set[str]) -> None:
    """Flush a content-word run into phrases if it meets criteria."""
    if len(run) >= 2:
        phrase = " ".join(run)
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            phrases.append(phrase)
    elif len(run) == 1 and _is_proper(run[0]):
        key = run[0].lower()
        if key not in seen:
            seen.add(key)
            phrases.append(run[0])


def _is_proper(word: str) -> bool:
    """Heuristic: likely a proper noun, acronym, or technical term."""
    if len(word) <= 2:
        return False
    return (
        word.isupper()
        or (word[0].isupper() and any(c.isupper() for c in word[1:]))
        or "-" in word
        or (word[0].isupper() and len(word) >= 4)
    )


# ── SQL helpers ──────────────────────────────────────────────────────────────


def _wb_conditions(term: str) -> tuple[list[str], list[str]]:
    """Build word-boundary SQL conditions for a single term."""
    norm = term.lower().strip()
    conditions: list[str] = []
    params: list[str] = []
    for prefix in ("prop.name@@", "prop.topic@@"):
        conditions.append("key = ?")
        params.append(f"{prefix}{norm}")
        conditions.append("key LIKE ?")
        params.append(f"{prefix}{norm} %")
        conditions.append("key LIKE ?")
        params.append(f"{prefix}% {norm}")
        conditions.append("key LIKE ?")
        params.append(f"{prefix}% {norm} %")
    return conditions, params


def idf_expand(
    query_words: list[str],
    *,
    max_discriminative: int = 4,
    max_results: int = 8,
    db_path: Path | None = None,
) -> list[str]:
    """IDF-weighted corpus expansion via property index.

    IDF = Inverse Document Frequency: terms that appear in few documents score
    high (discriminative); terms that appear in nearly all documents score low
    (noise). The rarest query words are used as seeds for co-occurrence lookup.

    Single DB connection, two queries:
    1. Batch DF computation for all query words (one temp table + GROUP BY)
    2. Co-occurrence join for top-K discriminative words (one tagged temp table)

    Returns terms sorted by discriminative co-occurrence score.

    Known limitation: expansion is bounded by the property index vocabulary.
    If the query uses a synonym that was never extracted as an entity or topic
    in any chunk (e.g. "neural network" when the corpus only contains "deep
    learning"), the discriminative terms have zero DF and no co-occurrence
    paths exist — expansion returns nothing. Dense retrieval (Pool A) handles
    this case via embedding-space proximity. The practical frequency of this
    gap depends on extraction model quality and corpus vocabulary coverage.
    """
    resolved = db_path or DEFAULT_METADATA_DB
    if not resolved.exists() or not query_words:
        return []

    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
        ) as conn:
            conn.execute("PRAGMA temp_store = MEMORY")

            term_dfs = _batch_compute_dfs(conn, query_words)
            if not term_dfs:
                return []

            ranked = sorted(term_dfs.items(), key=lambda x: x[1])
            disc_terms = [t for t, df in ranked[:max_discriminative] if df > 0]
            if not disc_terms:
                return []

            max_df = max(term_dfs.values())

            cooc = _batch_cooccurrence(conn, disc_terms)

            query_lower = {w.lower() for w in query_words}
            scored: list[tuple[str, float]] = []
            for hint_term, per_qt in cooc.items():
                if hint_term in query_lower:
                    continue
                score = sum(
                    math.log(1 + max_df / max(1, term_dfs.get(qt, max_df))) * cnt
                    for qt, cnt in per_qt.items()
                )
                scored.append((hint_term, score))

            scored.sort(key=lambda x: -x[1])
            return [term for term, _ in scored[:max_results]]

    except sqlite3.OperationalError:
        logger.warning("IDF expansion failed — property DB unavailable")
        return []


def _batch_compute_dfs(conn: sqlite3.Connection, terms: list[str]) -> dict[str, int]:
    """Batch DF computation: one temp table insert, one GROUP BY query."""
    norms = list(dict.fromkeys(t.lower().strip() for t in terms if t.strip()))
    if not norms:
        return {}

    conn.execute("DROP TABLE IF EXISTS df_query_terms")
    try:
        conn.execute("CREATE TEMP TABLE df_query_terms (term TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO df_query_terms VALUES (?)",
            [(n,) for n in norms],
        )

        rows = conn.execute(
            f"""SELECT dqt.term,
                   (SELECT COUNT(DISTINCT p.chunk_id)
                    FROM properties p
                    WHERE p.source != '' AND ({_wb_where_for_term("dqt.term")})) AS df
            FROM df_query_terms dqt""",
        ).fetchall()

        return {row[0]: row[1] for row in rows}
    except sqlite3.OperationalError:
        result: dict[str, int] = {}
        for norm in norms:
            conds, params = _wb_conditions(norm)
            where = " OR ".join(conds)
            row = conn.execute(
                f"SELECT COUNT(DISTINCT chunk_id) FROM properties"
                f" WHERE source != '' AND ({where})",
                params,
            ).fetchone()
            result[norm] = row[0] if row else 0
        return result
    finally:
        conn.execute("DROP TABLE IF EXISTS df_query_terms")


def _wb_where_for_term(term_expr: str) -> str:
    """Build word-boundary WHERE clause referencing a SQL expression."""
    prefixes = ("prop.name@@", "prop.topic@@")
    parts: list[str] = []
    for p in prefixes:
        parts.append(f"p.key = '{p}' || {term_expr}")
        parts.append(f"p.key LIKE '{p}' || {term_expr} || ' %'")
        parts.append(f"p.key LIKE '{p}%' || ' ' || {term_expr}")
        parts.append(f"p.key LIKE '{p}%' || ' ' || {term_expr} || ' %'")
    return " OR ".join(parts)


def _batch_cooccurrence(
    conn: sqlite3.Connection,
    disc_terms: list[str],
) -> dict[str, dict[str, int]]:
    """Single-pass co-occurrence: one tagged temp table, one join."""
    conn.execute("DROP TABLE IF EXISTS disc_chunks")
    try:
        unions: list[str] = []
        all_params: list[str] = []
        for term in disc_terms:
            conds, params = _wb_conditions(term)
            where = " OR ".join(conds)
            unions.append(
                f"SELECT ? AS query_term, source, chunk_id"
                f" FROM properties WHERE source != '' AND ({where})"
            )
            all_params.append(term.lower().strip())
            all_params.extend(params)

        sql = " UNION ALL ".join(unions)
        conn.execute(
            f"CREATE TEMP TABLE disc_chunks AS"
            f" SELECT DISTINCT query_term, source, chunk_id FROM ({sql})",
            all_params,
        )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_disc_src_chunk"
            " ON disc_chunks(source, chunk_id)"
        )

        rows = conn.execute(
            """SELECT p.key, dc.query_term, COUNT(DISTINCT p.chunk_id) AS cnt
            FROM properties p
            INNER JOIN disc_chunks dc
              ON p.source = dc.source AND p.chunk_id = dc.chunk_id
            WHERE p.key LIKE 'prop.name@@%' OR p.key LIKE 'prop.topic@@%'
            GROUP BY p.key, dc.query_term"""
        ).fetchall()

        result: dict[str, dict[str, int]] = {}
        for key, qt, cnt in rows:
            for prefix in ("prop.name@@", "prop.topic@@"):
                if key.startswith(prefix):
                    term = key[len(prefix) :]
                    entry = result.setdefault(term, {})
                    entry[qt] = max(entry.get(qt, 0), cnt)
        return result
    finally:
        conn.execute("DROP TABLE IF EXISTS disc_chunks")
