"""Loop stall regression tests for friction:39420 (sync SQLite off the event loop)."""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import threading
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
from services.rag.models import IndexResult
from services.rag.property_index import PropertyIndex
from services.rag.rag_service import scope_freshness, state
from services.rag.directory_ops import find_sources_under_prefixes, purge_orphaned_sources
from services.rag.rag_service import search as search_mod
from services.rag.rag_service.indexing import index_file as index_file_mod
from services.rag.rag_service.loop_instrumentation import (
    start_loop_instrumentation,
    stop_loop_instrumentation,
)
from services.rag.search_scope import apply_bm25_sidecar

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
                "metadatas": [
                    {"source": "/new.pdf"},
                    {"source": "/already.pdf"},
                ],
            }

        collection.get.side_effect = _slow_get
        result = await run_chroma(check_pdf_duplicate, collection, "hash", "/new.pdf")
        assert result is not None
        assert result.duplicate is True
        assert result.duplicate_of == "/already.pdf"
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


async def _drive_chunk_file(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reach the production ``chunk_file`` call with the pipeline around it stubbed."""
    config = MagicMock()
    config.is_path_entity_gated.return_value = False
    config.knowledge_extraction.schema_version = 1
    config.knowledge_extraction.extraction_model = "m"
    monkeypatch.setattr(state, "_config", config)
    monkeypatch.setattr(state, "_property_index", None)
    monkeypatch.setattr(state, "_event_bus", None)
    monkeypatch.setattr(state, "_entity_admission_gate", None)
    monkeypatch.setattr(state, "_get_collection", lambda: MagicMock())

    async def _healthy() -> None:
        return None

    async def _article_sync(**kwargs: object) -> None:
        del kwargs
        return None

    async def _no_dup(**kwargs: object) -> None:
        del kwargs
        return None

    async def _no_prefix(**kwargs: object) -> None:
        del kwargs
        return None

    async def _empty(**kwargs: object) -> IndexResult:
        del kwargs
        return IndexResult(deleted=0, indexed=0, unchanged=False, file=str(path))

    async def _chroma(fn: object, *args: object, **kwargs: object) -> dict:
        del fn, args, kwargs
        return {"ids": [], "metadatas": []}

    monkeypatch.setattr(index_file_mod, "require_healthy", _healthy)
    monkeypatch.setattr(index_file_mod, "_run_article_sync_phase", _article_sync)
    monkeypatch.setattr(index_file_mod, "_handle_pdf_duplicate_or_move", _no_dup)
    monkeypatch.setattr(index_file_mod, "_handle_unchanged_prefix_skip", _no_prefix)
    monkeypatch.setattr(index_file_mod, "_handle_empty_chunks", _empty)
    monkeypatch.setattr(
        "services.rag.chroma_writer.run_chroma",
        _chroma,
    )
    await index_file_mod._index_file_impl(path, None, None, str(path.resolve()))


@pytest.mark.asyncio
async def test_chunk_file_off_loop_one_second_sleep_under_drift_cap(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """chunk_file on a worker: 3 s sleep must not log loop_drift_ms ≥ 2000.

    An inline call on the loop thread blocks the drift probe for the full
    sleep, which is past the 2000 ms cap. ``time.sleep`` on a worker thread
    does not.
    """
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    path = tmp_path / "2026-10.md"
    path.write_text("hello\n")

    def _slow_chunk(file_path: Path, target_chars: int | None = None) -> list[object]:
        del file_path, target_chars
        time.sleep(3.0)
        return []

    monkeypatch.setattr(index_file_mod, "chunk_file", _slow_chunk)
    try:
        await _drive_chunk_file(path, monkeypatch)
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
        assert any(
            "chunk_file duration_ms=" in record.message and "path=" in record.message
            for record in caplog.records
        )
    finally:
        monkeypatch.setattr(state, "_config", None)
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_chunk_file_inline_mutation_logs_high_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """chunk_file must run on a non-loop thread via the production to_thread call.

    Patching ``asyncio.to_thread`` to inline hid a deleted ``to_thread``: the
    assertion never read the call in ``_index_file_impl``. Recording
    ``threading.get_ident`` inside the chunker fails when that call is inline.
    """
    path = tmp_path / "2026-10.md"
    path.write_text("hello\n")
    loop_ident = threading.get_ident()
    seen: list[int] = []

    def _slow_chunk(file_path: Path, target_chars: int | None = None) -> list[object]:
        del file_path, target_chars
        seen.append(threading.get_ident())
        return []

    monkeypatch.setattr(index_file_mod, "chunk_file", _slow_chunk)
    try:
        await _drive_chunk_file(path, monkeypatch)
        assert seen, "chunk_file was not called"
        assert seen[0] != loop_ident
    finally:
        monkeypatch.setattr(state, "_config", None)


@pytest.mark.asyncio
async def test_check_pdf_duplicate_get_runs_off_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Production PDF duplicate check must call collection.get off the loop.

    Inlining ``check_pdf_duplicate`` (dropping ``run_chroma``) records the
    loop thread ident and fails this test.
    """
    from services.rag.rag_service.indexing.file_guards import (
        _handle_pdf_duplicate_or_move,
    )

    init_chroma_executor()
    loop_ident = threading.get_ident()
    seen: list[int] = []
    collection = MagicMock()

    def _get(**kwargs: object) -> dict[str, list]:
        del kwargs
        seen.append(threading.get_ident())
        return {"ids": ["a"], "metadatas": [{"source": "/other.pdf"}]}

    collection.get.side_effect = _get
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(state, "_event_bus", None)
    try:
        await _handle_pdf_duplicate_or_move(
            file_path=path,
            source=str(path),
            source_hash="abc",
            collection=collection,
            article_sync_old_path=None,
            force=False,
            correlation_id="c",
            operation=None,
        )
        assert seen, "collection.get was not called"
        assert seen[0] != loop_ident
    finally:
        shutdown_chroma_executor()


@pytest.mark.asyncio
async def test_get_existing_runs_off_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``_get_existing`` must run on the Chroma writer, not the loop thread.

    Replacing ``await run_chroma(_get_existing, ...)`` with a direct call
    records the loop ident and fails this test.
    """
    init_chroma_executor()
    loop_ident = threading.get_ident()
    seen: list[int] = []
    collection = MagicMock()

    def _get(**kwargs: object) -> dict[str, list]:
        del kwargs
        seen.append(threading.get_ident())
        return {"ids": [], "metadatas": []}

    collection.get.side_effect = _get
    path = tmp_path / "note.md"
    path.write_text("hello\n")
    config = MagicMock()
    config.is_path_entity_gated.return_value = False
    config.knowledge_extraction.schema_version = 1
    config.knowledge_extraction.extraction_model = "m"
    monkeypatch.setattr(state, "_config", config)
    monkeypatch.setattr(state, "_property_index", None)
    monkeypatch.setattr(state, "_event_bus", None)
    monkeypatch.setattr(state, "_entity_admission_gate", None)
    monkeypatch.setattr(state, "_get_collection", lambda: collection)

    async def _healthy() -> None:
        return None

    async def _article_sync(**kwargs: object) -> None:
        del kwargs
        return None

    async def _no_dup(**kwargs: object) -> None:
        del kwargs
        return None

    async def _no_prefix(**kwargs: object) -> None:
        del kwargs
        return None

    async def _empty(**kwargs: object) -> IndexResult:
        del kwargs
        return IndexResult(deleted=0, indexed=0, unchanged=False, file=str(path))

    monkeypatch.setattr(index_file_mod, "require_healthy", _healthy)
    monkeypatch.setattr(index_file_mod, "_run_article_sync_phase", _article_sync)
    monkeypatch.setattr(index_file_mod, "_handle_pdf_duplicate_or_move", _no_dup)
    monkeypatch.setattr(index_file_mod, "_handle_unchanged_prefix_skip", _no_prefix)
    monkeypatch.setattr(index_file_mod, "_handle_empty_chunks", _empty)

    def _chunk(file_path: Path, target_chars: int | None = None) -> list[object]:
        del file_path, target_chars
        return []

    monkeypatch.setattr(index_file_mod, "chunk_file", _chunk)
    try:
        await index_file_mod._index_file_impl(path, None, None, str(path.resolve()))
        assert seen, "_get_existing did not call collection.get"
        assert seen[0] != loop_ident
    finally:
        monkeypatch.setattr(state, "_config", None)
        shutdown_chroma_executor()


@pytest.mark.asyncio
async def test_find_sources_list_known_sources_off_loop_under_drift_cap(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``list_known_sources_fn`` on a worker thread must not log loop_drift_ms ≥ 2000."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    init_chroma_executor()
    collection = MagicMock()

    async def _empty_chroma(fn: object, *args: object, **kwargs: object) -> set[str]:
        del fn, args, kwargs
        return set()

    monkeypatch.setattr(
        "services.rag.directory_ops.run_chroma",
        _empty_chroma,
    )

    def _slow_list(_prefixes: list[str]) -> set[str]:
        time.sleep(1.0)
        return {"/watch/stale.md"}

    try:
        sources = await find_sources_under_prefixes(
            collection=collection,
            prefixes=["/watch/"],
            list_known_sources_fn=_slow_list,
        )
        assert sources == {"/watch/stale.md"}
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
    finally:
        shutdown_chroma_executor()
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_purge_orphaned_sources_list_known_sources_off_loop(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``purge_orphaned_sources`` reaches ``list_known_sources`` off the loop."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    init_chroma_executor()
    collection = MagicMock()

    async def _empty_chroma(fn: object, *args: object, **kwargs: object) -> set[str]:
        del fn, args, kwargs
        return set()

    monkeypatch.setattr(
        "services.rag.directory_ops.run_chroma",
        _empty_chroma,
    )

    missing = "/watch/missing.md"

    def _slow_list(_prefixes: list[str]) -> set[str]:
        time.sleep(1.0)
        return {missing}

    try:
        _files, _chunks, purged = await purge_orphaned_sources(
            collection=collection,
            watch_prefixes=["/watch/"],
            list_known_sources_fn=_slow_list,
        )
        assert purged == {missing}
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
    finally:
        shutdown_chroma_executor()
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_indexed_sources_for_scope_off_loop_under_drift_cap(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sources_for_prefixes`` on a worker thread must not log loop_drift_ms ≥ 2000."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()

    class _SlowFts:
        def sources_for_prefixes(self, _prefixes: list[str]) -> list[str]:
            time.sleep(1.0)
            return ["/scope/a.md"]

        def get_count(self) -> int:
            return 1

    class _Pi:
        fts = _SlowFts()

    monkeypatch.setattr(state, "_property_index", _Pi())
    try:
        result = await search_mod._indexed_sources_for_scope(["/scope/"])
        assert result == ["/scope/a.md"]
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
    finally:
        monkeypatch.setattr(state, "_property_index", None)
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_index_file_get_indexed_source_off_loop_under_drift_cap(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``get_indexed_source`` on a worker thread must not log loop_drift_ms ≥ 2000."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    path = tmp_path / "note.md"
    path.write_text("body\n")

    pi = MagicMock()

    def _slow_get(_source: str) -> None:
        time.sleep(1.0)
        return None

    pi.get_indexed_source.side_effect = _slow_get
    config = MagicMock()
    config.is_path_entity_gated.return_value = False
    config.knowledge_extraction.schema_version = 1
    config.knowledge_extraction.extraction_model = "m"
    monkeypatch.setattr(state, "_config", config)
    monkeypatch.setattr(state, "_property_index", pi)
    monkeypatch.setattr(state, "_event_bus", None)
    monkeypatch.setattr(state, "_entity_admission_gate", None)
    monkeypatch.setattr(state, "_get_collection", lambda: MagicMock())
    async def _healthy() -> None:
        return None

    async def _noop(**kwargs: object) -> None:
        del kwargs
        return None

    async def _empty_result(**kwargs: object) -> IndexResult:
        del kwargs
        return IndexResult(deleted=0, indexed=0, unchanged=False, file=str(path))

    monkeypatch.setattr(index_file_mod, "require_healthy", _healthy)
    monkeypatch.setattr(index_file_mod, "_run_article_sync_phase", _noop)
    monkeypatch.setattr(index_file_mod, "_handle_pdf_duplicate_or_move", _noop)
    monkeypatch.setattr(index_file_mod, "_handle_unchanged_prefix_skip", _noop)
    monkeypatch.setattr(index_file_mod, "_handle_empty_chunks", _empty_result)
    init_chroma_executor()

    async def _chroma(fn: object, *args: object, **kwargs: object) -> dict[str, list]:
        del fn, args, kwargs
        return {"ids": [], "metadatas": []}

    monkeypatch.setattr(
        "services.rag.chroma_writer.run_chroma",
        _chroma,
    )
    monkeypatch.setattr(index_file_mod, "chunk_file", lambda *a, **k: [])
    try:
        await index_file_mod._index_file_impl(path, None, None, str(path.resolve()))
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
        pi.get_indexed_source.assert_called()
    finally:
        monkeypatch.setattr(state, "_property_index", None)
        monkeypatch.setattr(state, "_config", None)
        shutdown_chroma_executor()
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_apply_bm25_sidecar_search_scoped_off_loop_under_drift_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scoped ``search_scoped`` on a worker thread must not log loop_drift_ms ≥ 2000."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()

    class _SlowFts:
        def search_scoped(
            self,
            _query: str,
            _prefixes: list[str],
            *,
            limit: int = 50,
        ) -> list[tuple[str, float]]:
            del limit
            time.sleep(1.0)
            return []

        def search(self, _query: str, *, limit: int = 50) -> list[tuple[str, float]]:
            del limit
            return []

    fts = _SlowFts()
    collection = MagicMock()
    try:
        ids, _chunks, _metadatas, _distances, hits = await apply_bm25_sidecar(
            ids=["d1"],
            chunks=["c"],
            metadatas=[{"source": "/p/a.md"}],
            distances=[0.1],
            query="needle",
            fts=fts,  # type: ignore[arg-type]
            collection=collection,
            source_prefixes=["/p/"],
        )
        assert ids == ["d1"]
        assert hits == 0
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
    finally:
        await stop_loop_instrumentation()


@pytest.mark.asyncio
async def test_bm25_sidecar_code_token_dense_only_no_stall(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """FTS token ``code`` must not error the sidecar or stall the loop over 2 s."""
    _capture_loop_drift(caplog)
    start_loop_instrumentation()
    db_path = tmp_path / "fts.db"
    pi = PropertyIndex(db_path)
    await pi.start()
    try:
        await pi.fts.insert("c1", "/docs/a.md", "snippet about programming")
        collection = MagicMock()
        ids, _chunks, _metadatas, _distances, hits = await apply_bm25_sidecar(
            ids=["d1"],
            chunks=["dense"],
            metadatas=[{"source": "/docs/a.md"}],
            distances=[0.2],
            query="code",
            fts=pi.fts,
            collection=collection,
            source_prefixes=["/docs/"],
        )
        assert ids == ["d1"]
        assert hits == 0
        collection.get.assert_not_called()
        await asyncio.sleep(0.05)
        assert _max_loop_drift_ms(caplog) < _DRIFT_FAIL_MS
    finally:
        await pi.stop()
        await stop_loop_instrumentation()
