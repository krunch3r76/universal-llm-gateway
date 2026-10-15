"""Loop stall regression tests for friction:39420 (startup repair, PDF dup, commit)."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.rag.chroma_writer import (
    init_chroma_executor,
    run_chroma,
    shutdown_chroma_executor,
)
from services.rag.config import RagConfig, ScopeDefinition, WatchDirectory
from services.rag.corpus_hints import freshness
from services.rag.indexing_helpers import check_pdf_duplicate
from services.rag.property_index import PropertyIndex
from services.rag.rag_service import scope_freshness, state
from services.rag.rag_service.loop_instrumentation import (
    start_loop_instrumentation,
    stop_loop_instrumentation,
)

_DRIFT_FAIL_MS = 2000.0
_LOOP_DRIFT_LOGGER = "services.rag.rag_service.loop_instrumentation"


def _capture_loop_drift(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=_LOOP_DRIFT_LOGGER)


def _hold_gil(seconds: float) -> None:
    """Busy-wait so the caller keeps the GIL (``time.sleep`` would release it)."""
    deadline = time.perf_counter() + seconds
    acc = 0
    while time.perf_counter() < deadline:
        acc = (acc + 1) & 0xFFFFFF
    if acc < 0:
        raise RuntimeError("unreachable")


def _max_loop_drift_ms(caplog: pytest.LogCaptureFixture) -> float:
    vals: list[float] = []
    for record in caplog.records:
        match = re.search(r"loop_drift_ms=([\d.]+)", record.message)
        if match:
            vals.append(float(match.group(1)))
    return max(vals) if vals else 0.0


@pytest.mark.asyncio
async def test_detect_stale_scopes_off_loop_survives_one_second_scan(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I1 pass: 1 s scan on a worker thread must not log loop_drift_ms ≥ 2000."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()

    def _slow_detect(**kwargs: object) -> list[str]:
        del kwargs
        time.sleep(1.0)
        return ["docs"]

    monkeypatch.setattr(freshness, "detect_stale_scopes", _slow_detect)
    pi = MagicMock()
    stale = await freshness.detect_stale_scopes_off_loop(
        property_index=pi,
        configured_scopes={"docs": ["/tmp"]},
        scope_filter=None,
    )
    assert stale == ["docs"]
    await asyncio.sleep(0.05)
    try:
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_detect_stale_scopes_on_loop_mutation_logs_high_drift(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I1 mutation: startup repair with sync detect_stale_scopes on the loop drifts."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    config = RagConfig(
        watch_directories=[WatchDirectory(path="/tmp")],
        scopes={"docs": ScopeDefinition(prefixes=["/tmp"], description="Docs")},
    )

    def _slow_detect(**kwargs: object) -> list[str]:
        del kwargs
        _hold_gil(2.5)
        return []

    async def _sync_on_loop(**kwargs: object) -> list[str]:
        return freshness.detect_stale_scopes(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(freshness, "detect_stale_scopes", _slow_detect)
    monkeypatch.setattr(scope_freshness, "detect_stale_scopes_off_loop", _sync_on_loop)
    pi = MagicMock()
    pi.check_watermarks.return_value = []
    monkeypatch.setattr(state, "_property_index", pi)
    monkeypatch.setattr(state, "_post_index_stale", False)
    await asyncio.sleep(0.02)
    try:
        await scope_freshness._run_startup_scope_freshness_repair(config)
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) >= _DRIFT_FAIL_MS
    finally:
        monkeypatch.setattr(state, "_property_index", None)
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_check_pdf_duplicate_cooperative_pages_under_drift_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """I2 pass: GIL-holding get on the chroma writer stays under loop_drift_ms 2000.

    Paging was reverted (no measured source_hash get ≥ 2000 ms). One ``get``
    returns two ids so a single-page empty stub is not the exercised shape,
    and the duplicate decision stays None when every match is this source.
    """
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    init_chroma_executor()
    try:
        collection = MagicMock()

        def _slow_get(**kwargs: object) -> dict[str, list]:
            assert kwargs.get("limit") == 10
            _hold_gil(0.3)
            return {
                "ids": ["a", "b"],
                "metadatas": [{"source": "/new.pdf"}, {"source": "/new.pdf"}],
            }

        collection.get.side_effect = _slow_get
        result = await run_chroma(check_pdf_duplicate, collection, "hash", "/new.pdf")
        assert result is None
        assert collection.get.call_count == 1
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
    finally:
        shutdown_chroma_executor()
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_check_pdf_duplicate_mutation_single_blocking_get(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """I2 mutation: check_pdf_duplicate on the loop with a GIL-holding get drifts."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    collection = MagicMock()

    def _slow_get(**kwargs: object) -> dict[str, list]:
        del kwargs
        _hold_gil(2.5)
        return {"ids": ["a", "b"], "metadatas": [{}, {}]}

    collection.get.side_effect = _slow_get
    await asyncio.sleep(0.02)
    check_pdf_duplicate(collection, "hash", "/new.pdf")
    await asyncio.sleep(0.05)
    try:
        assert _max_loop_drift_ms(caplog) >= _DRIFT_FAIL_MS
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_remove_chunk_sqlite_off_loop_under_drift_cap(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I3 pass: 1 s SQLite delete on a worker thread must not log loop_drift_ms ≥ 2000."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "meta.db"
        pi = PropertyIndex(db_path)
        await pi.start()
        try:
            await pi.add("key", "chunk-a", scope="all", source="/s")
            real_to_thread = asyncio.to_thread

            async def _sleeping_to_thread(fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
                def _wrap() -> object:
                    time.sleep(1.0)
                    return fn(*args, **kwargs)

                return await real_to_thread(_wrap)

            monkeypatch.setattr(asyncio, "to_thread", _sleeping_to_thread)
            await pi.remove_chunk("chunk-a")
            await asyncio.sleep(0.05)
            assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
        finally:
            await pi.stop()
    await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_remove_chunk_sqlite_on_loop_mutation_logs_high_drift(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I3 mutation: inline SQLite (no to_thread) must log loop_drift_ms ≥ 2000."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "meta.db"
        pi = PropertyIndex(db_path)
        await pi.start()
        try:
            await pi.add("key", "chunk-a", scope="all", source="/s")
            real_ensure = pi._ensure_conn

            class _SlowDeleteConn:
                def __init__(self, inner: object) -> None:
                    self._inner = inner

                def execute(self, sql: str, *args: object, **kwargs: object) -> object:
                    if sql.startswith("DELETE"):
                        _hold_gil(2.5)
                    return self._inner.execute(sql, *args, **kwargs)  # type: ignore[attr-defined]

                def commit(self) -> None:
                    self._inner.commit()  # type: ignore[attr-defined]

            monkeypatch.setattr(
                pi, "_ensure_conn", lambda: _SlowDeleteConn(real_ensure())
            )

            async def _inline_to_thread(fn, /, *args, **kwargs):  # type: ignore[no-untyped-def]
                return fn(*args, **kwargs)

            monkeypatch.setattr(asyncio, "to_thread", _inline_to_thread)
            await pi.remove_chunk("chunk-a")
            await asyncio.sleep(0.05)
            assert _max_loop_drift_ms(caplog) >= _DRIFT_FAIL_MS
        finally:
            await pi.stop()
    await stop_loop_instrumentation()
