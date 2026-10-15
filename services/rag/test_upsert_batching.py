"""ChromaDB upsert respects max_batch_size via create_batches."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.chroma_writer import (
    DELETE_IDS_CAP,
    UPSERT_BATCH_CAP,
    init_chroma_executor,
    run_chroma,
    shutdown_chroma_executor,
)
from services.rag.rag_service.api import health
from services.rag.rag_service.indexing.chroma import _upsert_chroma_chunk_batches
from services.rag.rag_service.loop_instrumentation import (
    _gil_gap_monitor,
    _loop_drift_monitor,
)


class _FakeChroma:
    def __init__(self, max_batch_size: int) -> None:
        self._max = max_batch_size

    def get_max_batch_size(self) -> int:
        return self._max


@pytest.fixture(autouse=True)
def _chroma_executor_lifecycle() -> None:
    init_chroma_executor()
    yield
    shutdown_chroma_executor()


@pytest.mark.asyncio
async def test_upsert_splits_into_two_batches_when_over_max() -> None:
    # total = max_bs + 100 ⇒ two batches iff max_bs >= 100 (ceil division).
    max_bs = 1000
    total = max_bs + 100
    chroma = _FakeChroma(max_bs)
    collection = MagicMock()
    event_bus = None

    ids = [f"id{i}" for i in range(total)]
    embeddings = [[0.0, 1.0] for _ in range(total)]
    texts = [f"t{i}" for i in range(total)]
    metadatas = [{"i": i} for i in range(total)]

    await _upsert_chroma_chunk_batches(
        chroma_client=chroma,
        collection=collection,
        event_bus=event_bus,
        source="/tmp/large.md",
        correlation_id="op-1",
        operation="index",
        ids=ids,
        embeddings=embeddings,
        texts=texts,
        metadatas=metadatas,
    )

    assert collection.upsert.call_count == 3
    sizes = [len(c.kwargs["ids"]) for c in collection.upsert.call_args_list]
    assert sizes == [UPSERT_BATCH_CAP, UPSERT_BATCH_CAP, total - 2 * UPSERT_BATCH_CAP]


@pytest.mark.asyncio
async def test_upsert_emits_started_completed_per_batch() -> None:
    max_bs = 1000
    total = max_bs + 100
    chroma = _FakeChroma(max_bs)
    collection = MagicMock()
    event_bus = MagicMock()
    event_bus.publish_nowait = AsyncMock()

    ids = [f"id{i}" for i in range(total)]
    embeddings = [[0.0] for _ in range(total)]
    texts = [f"t{i}" for i in range(total)]
    metadatas = [{"i": i} for i in range(total)]

    await _upsert_chroma_chunk_batches(
        chroma_client=chroma,
        collection=collection,
        event_bus=event_bus,
        source="/tmp/large.md",
        correlation_id="op-1",
        operation="reindex",
        ids=ids,
        embeddings=embeddings,
        texts=texts,
        metadatas=metadatas,
    )

    assert event_bus.publish_nowait.await_count == 6
    events = [c.args[0] for c in event_bus.publish_nowait.call_args_list]
    assert all(e.signal.startswith("rag.chroma.upsert.") for e in events)
    assert events[0].signal == "rag.chroma.upsert.started"
    assert events[0].payload["batch_index"] == 0
    assert events[0].payload["batch_total"] == 3
    assert events[-1].signal == "rag.chroma.upsert.completed"
    assert events[-1].payload["batch_index"] == 2
    assert events[-1].payload["batch_total"] == 3


@pytest.mark.asyncio
async def test_loop_drift_and_gil_gap_are_distinct_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failure mode 11: loop_drift_ms and gil_gap_ms are separate log fields."""
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    task = asyncio.create_task(_loop_drift_monitor(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.gather(task, return_exceptions=True)
    assert not any("loop_lag_ms" in r.message for r in caplog.records)
    caplog.clear()
    ticks = iter([0.0, 0.08])

    class _Stop:
        n = 0

        def wait(self, _seconds: float) -> bool:
            self.n += 1
            return self.n >= 2

    with patch(
        "services.rag.rag_service.loop_instrumentation.time.perf_counter",
        side_effect=lambda: next(ticks),
    ):
        _gil_gap_monitor(_Stop())  # type: ignore[arg-type]
    gil_lines = [r.message for r in caplog.records if "gil_gap_ms=" in r.message]
    assert gil_lines == ["gil_gap_ms=70.000 GIL"]


@pytest.mark.asyncio
async def test_loop_drift_logs_only_above_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M1: sub-threshold schedule slack must not emit; a stall above 50 ms must.

    Fails on 54447c40, which logs whenever drift_ms > 0.
    """
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    clock = {"t": 0.0}
    step = {"n": 0}

    class _Loop:
        def time(self) -> float:
            return clock["t"]

    async def _sleep(_seconds: float) -> None:
        step["n"] += 1
        if step["n"] == 1:
            clock["t"] = 0.011
            return
        if step["n"] == 2:
            clock["t"] = 0.081
            return
        stop.set()

    with (
        patch(
            "services.rag.rag_service.loop_instrumentation.asyncio.get_running_loop",
            return_value=_Loop(),
        ),
        patch(
            "services.rag.rag_service.loop_instrumentation.asyncio.sleep",
            side_effect=_sleep,
        ),
    ):
        await _loop_drift_monitor(stop)

    drift_lines = [r.message for r in caplog.records if "loop_drift_ms=" in r.message]
    assert drift_lines == ["loop_drift_ms=60.000"]


@pytest.mark.asyncio
async def test_health_logs_handler_ms_and_count_wait_ms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failure mode 12: health_handler_ms covers queue breakdown; count_wait_ms does not."""
    from services.rag.rag_service import state

    caplog.set_level(logging.INFO)
    slow = MagicMock()
    slow.total = slow.ready = slow.in_flight = 0
    slow.cooling_off = slow.capacity_blocked = slow.exhausted = 0

    def slow_breakdown() -> MagicMock:
        import time

        time.sleep(0.02)
        return slow

    collection = MagicMock()
    collection.count.return_value = 42
    activation = state.DependencyActivationState()
    activation.phase = "ready"
    with (
        patch.object(state, "_collection", collection),
        patch.object(
            state,
            "_property_index",
            MagicMock(get_extraction_queue_breakdown=slow_breakdown),
        ),
        patch.object(state, "_dependency_activation", activation),
        patch.object(state, "_watcher_manager", None),
        patch(
            "services.rag.rag_service.api.get_embed_model_id", return_value="test-model"
        ),
        patch(
            "services.rag.rag_service.api.resolve_code_version", return_value="test-sha"
        ),
    ):
        result = health()
    assert result["collection_count"] == 42
    messages = " ".join(r.message for r in caplog.records)
    assert "count_wait_ms=" in messages
    assert "health_handler_ms=" in messages
    assert "loop_lag_ms" not in messages


@pytest.mark.asyncio
async def test_upsert_over_500_is_split() -> None:
    chroma = _FakeChroma(10_000)
    collection = MagicMock()
    n = UPSERT_BATCH_CAP + 50
    await _upsert_chroma_chunk_batches(
        chroma_client=chroma,
        collection=collection,
        event_bus=None,
        source="/tmp/x.md",
        correlation_id="c1",
        operation="index",
        ids=[f"id{i}" for i in range(n)],
        embeddings=[[0.0] for _ in range(n)],
        texts=[f"t{i}" for i in range(n)],
        metadatas=[{"i": i} for i in range(n)],
    )
    for call in collection.upsert.call_args_list:
        assert len(call.kwargs["ids"]) <= UPSERT_BATCH_CAP


@pytest.mark.asyncio
async def test_delete_over_2000_is_sliced() -> None:
    from services.rag.chroma_writer import chroma_delete_ids

    collection = MagicMock()
    ids = [f"c{i}" for i in range(DELETE_IDS_CAP + 100)]
    await run_chroma(chroma_delete_ids, collection, ids)
    assert collection.delete.call_count == 2
    assert len(collection.delete.call_args_list[0].kwargs["ids"]) == DELETE_IDS_CAP


@pytest.mark.asyncio
async def test_run_chroma_exception_allows_next_call() -> None:
    def _boom() -> None:
        raise RuntimeError("boom")

    def _ok() -> int:
        return 1

    with pytest.raises(RuntimeError, match="boom"):
        await run_chroma(_boom)
    assert await run_chroma(_ok) == 1


def test_run_chroma_two_asyncio_run_calls() -> None:
    async def _once() -> int:
        return await run_chroma(lambda: 7)

    assert asyncio.run(_once()) == 7
    assert asyncio.run(_once()) == 7


@pytest.mark.asyncio
async def test_concurrent_run_chroma_max_inflight_is_one() -> None:

    inflight = 0
    max_inflight = 0

    def _slow() -> None:
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        time.sleep(0.05)
        inflight -= 1

    async def _runner() -> None:
        await run_chroma(_slow)

    import time

    await asyncio.gather(*[_runner() for _ in range(4)])
    assert max_inflight == 1


@pytest.mark.asyncio
async def test_purge_excluded_sources_submits_each_page_and_delete_slice() -> None:
    from services.rag.directory_ops import find_sources_under_prefixes
    from services.rag.rag_service import startup_cleanup, state

    run_calls = 0
    real_run = run_chroma

    async def _counting_run(fn, /, *args, **kwargs):  # noqa: ANN001
        nonlocal run_calls
        run_calls += 1
        return await real_run(fn, *args, **kwargs)

    collection = MagicMock()
    collection.get.return_value = {
        "metadatas": [{"source": "/watch/excluded/file.md"}],
    }
    cfg = MagicMock()
    cfg.watch_directories = [MagicMock(path="/watch", exclude=["**/excluded/**"])]

    with (
        patch.object(state, "_collection", collection),
        patch.object(state, "_property_index", MagicMock(list_known_sources=lambda _p: set())),
        patch.object(state, "_event_bus", None),
        patch(
            "services.rag.rag_service.startup_cleanup.matches_watch_exclude",
            return_value=True,
        ),
        patch(
            "services.rag.directory_ops.run_chroma",
            side_effect=_counting_run,
        ),
        patch(
            "services.rag.rag_service.startup_cleanup.delete_sources",
            new_callable=AsyncMock,
        ) as mock_delete,
    ):
        mock_delete.return_value = (1, 1)
        await startup_cleanup._purge_excluded_sources(cfg)
    assert run_calls == 1
    mock_delete.assert_awaited_once()
    await find_sources_under_prefixes(collection=collection, prefixes=["/watch/"])


@pytest.mark.asyncio
async def test_slice_failure_does_not_call_metadata() -> None:
    from services.rag.directory_ops import delete_sources

    ids = [f"x{i}" for i in range(DELETE_IDS_CAP + 1)]
    collection = MagicMock()
    collection.get.return_value = {"ids": ids}
    delete_calls = 0

    def _delete_side_effect(*, ids: list[str]) -> None:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 2:
            raise RuntimeError("slice fail")

    collection.delete.side_effect = _delete_side_effect
    metadata = AsyncMock()
    with pytest.raises(RuntimeError, match="slice fail"):
        await delete_sources(
            collection=collection,
            sources={"/tmp/source.md"},
            remove_source_metadata_fn=metadata,
        )
    metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_source_retry_deletes_properties_by_source() -> None:
    import sqlite3

    from services.rag.fts_index import FtsIndex
    from services.rag.property_index import PropertyIndex

    fts = FtsIndex()
    conn = sqlite3.connect(":memory:")
    from universal_event_bus.actor.sequential import SequentialExecutor

    seq = SequentialExecutor()
    fts.attach(conn, seq)
    conn.execute("CREATE TABLE properties (chunk_id TEXT, source TEXT)")
    conn.execute(
        "INSERT INTO properties VALUES ('c1', '/src/a.md'), ('c2', '/src/a.md')"
    )
    conn.commit()
    fts_calls: list[str] = []
    original = fts.remove_for_source

    async def _wrap(source: str) -> int:
        fts_calls.append(source)
        return await original(source)

    fts.remove_for_source = _wrap  # type: ignore[method-assign]
    await fts.remove_for_source("/src/a.md")
    assert fts_calls == ["/src/a.md"]
    remaining = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    assert remaining == 2
    conn.execute("DELETE FROM properties WHERE source = ?", ("/src/a.md",))
    assert conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0] == 0
    _ = PropertyIndex


@pytest.mark.asyncio
async def test_shutdown_cancel_still_waits_executor() -> None:
    from services.rag.rag_service import lifecycle, state

    shutdown_order: list[str] = []

    async def _slow_index() -> None:
        try:
            await run_chroma(lambda: time.sleep(0.2))
        except asyncio.CancelledError:
            raise

    import time

    task = asyncio.create_task(_slow_index())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with (
        patch("services.rag.rag_service.lifecycle.stop_loop_instrumentation", AsyncMock()),
        patch("services.rag.rag_service.lifecycle.stop_extraction_runtime", AsyncMock()),
        patch(
            "services.rag.rag_service.lifecycle.shutdown_chroma_executor",
            side_effect=lambda: shutdown_order.append("executor"),
        ),
        patch.object(state, "_background_tasks", set()),
        patch.object(state, "_admission_gate", None),
        patch.object(state, "_entity_admission_gate", None),
        patch.object(state, "_event_bus", None),
        patch.object(state, "_watcher_manager", None),
    ):
        prop = MagicMock()
        prop.stop = AsyncMock(side_effect=lambda: shutdown_order.append("property_index"))
        state._property_index = prop
        await lifecycle._shutdown()
    assert shutdown_order[0] == "executor"


@pytest.mark.asyncio
async def test_shutdown_waits_executor_before_property_index_stop() -> None:
    from services.rag.rag_service import lifecycle, state

    shutdown_order: list[str] = []

    with (
        patch("services.rag.rag_service.lifecycle.stop_loop_instrumentation", AsyncMock()),
        patch("services.rag.rag_service.lifecycle.stop_extraction_runtime", AsyncMock()),
        patch(
            "services.rag.rag_service.lifecycle.shutdown_chroma_executor",
            side_effect=lambda: shutdown_order.append("executor"),
        ),
        patch.object(state, "_background_tasks", set()),
        patch.object(state, "_admission_gate", None),
        patch.object(state, "_entity_admission_gate", None),
        patch.object(state, "_event_bus", None),
        patch.object(state, "_watcher_manager", None),
    ):
        prop = MagicMock()

        async def _stop() -> None:
            shutdown_order.append("property_index")
            assert "executor" in shutdown_order

        prop.stop = _stop
        state._property_index = prop
        await lifecycle._shutdown()
    assert shutdown_order == ["executor", "property_index"]


@pytest.mark.asyncio
async def test_migrate_and_extract_source_use_run_chroma(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from services.rag.extraction import chroma_source
    from services.rag.indexing_helpers import check_pdf_duplicate, migrate_chroma_source

    caplog.set_level(logging.INFO)
    collection = MagicMock()
    collection.get.return_value = {"ids": [], "metadatas": [], "documents": []}

    await run_chroma(migrate_chroma_source, collection, "h", "/old", "/new")
    await run_chroma(check_pdf_duplicate, collection, "h", "/new")
    await chroma_source.extract_source(
        "/s",
        collection=collection,
        config=MagicMock(schema_version=1, extraction_model="m"),
        rag_config=MagicMock(get_scope_for_path=lambda _s: "all"),
        property_index=MagicMock(add_batch_with_scope=AsyncMock()),
    )
    logged = " ".join(r.message for r in caplog.records)
    assert "fn=migrate_chroma_source" in logged
    assert "fn=check_pdf_duplicate" in logged
    assert "fn=chroma_get_where" in logged


@pytest.mark.asyncio
async def test_update_over_500_is_sliced() -> None:
    from services.rag.chroma_writer import chroma_update_metadatas

    collection = MagicMock()
    n = UPSERT_BATCH_CAP + 10
    ids = [f"i{i}" for i in range(n)]
    metas = [{"i": i} for i in range(n)]
    await run_chroma(chroma_update_metadatas, collection, ids, metas)
    assert collection.update.call_count == 2
    assert len(collection.update.call_args_list[0].kwargs["ids"]) == UPSERT_BATCH_CAP


@pytest.mark.asyncio
async def test_cancel_awaiting_task_does_not_drop_queued_chroma_job() -> None:
    started = asyncio.Event()
    allow_finish = asyncio.Event()

    def _blocking() -> None:
        started.set()
        while not allow_finish.is_set():
            time.sleep(0.01)

    import time

    async def _waiter() -> None:
        await run_chroma(_blocking)

    task = asyncio.create_task(_waiter())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    allow_finish.set()
    assert await run_chroma(lambda: 99) == 99
