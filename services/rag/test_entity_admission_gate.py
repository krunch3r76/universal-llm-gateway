"""Unit tests for EntityAdmissionGate and entity-gated indexing layers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.config import RagConfig, WatchDirectory
from services.rag.entity_admission import EntityAdmissionGate
from services.rag.entity_admission._io import _refresh
from services.rag.events.indexing import (
    rag_entity_gate_io_failed,
    rag_file_indexing_gated,
)
from services.rag.watcher_manager import WatcherManager


def _gated_config() -> RagConfig:
    return RagConfig(
        watch_directories=[
            WatchDirectory(path="/legal", entity_gated=True),
            WatchDirectory(path="/research", entity_gated=False),
            WatchDirectory(
                path="/legal/nested",
                entity_gated=False,
            ),
        ],
        scopes={},
    )


def test_is_path_entity_gated_longest_prefix() -> None:
    config = _gated_config()
    assert config.is_path_entity_gated("/legal/doc.pdf") is True
    assert config.is_path_entity_gated("/legal") is True
    assert config.is_path_entity_gated("/legal/nested/doc.pdf") is False
    assert config.is_path_entity_gated("/research/paper.pdf") is False


def test_is_path_entity_gated_trailing_separator_guard() -> None:
    config = RagConfig(
        watch_directories=[WatchDirectory(path="/legal", entity_gated=True)],
        scopes={},
    )
    assert config.is_path_entity_gated("/legal-archive/doc.pdf") is False


@pytest.mark.asyncio
async def test_should_attempt_gated_unbacked_emits_and_skips() -> None:
    gate = EntityAdmissionGate()
    gate._admitted = set()
    emitted: list[object] = []

    async def capture_emit(event: object) -> None:
        emitted.append(event)

    wm = WatcherManager(
        index_fn=AsyncMock(),
        event_bus=MagicMock(),
        entity_admission_gate=gate,
    )
    wm._emit = capture_emit  # type: ignore[method-assign]

    fp = Path("/legal/unbacked.pdf")
    result = await wm._should_attempt(fp, entity_gated=True)

    assert result is False
    assert len(emitted) == 1
    assert emitted[0].signal == "rag.file.indexing.gated"
    assert emitted[0].payload["layer"] == "watcher_sweep"


@pytest.mark.asyncio
async def test_should_attempt_ungated_passthrough_without_gate() -> None:
    gate = EntityAdmissionGate()
    gate._admitted = set()
    wm = WatcherManager(
        index_fn=AsyncMock(),
        event_bus=MagicMock(),
        entity_admission_gate=gate,
    )
    assert await wm._should_attempt(Path("/research/open.pdf"), entity_gated=False)


@pytest.mark.asyncio
async def test_refresh_fail_safe_keeps_prior_set_on_http_error() -> None:
    gate = EntityAdmissionGate()
    gate._admitted = {"/backed/doc.pdf"}
    gate._ready = True

    with patch(
        "services.rag.entity_admission._io.make_async_client",
        side_effect=RuntimeError("cortex-api down"),
    ):
        await _refresh(gate)

    assert gate._admitted == {"/backed/doc.pdf"}
    assert gate._ready is True


@pytest.mark.asyncio
async def test_refresh_failure_emits_io_failed_event() -> None:
    gate = EntityAdmissionGate(event_bus=MagicMock())
    gate._event_bus.publish_nowait = AsyncMock()
    gate._admitted = {"/backed/doc.pdf"}
    gate._ready = True

    with patch(
        "services.rag.entity_admission._io.make_async_client",
        side_effect=RuntimeError("cortex-api down"),
    ):
        await _refresh(gate)

    gate._event_bus.publish_nowait.assert_awaited_once()
    published = gate._event_bus.publish_nowait.await_args.args[0]
    assert published.signal == "rag.entity.gate.io.failed"
    assert published.payload["operation"] == "refresh"
    assert "cortex-api down" in published.payload["error"]


def test_rag_entity_gate_io_failed_factory_shape() -> None:
    ev = rag_entity_gate_io_failed(operation="subscribe", error="ws reset")
    assert ev.signal == "rag.entity.gate.io.failed"
    assert ev.payload == {"operation": "subscribe", "error": "ws reset"}


@pytest.mark.asyncio
async def test_refresh_replaces_set_on_success() -> None:
    gate = EntityAdmissionGate()
    gate._admitted = set()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"paths": ["/legal/a.pdf", "/legal/b.pdf"], "unresolved": 0}

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, path: str) -> FakeResponse:
            assert path == "/entities/source-paths"
            return FakeResponse()

    with patch(
        "services.rag.entity_admission._io.make_async_client",
        return_value=FakeClient(),
    ):
        await _refresh(gate)

    assert gate.is_admitted("/legal/a.pdf")
    assert gate.is_ready()


@pytest.mark.asyncio
async def test_index_file_impl_l2_gated_returns_unchanged() -> None:
    from services.rag.rag_service import indexing, state

    source = "/legal/unbacked.pdf"
    file_path = Path(source)
    gate = EntityAdmissionGate()
    gate._admitted = set()

    config = RagConfig(
        watch_directories=[WatchDirectory(path="/legal", entity_gated=True)],
        scopes={},
    )

    event_bus = MagicMock()
    event_bus.publish_nowait = AsyncMock()

    with (
        patch.object(state, "_config", config),
        patch.object(state, "_entity_admission_gate", gate),
        patch.object(state, "_property_index", None),
        patch.object(state, "_event_bus", event_bus),
        patch(
            "services.rag.rag_service.indexing.asyncio.to_thread",
            return_value=MagicMock(st_mtime_ns=1, st_size=100),
        ),
    ):
        result = await indexing._index_file_impl(
            file_path,
            None,
            None,
            source,
        )

    assert result.unchanged is True
    assert result.indexed == 0
    event_bus.publish_nowait.assert_awaited_once()
    published = event_bus.publish_nowait.await_args.args[0]
    assert published.signal == "rag.file.indexing.gated"
    assert published.payload["layer"] == "index_funnel"


@pytest.mark.asyncio
async def test_index_file_impl_l2_admitted_proceeds_past_gate() -> None:
    from services.rag.rag_service import indexing, state

    source = "/legal/backed.pdf"
    file_path = Path(source)
    gate = EntityAdmissionGate()
    gate._admitted = {source}

    config = RagConfig(
        watch_directories=[WatchDirectory(path="/legal", entity_gated=True)],
        scopes={},
    )

    with (
        patch.object(state, "_config", config),
        patch.object(state, "_entity_admission_gate", gate),
        patch.object(state, "_property_index", None),
        patch.object(state, "_event_bus", None),
        patch(
            "services.rag.rag_service.indexing.asyncio.to_thread",
            return_value=MagicMock(st_mtime_ns=1, st_size=100),
        ),
        patch(
            "services.rag.rag_service.indexing.require_healthy",
            side_effect=RuntimeError("past-gate"),
        ),
    ):
        with pytest.raises(RuntimeError, match="past-gate"):
            await indexing._index_file_impl(
                file_path,
                None,
                None,
                source,
            )


def test_rag_file_indexing_gated_factory_shape() -> None:
    ev = rag_file_indexing_gated(file="/x", layer="watcher_sweep")
    assert ev.signal == "rag.file.indexing.gated"
    assert ev.role == "coordination"


def test_source_paths_endpoint_constant_drift_guard() -> None:
    """Drift-guard: RAG client endpoint must match cortex-api mount.

    cortex-api mounts entities at bare /entities (no /api/v1 prefix).
    If this test fails, the EntityAdmissionGate will 404 on every refresh.
    Fix: update _SOURCE_PATHS_ENDPOINT in _constants.py to match the mount.
    """
    from services.rag.entity_admission._constants import _SOURCE_PATHS_ENDPOINT

    assert _SOURCE_PATHS_ENDPOINT == "/entities/source-paths", (
        f"_SOURCE_PATHS_ENDPOINT={_SOURCE_PATHS_ENDPOINT!r} does not match "
        "cortex-api mount /entities/source-paths — path-prefix drift detected"
    )
