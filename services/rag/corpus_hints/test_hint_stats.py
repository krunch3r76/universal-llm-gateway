"""Corpus-hint stats stay off the RAG event loop and match prefix SQL.

The live failure is a ~60s synchronous scan inside ``update_corpus_hints``
that prevents ``GET /scopes`` from being served. These tests lock the two
contracts of the fix: a slow stats read must yield the event loop, and
source-prefix aggregation must match the previous per-scope ``LIKE`` counts.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from services.rag.corpus_hints.stats_read import read_corpus_hint_stats
from services.rag.corpus_hints.update import update_corpus_hints
from services.rag.property_index import PropertyIndex

_NAME = "prop.name@@"
_TOPIC = "prop.topic@@"


def _create_properties(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE properties ("
        " key TEXT NOT NULL,"
        " chunk_id TEXT NOT NULL,"
        " scope TEXT NOT NULL DEFAULT 'all',"
        " source TEXT NOT NULL DEFAULT '',"
        " PRIMARY KEY (key, chunk_id))"
    )
    conn.executemany(
        "INSERT INTO properties (key, chunk_id, scope, source) VALUES (?, ?, ?, ?)",
        [
            (_NAME + "retrieval", "c1", "leaf", "/data/alpha/a.md"),
            (_NAME + "retrieval", "c2", "leaf", "/data/alpha/a.md"),
            (_NAME + "retrieval", "c3", "leaf", "/data/alpha/b.md"),
            (_NAME + "retrieval", "c10", "leaf", "/Data/Alpha/C.md"),
            (_TOPIC + "routing", "t1", "leaf", "/data/beta/c.md"),
            (_TOPIC + "routing", "t2", "leaf", "/data/beta/c.md"),
            (_TOPIC + "routing", "t3", "leaf", "/data/beta/c.md"),
            ("prop.other@@x", "e1", "leaf", "/data/alpha/empty.md"),
            (_NAME + "retrieval", "c9", "other", "/other/d.md"),
            (_NAME + "wid", "w1", "leaf", "/data/alpha/wild.md"),
        ],
    )
    conn.commit()
    conn.close()


def _pairs(
    stats_terms: dict[str, list[tuple[str, int, int]]],
    prefix: str,
) -> set[tuple[str, int, int]]:
    return set(stats_terms.get(prefix, []))


def test_source_prefix_stats_match_per_scope_like_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "rag_metadata.db"
    _create_properties(db_path)
    scopes = {
        "alpha": ["/data/alpha"],
        "umbrella": ["/data/alpha", "/data/beta"],
        "wild": ["/data/al_ha"],
        "empty": [],
    }
    stats = read_corpus_hint_stats(
        db_path,
        configured_scopes=scopes,
        key_prefixes=[_NAME, _TOPIC],
        only_scope=None,
    )

    assert stats.total_chunks == 10
    assert stats.scope_doc_counts["alpha"] == 5
    assert stats.scope_doc_counts["umbrella"] == 6
    assert stats.scope_doc_counts["empty"] == 0
    assert _pairs(stats.scope_prefix_terms["alpha"], _NAME) == {
        ("retrieval", 4, 3),
        ("wid", 1, 1),
    }
    assert _pairs(stats.scope_prefix_terms["alpha"], _TOPIC) == set()
    assert _pairs(stats.scope_prefix_terms["umbrella"], _NAME) == {
        ("retrieval", 4, 3),
        ("wid", 1, 1),
    }
    assert _pairs(stats.scope_prefix_terms["umbrella"], _TOPIC) == {
        ("routing", 3, 1),
    }
    assert ("retrieval", 4, 3) in _pairs(stats.scope_prefix_terms["wild"], _NAME)
    assert "/other/d.md" not in {
        term for term, _, _ in stats.scope_prefix_terms["alpha"][_NAME]
    }


def test_only_scope_skips_other_configured_scopes(tmp_path: Path) -> None:
    db_path = tmp_path / "rag_metadata.db"
    _create_properties(db_path)
    stats = read_corpus_hint_stats(
        db_path,
        configured_scopes={"alpha": ["/data/alpha"], "beta": ["/data/beta"]},
        key_prefixes=[_NAME, _TOPIC],
        only_scope="beta",
    )
    assert set(stats.scope_prefix_terms) == {"beta"}
    assert set(stats.scope_doc_counts) == {"beta"}
    assert stats.scope_doc_counts["beta"] == 1


def test_scope_column_grouping_ignores_path_prefixes(tmp_path: Path) -> None:
    db_path = tmp_path / "rag_metadata.db"
    _create_properties(db_path)
    stats = read_corpus_hint_stats(
        db_path,
        configured_scopes=None,
        key_prefixes=[_NAME],
        only_scope=None,
    )
    assert stats.scope_doc_counts == {}
    leaf = _pairs(stats.scope_prefix_terms["leaf"], _NAME)
    assert ("retrieval", 4, 3) in leaf
    assert ("retrieval", 1, 1) in _pairs(stats.scope_prefix_terms["other"], _NAME)


def test_empty_property_index_skips_scans(tmp_path: Path) -> None:
    db_path = tmp_path / "rag_metadata.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE properties ("
        " key TEXT NOT NULL, chunk_id TEXT NOT NULL,"
        " scope TEXT NOT NULL DEFAULT 'all', source TEXT NOT NULL DEFAULT '')"
    )
    conn.commit()
    conn.close()
    stats = read_corpus_hint_stats(
        db_path,
        configured_scopes={"alpha": ["/data/alpha"]},
        key_prefixes=[_NAME],
        only_scope=None,
    )
    assert stats.total_chunks == 0
    assert stats.scope_prefix_terms == {}


class _FakeIndex:
    db_path = Path("/unused")

    def __init__(self) -> None:
        self.replaced: list[tuple[str, list]] = []

    async def replace_corpus_hints_for_scope(
        self, scope: str, rows: list[tuple[str, str, float, str]]
    ) -> None:
        self.replaced.append((scope, rows))


@pytest.mark.asyncio
async def test_update_corpus_hints_reads_off_the_event_loop(monkeypatch) -> None:
    """A multi-hundred-millisecond stats read must not stall other tasks.

    Before the fix the same sleep ran on the event loop inside
    ``update_corpus_hints`` and this assertion failed: the marker could not
    finish until the scan returned.
    """

    def slow_read(*_args, **_kwargs):
        time.sleep(0.4)
        from services.rag.corpus_hints.stats_read import CorpusHintStats

        return CorpusHintStats(total_chunks=0, total_docs=0)

    monkeypatch.setattr(
        "services.rag.corpus_hints.update.read_corpus_hint_stats",
        slow_read,
    )
    finished_at: list[float] = []

    async def mark() -> None:
        await asyncio.sleep(0.05)
        finished_at.append(time.monotonic())

    started = time.monotonic()
    marker = asyncio.create_task(mark())
    await update_corpus_hints(
        _FakeIndex(),
        configured_scopes={"alpha": ["/data/alpha"]},
    )
    await marker
    assert finished_at
    assert finished_at[0] - started < 0.2


@pytest.mark.asyncio
async def test_update_writes_scored_hints_for_configured_scope(
    tmp_path: Path,
) -> None:
    idx = PropertyIndex(db_path=tmp_path / "rag_metadata.db")
    await idx.start()
    try:
        conn = idx._ensure_conn()
        conn.executemany(
            "INSERT INTO properties (key, chunk_id, scope, source)"
            " VALUES (?, ?, ?, ?)",
            [
                (_NAME + "retrieval", "c1", "alpha", "/data/alpha/a.md"),
                (_NAME + "retrieval", "c2", "alpha", "/data/alpha/b.md"),
            ],
        )
        conn.commit()
        result = await update_corpus_hints(
            idx,
            configured_scopes={"alpha": ["/data/alpha"]},
            min_chunks_name=1,
            min_chunks_topic=1,
            min_docs=1,
            names_budget=5,
            topics_budget=5,
        )
        stored = conn.execute(
            "SELECT term, prefix FROM corpus_hints WHERE scope = ?",
            ("alpha",),
        ).fetchall()
    finally:
        await idx.stop()

    assert "retrieval" in result["alpha"]
    assert ("retrieval", _NAME) in stored
