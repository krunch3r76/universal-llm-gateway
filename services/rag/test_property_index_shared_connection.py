"""Concurrency regression for PropertyIndex shared SQLite connection (friction:39548)."""

from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path

import pytest
import pytest_asyncio

from services.rag.property_index import PropertyIndex

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
