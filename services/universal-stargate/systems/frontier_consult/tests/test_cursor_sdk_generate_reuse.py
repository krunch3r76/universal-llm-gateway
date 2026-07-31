"""AC7–AC8: reuse_thread param for cursor-sdk generate."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    init_db,
)
from agent_bus_store.db.turns import insert_turn

from systems.frontier_consult.cursor_sdk_generate import dispatch_cursor_sdk_generate


@pytest.fixture(autouse=True)
def _bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()


@pytest.mark.asyncio
async def test_reuse_thread_no_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC7: reuse_thread skips create_handoff_thread, posts pointer turn."""
    mock_post = AsyncMock()
    mock_create = AsyncMock()
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        "systems.frontier_consult.handoff.post_pointer_turn",
        mock_post,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.create_handoff_thread",
        mock_create,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_thread_created",
        lambda **kwargs: emitted.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.admit_handoff_dispatch",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        AsyncMock(return_value=(True, None)),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_generate_requested",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        lambda **_kwargs: None,
    )

    await dispatch_cursor_sdk_generate(
        request_id="req-reuse",
        role="cursor-sdk",
        contract="light-bounded",
        model=None,
        subject="subject",
        caller_agent="claude-web",
        packet_path=None,
        message_text="hello",
        reuse_thread="900",
    )

    mock_create.assert_not_called()
    mock_post.assert_awaited_once()
    assert mock_post.await_args.kwargs["thread_id"] == "900"
    assert emitted[0]["reused"] is True
    assert emitted[0]["thread_id"] == "900"


def test_reuse_admit_link_only_on_active() -> None:
    """AC7: admit on active thread inserts link without lifecycle transition."""
    thread_row, *_ = create_thread_with_turn(
        slug="active-reuse",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="cursor-sdk generate",
        body="pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-pending",
        pipeline_id="cursor-sdk-generate",
    )
    insert_turn(
        thread=thread_id,
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="activate",
        body="pointer",
        status="open",
    )

    admitted = admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-reuse",
        pipeline_id="cursor-sdk-generate",
        caller_agent="claude-web",
    )
    assert admitted is not None
    assert admitted["bus_lifecycle_state"] == "active"
    links = admitted["dispatch_links"]
    assert len(links) == 2
    assert any(link["execution_id"] == "exec-reuse" for link in links)


@pytest.mark.asyncio
async def test_create_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC8: reuse_thread=None preserves create path."""
    mock_create = AsyncMock(return_value="new-thread")
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.create_handoff_thread",
        mock_create,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_thread_created",
        lambda **kwargs: emitted.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.admit_handoff_dispatch",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        AsyncMock(return_value=(True, None)),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_generate_requested",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        lambda **_kwargs: None,
    )

    await dispatch_cursor_sdk_generate(
        request_id="req-create",
        role="cursor-sdk",
        contract="light-bounded",
        model=None,
        subject=None,
        caller_agent=None,
        packet_path=None,
        message_text="hello",
        reuse_thread=None,
    )

    mock_create.assert_awaited_once()
    assert emitted[0]["reused"] is False
    assert emitted[0]["thread_id"] == "new-thread"


@pytest.mark.asyncio
async def test_generate_forwards_execution_id_to_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1/AC7: worker dispatch receives execution_id and caller_agent."""
    worker_mock = AsyncMock(return_value=(True, None))

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.create_handoff_thread",
        AsyncMock(return_value="thread-1"),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.admit_handoff_dispatch",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.dispatch_cursor_sdk_worker_message",
        worker_mock,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_generate_requested",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_thread_created",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate.emit_sdk_worker_outcome",
        lambda **_kwargs: None,
    )

    result = await dispatch_cursor_sdk_generate(
        request_id="req-forward",
        role="cursor-sdk",
        contract="light-bounded",
        model=None,
        subject=None,
        caller_agent="claude-web",
        packet_path=None,
        message_text="hello",
    )

    worker_mock.assert_awaited_once()
    assert worker_mock.await_args.kwargs["execution_id"] == result["execution_id"]
    assert worker_mock.await_args.kwargs["caller_agent"] == "claude-web"


def test_worker_dispatch_failed_event_carries_execution_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7: dispatch_failed branch includes execution_id."""
    from systems.frontier_consult.cursor_sdk_generate_signals import (
        emit_sdk_worker_outcome,
    )

    captured: list[dict[str, object]] = []

    def _capture(event: object) -> None:
        captured.append(getattr(event, "payload", {}))

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_generate_signals.publish_frontier_event",
        _capture,
    )

    emit_sdk_worker_outcome(
        request_id="req-fail",
        thread_id="thread-fail",
        execution_id="exec-fail-event",
        worker_ok=False,
        worker_warning="worker unreachable",
    )

    assert captured[0]["execution_id"] == "exec-fail-event"
    assert captured[0]["error"] == "worker unreachable"
