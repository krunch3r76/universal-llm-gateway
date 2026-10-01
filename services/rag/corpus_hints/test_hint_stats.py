"""Corpus-hint stats stay off the RAG event loop and match prefix SQL.

The live failure is a ~60s synchronous scan inside ``update_corpus_hints``
that prevents ``GET /scopes`` from being served. These tests lock the two
contracts of the fix: a slow stats read must yield the event loop, and
source-prefix aggregation must match the previous per-scope ``LIKE`` counts.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from services.rag.corpus_hints.rebuild_gate import (
    HintRebuildGate,
    HintRebuildRequest,
    reset_hint_rebuild_gates,
    widen,
)
from services.rag.corpus_hints.stats_read import CorpusHintStats, read_corpus_hint_stats
from services.rag.corpus_hints.update import update_corpus_hints
from services.rag.property_index import PropertyIndex


@pytest.fixture(autouse=True)
def _fresh_hint_rebuild_gate() -> None:
    reset_hint_rebuild_gates()


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
            (_NAME + "sibling", "s1", "leaf", "/data/alphabet/z.md"),
            (_NAME + "newline", "n1", "leaf", "/data/alpha\n/nested.md"),
            (_NAME + "pct", "p1", "leaf", "/data/x.md"),
        ],
    )
    conn.commit()
    conn.close()


def _pairs(
    stats_terms: dict[str, list[tuple[str, int, int]]],
    prefix: str,
) -> set[tuple[str, int, int]]:
    return set(stats_terms.get(prefix, []))


def _legacy_like_stats(
    db_path: Path,
    scopes: dict[str, list[str]],
    key_prefixes: list[str],
) -> tuple[int, dict[str, int], dict[str, dict[str, set[tuple[str, int, int]]]]]:
    """Per-scope LIKE counts the old property-index queries returned."""
    conn = sqlite3.connect(db_path)
    total_chunks = int(
        conn.execute("SELECT COUNT(DISTINCT chunk_id) FROM properties").fetchone()[0]
    )
    doc_counts: dict[str, int] = {}
    terms: dict[str, dict[str, set[tuple[str, int, int]]]] = {}
    for scope_name, prefixes in scopes.items():
        if not prefixes:
            doc_counts[scope_name] = 0
            continue
        clause = " OR ".join("source LIKE ?" for _ in prefixes)
        params = tuple(f"{prefix}%" for prefix in prefixes)
        doc_counts[scope_name] = int(
            conn.execute(
                "SELECT COUNT(DISTINCT source) FROM properties"
                f" WHERE source != '' AND ({clause})",
                params,
            ).fetchone()[0]
        )
        terms[scope_name] = {}
        for key_prefix in key_prefixes:
            prefix_len = len(key_prefix) + 1
            rows = conn.execute(
                "SELECT substr(key, ?), COUNT(DISTINCT chunk_id),"
                " COUNT(DISTINCT CASE WHEN source != '' THEN source END)"
                " FROM properties"
                f" WHERE key LIKE ? AND source != '' AND ({clause})"
                " GROUP BY substr(key, ?)",
                (prefix_len, f"{key_prefix}%", *params, prefix_len),
            )
            terms[scope_name][key_prefix] = {
                (str(term), int(chunks), int(docs))
                for term, chunks, docs in rows
                if term
            }
    conn.close()
    return total_chunks, doc_counts, terms


def test_source_prefix_stats_match_per_scope_like_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "rag_metadata.db"
    _create_properties(db_path)
    scopes = {
        "alpha": ["/data/alpha"],
        "alpha_slash": ["/data/alpha/"],
        "umbrella": ["/data/alpha", "/data/beta"],
        "overlap": ["/data/alpha/a.md", "/data/beta"],
        "wild": ["/data/al_ha"],
        "percent": ["/data/%"],
        "empty": [],
    }
    prefixes = [_NAME, _TOPIC]
    stats = read_corpus_hint_stats(
        db_path,
        configured_scopes=scopes,
        key_prefixes=prefixes,
        only_scope=None,
    )
    total_chunks, doc_counts, terms = _legacy_like_stats(db_path, scopes, prefixes)

    assert stats.total_chunks == total_chunks
    assert stats.scope_doc_counts == doc_counts
    for scope_name, prefix_terms in terms.items():
        for prefix, expected in prefix_terms.items():
            assert _pairs(stats.scope_prefix_terms[scope_name], prefix) == expected
    alpha_names = _pairs(stats.scope_prefix_terms["alpha"], _NAME)
    slash_names = _pairs(stats.scope_prefix_terms["alpha_slash"], _NAME)
    assert ("sibling", 1, 1) in alpha_names
    assert ("sibling", 1, 1) not in slash_names
    assert ("newline", 1, 1) in alpha_names
    assert ("newline", 1, 1) not in slash_names
    assert ("routing", 3, 1) in _pairs(stats.scope_prefix_terms["overlap"], _TOPIC)
    percent_names = _pairs(stats.scope_prefix_terms["percent"], _NAME)
    assert ("sibling", 1, 1) in percent_names
    assert ("retrieval", 4, 3) in percent_names


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
            "INSERT INTO properties (key, chunk_id, scope, source) VALUES (?, ?, ?, ?)",
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


def test_only_scope_term_sql_filters_by_source_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "rag_metadata.db"
    _create_properties(db_path)
    seen: list[str] = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(
        "services.rag.corpus_hints.stats_read.sqlite3.connect",
        connect,
    )
    stats = read_corpus_hint_stats(
        db_path,
        configured_scopes={"alpha": ["/data/alpha"], "beta": ["/data/beta"]},
        key_prefixes=[_NAME, _TOPIC],
        only_scope="beta",
    )
    term_sql = [sql for sql in seen if "GROUP BY source" in sql]
    assert term_sql
    assert all("source LIKE" in sql for sql in term_sql)
    assert set(stats.scope_prefix_terms) == {"beta"}


def test_only_scope_column_sql_filters_by_scope(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "rag_metadata.db"
    _create_properties(db_path)
    seen: list[str] = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(
        "services.rag.corpus_hints.stats_read.sqlite3.connect",
        connect,
    )
    read_corpus_hint_stats(
        db_path,
        configured_scopes=None,
        key_prefixes=[_NAME],
        only_scope="leaf",
    )
    grouped = [sql for sql in seen if "GROUP BY scope" in sql]
    assert grouped
    assert all("AND scope =" in sql for sql in grouped)


@pytest.mark.asyncio
async def test_overlapping_rebuilds_share_one_follow_up(
    monkeypatch, tmp_path: Path
) -> None:
    in_flight = 0
    peak = 0
    calls = 0
    only_scopes: list[str | None] = []
    lock = threading.Lock()

    def slow_read(*_args, **kwargs):
        nonlocal in_flight, peak, calls
        with lock:
            calls += 1
            only_scopes.append(kwargs.get("only_scope"))
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            time.sleep(0.35)
            return CorpusHintStats(total_chunks=0, total_docs=0)
        finally:
            with lock:
                in_flight -= 1

    monkeypatch.setattr(
        "services.rag.corpus_hints.update.read_corpus_hint_stats",
        slow_read,
    )
    index = _FakeIndex()
    index.db_path = tmp_path / "hints.db"

    async def rebuild() -> dict[str, str]:
        return await update_corpus_hints(
            index,
            scope="alpha",
            configured_scopes={"alpha": ["/data/alpha"], "beta": ["/data/beta"]},
        )

    leader = asyncio.create_task(rebuild())
    await asyncio.sleep(0.05)
    await asyncio.gather(leader, rebuild(), rebuild())
    assert calls == 2
    assert peak == 1
    assert only_scopes == ["alpha", "alpha"]


@pytest.mark.asyncio
async def test_scoring_runs_off_the_event_loop(monkeypatch, tmp_path: Path) -> None:
    def fast_read(*_args, **_kwargs):
        return CorpusHintStats(
            total_chunks=1,
            total_docs=1,
            scope_prefix_terms={"alpha": {_NAME: [("retrieval", 1, 1)]}},
            scope_doc_counts={"alpha": 1},
        )

    def slow_score(*_args, **_kwargs):
        time.sleep(0.4)
        return 1.0

    monkeypatch.setattr(
        "services.rag.corpus_hints.update.read_corpus_hint_stats",
        fast_read,
    )
    monkeypatch.setattr("services.rag.corpus_hints.update.score_term", slow_score)
    index = _FakeIndex()
    index.db_path = tmp_path / "score.db"
    finished_at: list[float] = []

    async def mark() -> None:
        await asyncio.sleep(0.05)
        finished_at.append(time.monotonic())

    started = time.monotonic()
    marker = asyncio.create_task(mark())
    await update_corpus_hints(
        index,
        configured_scopes={"alpha": ["/data/alpha"]},
        min_chunks_name=1,
        min_docs=1,
    )
    await marker
    assert finished_at
    assert finished_at[0] - started < 0.2


@pytest.mark.asyncio
async def test_freshness_repair_batches_stale_scopes(monkeypatch) -> None:
    from services.rag.config import RagConfig, ScopeDefinition, WatchDirectory
    from services.rag.vocabulary._repair import run_scope_freshness_repair

    calls: list[dict] = []

    async def fake_update(_index, **kwargs) -> dict[str, str]:
        calls.append(kwargs)
        return {}

    monkeypatch.setattr(
        "services.rag.vocabulary._repair.update_corpus_hints",
        fake_update,
    )

    class _Index:
        def has_scope_vocabulary(self, _scope: str) -> bool:
            return True

        async def stamp_watermark(self, _step: str) -> None:
            return None

    config = RagConfig(
        watch_directories=[WatchDirectory(path="/data")],
        scopes={
            "alpha": ScopeDefinition(prefixes=["/data/alpha"], vocab_mode="none"),
            "beta": ScopeDefinition(prefixes=["/data/beta"], vocab_mode="none"),
            "union": ScopeDefinition(
                prefixes=["/data/alpha", "/data/beta"],
                is_union=True,
                vocab_mode="none",
            ),
        },
    )
    await run_scope_freshness_repair(
        property_index=_Index(),
        config=config,
        stale_scopes=["alpha", "beta", "union", "missing"],
        event_bus=None,
        trigger="startup",
    )
    assert len(calls) == 1
    assert "scope" not in calls[0]
    assert set(calls[0]["configured_scopes"]) == {"alpha", "beta"}

    calls.clear()
    await run_scope_freshness_repair(
        property_index=_Index(),
        config=config,
        stale_scopes=["beta", "union"],
        event_bus=None,
        trigger="startup",
    )
    assert len(calls) == 1
    assert calls[0]["scope"] == "beta"


def _rebuild_request(**overrides: object) -> HintRebuildRequest:
    fields: dict[str, object] = {
        "scope": "alpha",
        "configured_scopes": {"alpha": ["/data/alpha"], "beta": ["/data/beta"]},
        "key_prefixes": ["prop.name@@"],
        "names_budget": 15,
        "topics_budget": 12,
        "min_chunks_name": 2,
        "min_chunks_topic": 3,
        "max_chunks_name": 80,
        "max_chunks_topic": 50,
        "min_docs": 2,
        "entity_boost_hyphen": 1.3,
        "entity_boost_single": 1.2,
        "extra_blocklist": frozenset(),
        "blocklist_override": None,
        "event_bus": None,
    }
    fields.update(overrides)
    return HintRebuildRequest(**fields)  # type: ignore[arg-type]


def test_widen_refuses_unequal_scoring_and_scan_mode() -> None:
    later_boost = _rebuild_request(entity_boost_hyphen=9.0)
    with pytest.raises(ValueError):
        widen(_rebuild_request(), later_boost)
    later_block = _rebuild_request(extra_blocklist=frozenset({"custom"}))
    with pytest.raises(ValueError):
        widen(_rebuild_request(), later_block)
    column = _rebuild_request(scope=None, configured_scopes=None)
    with pytest.raises(ValueError):
        widen(_rebuild_request(), column)
    merged = widen(_rebuild_request(scope="alpha"), _rebuild_request(scope="beta"))
    assert merged.scope is None
    assert set(merged.configured_scopes or {}) == {"alpha", "beta"}
    assert merged.entity_boost_hyphen == 1.3


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for rebuild-gate state")
        await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cancelled_leader_does_not_drop_waiter_follow_up() -> None:
    """A cancelled runner used to store CancelledError and re-raise it to waiters.

    ``commit`` only catches ``Exception``, so that error dropped the pending
    union. The waiter must run the union instead of seeing the cancellation.
    """
    entered = asyncio.Event()
    calls: list[HintRebuildRequest] = []

    async def worker(request: HintRebuildRequest) -> dict[str, str]:
        calls.append(request)
        if len(calls) == 1:
            entered.set()
            await asyncio.Event().wait()
        return {name: "hint" for name in (request.configured_scopes or {})}

    gate = HintRebuildGate()
    leader = asyncio.create_task(gate.run(_rebuild_request(scope="alpha"), worker))
    await entered.wait()
    follower = asyncio.create_task(gate.run(_rebuild_request(scope="beta"), worker))
    await _wait_until(lambda: gate._dirty and gate._pending is not None)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    result = await asyncio.wait_for(follower, timeout=2)
    assert set(result) == {"alpha", "beta"}
    assert len(calls) == 2
    assert calls[1].scope is None
    assert set(calls[1].configured_scopes or {}) == {"alpha", "beta"}
    assert gate._error is None


@pytest.mark.asyncio
async def test_unequal_boosts_and_blocklists_run_separately() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[HintRebuildRequest] = []

    async def worker(request: HintRebuildRequest) -> dict[str, str]:
        calls.append(request)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return {"alpha": "hint"}

    gate = HintRebuildGate()
    first = _rebuild_request()
    second = _rebuild_request(
        entity_boost_hyphen=9.0,
        extra_blocklist=frozenset({"custom"}),
    )
    leader = asyncio.create_task(gate.run(first, worker))
    await entered.wait()
    follower = asyncio.create_task(gate.run(second, worker))
    await _wait_until(lambda: bool(gate._cond and gate._cond._waiters))  # type: ignore[attr-defined]
    assert len(calls) == 1
    assert gate._pending is None
    release.set()
    await asyncio.wait_for(asyncio.gather(leader, follower), timeout=2)
    assert [call.entity_boost_hyphen for call in calls] == [1.3, 9.0]
    assert calls[0].extra_blocklist == frozenset()
    assert calls[1].extra_blocklist == frozenset({"custom"})


@pytest.mark.asyncio
async def test_column_and_prefix_scans_are_not_merged() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[HintRebuildRequest] = []

    async def worker(request: HintRebuildRequest) -> dict[str, str]:
        calls.append(request)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return {"alpha": "hint"}

    gate = HintRebuildGate()
    column = _rebuild_request(scope=None, configured_scopes=None)
    prefix = _rebuild_request(scope="alpha")
    leader = asyncio.create_task(gate.run(column, worker))
    await entered.wait()
    follower = asyncio.create_task(gate.run(prefix, worker))
    await _wait_until(lambda: bool(gate._cond and gate._cond._waiters))  # type: ignore[attr-defined]
    assert gate._pending is None
    release.set()
    await asyncio.wait_for(asyncio.gather(leader, follower), timeout=2)
    assert len(calls) == 2
    assert calls[0].configured_scopes is None
    assert calls[0].scope is None
    assert calls[1].configured_scopes is not None
    assert calls[1].scope == "alpha"
