"""Concurrency regression for PropertyIndex shared SQLite connection (friction:39548)."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
import time
from pathlib import Path

import pytest
import pytest_asyncio

from services.rag.property_index import PropertyIndex
from services.rag.property_index.queue_redrive import redrive_exhausted_once

_SOURCE = "/concurrency/test-source.md"
_ITERATIONS = 120


@pytest_asyncio.fixture
async def started_index(tmp_path: Path) -> PropertyIndex:
    index = PropertyIndex(tmp_path / "rag_metadata.db")
    await index.start()
    try:
        yield index
    finally:
        await index.stop()


@pytest.mark.asyncio
async def test_concurrent_threads_read_indexed_source_and_upsert(
    started_index: PropertyIndex,
) -> None:
    """Four readers + one writer on get_indexed_source/upsert must not corrupt SQLite."""
    index = started_index
    barrier = threading.Barrier(5)
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(_ITERATIONS):
                barrier.wait(timeout=30)
                snap = index.get_indexed_source(_SOURCE)
                if snap is not None:
                    _ = snap.source_hash
        except BaseException as exc:
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(_ITERATIONS):
                barrier.wait(timeout=30)

                def _write(conn: object, step: int = i) -> None:
                    conn.execute(
                        "INSERT INTO indexed_sources ("
                        "  source, mtime_ns, size_bytes, extraction_schema_version,"
                        "  extraction_model, source_hash, updated_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, datetime('now'))"
                        " ON CONFLICT(source) DO UPDATE SET"
                        "  mtime_ns = excluded.mtime_ns,"
                        "  size_bytes = excluded.size_bytes,"
                        "  extraction_schema_version = excluded.extraction_schema_version,"
                        "  extraction_model = excluded.extraction_model,"
                        "  source_hash = excluded.source_hash,"
                        "  updated_at = datetime('now')",
                        (
                            _SOURCE,
                            step,
                            step + 1,
                            1,
                            "test-model",
                            f"hash-{step}",
                        ),
                    )
                    conn.commit()

                index.run_sqlite(lambda step=i: _write(index._ensure_conn(), step))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=writer))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors, errors


@pytest.mark.asyncio
async def test_concurrent_event_loop_and_to_thread_reads(
    started_index: PropertyIndex,
) -> None:
    """Mix direct async writes with to_thread(get_indexed_source) like index_file."""
    index = started_index

    async def write_loop() -> None:
        for i in range(_ITERATIONS):
            await index.upsert_indexed_source(
                source=_SOURCE,
                mtime_ns=i,
                size_bytes=100 + i,
                extraction_schema_version=1,
                extraction_model="test-model",
                source_hash=f"loop-{i}",
            )

    async def read_loop() -> None:
        for _ in range(_ITERATIONS):
            snap = await asyncio.to_thread(index.get_indexed_source, _SOURCE)
            if snap is not None:
                _ = snap.source_hash

    await asyncio.gather(write_loop(), read_loop(), read_loop())


def test_property_index_mixins_do_not_call_ensure_conn_directly() -> None:
    """Every mixin except mixin_01 routes sqlite via _with_conn / run_sqlite helpers."""
    root = Path(__file__).resolve().parent / "property_index"
    pattern = re.compile(r"(?<![\w.])self\._ensure_conn\(\)")
    offenders: list[str] = []
    for path in sorted(root.glob("mixin*.py")):
        if path.name in ("mixin_00_sqlite_access.py", "mixin_01.py"):
            continue
        text = path.read_text()
        if pattern.search(text):
            offenders.append(path.name)
    assert offenders == [], f"direct _ensure_conn in: {offenders}"


def test_fts_index_has_no_owned_sqlite_connection_field() -> None:
    fts_path = Path(__file__).resolve().parent / "fts_index.py"
    text = fts_path.read_text()
    assert "self._conn:" not in text
    assert "self._conn =" not in text


@pytest.mark.asyncio
async def test_redrive_write_and_article_query_stay_on_sqlite_thread(
    tmp_path: Path,
) -> None:
    """Admin-style reads and queue redrive writes must not touch _conn off-thread."""
    index = PropertyIndex(tmp_path / "rag_metadata.db")
    await index.start()
    try:
        await index.enqueue_extraction("/queue/test.json")
        article_count = index.run_sqlite(
            lambda: (
                index._ensure_conn()
                .execute("SELECT COUNT(*) FROM articles")
                .fetchone()[0]
            )
        )
        assert article_count == 0
        outcome = await redrive_exhausted_once(index, dry_run=True)
        assert outcome.dry_run is True
    finally:
        await index.stop()


@pytest.mark.asyncio
async def test_direct_ensure_conn_from_main_thread_raises_programming_error(
    tmp_path: Path,
) -> None:
    """Regression guard: bare _ensure_conn().execute on main thread must fail."""
    index = PropertyIndex(tmp_path / "rag_metadata.db")
    await index.start()
    try:
        with pytest.raises(sqlite3.ProgrammingError):
            index._ensure_conn().execute("SELECT 1").fetchone()
    finally:
        await index.stop()


@pytest.mark.asyncio
async def test_rescope_cli_stats_query_uses_run_sqlite(tmp_path: Path) -> None:
    """``__main__`` scope stats pattern: run_sqlite from the main thread."""
    index = PropertyIndex(tmp_path / "rag_metadata.db")
    await index.start()
    try:
        await index.add("prop.name@@x", "c1", scope="docs", source="/a.md")
        rows = index.run_sqlite(
            lambda: (
                index._ensure_conn()
                .execute(
                    "SELECT scope, COUNT(*) FROM properties GROUP BY scope ORDER BY scope"
                )
                .fetchall()
            )
        )
        assert ("docs", 1) in rows
    finally:
        await index.stop()


@pytest.mark.asyncio
async def test_start_survives_failed_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = PropertyIndex(tmp_path / "rag_metadata.db")
    real_apply = index._apply_migrations
    calls = 0

    def _flaky_apply(conn: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("migrate boom")
        real_apply(conn)

    monkeypatch.setattr(index, "_apply_migrations", _flaky_apply)
    with pytest.raises(RuntimeError, match="migrate boom"):
        await index.start()
    await index.start()
    await index.stop()


@pytest.mark.asyncio
async def test_event_loop_completes_task_while_sqlite_thread_busy(
    started_index: PropertyIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = started_index
    real_run = index.run_sqlite

    def _slow_run(fn: object) -> object:
        time.sleep(0.2)
        return real_run(fn)  # type: ignore[arg-type]

    monkeypatch.setattr(index, "run_sqlite", _slow_run)
    seen: list[str] = []

    async def _ping() -> None:
        seen.append("ok")

    await asyncio.gather(
        asyncio.to_thread(index.get_indexed_source, _SOURCE),
        _ping(),
    )
    assert seen == ["ok"]
