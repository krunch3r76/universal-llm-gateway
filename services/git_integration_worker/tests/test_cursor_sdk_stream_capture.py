"""Unit tests for observe_run_stream (friction 21654 fix #1).

Locks in the stream-capture keystone ahead of the manual 3871 re-dispatch:
a call that reaches a terminal status mid-stream is emitted once, a call
still ``running`` at end-of-stream is flushed, and ``truncated`` fields
survive into the observation. See addendum note
``cortex:notes/system/threads/3880-cursor-preamble-interaction-note.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from services.git_integration_worker import cursor_sdk_stream_capture as capture_mod
from services.git_integration_worker.cursor_sdk_stream_capture import (
    StreamCapture,
    aggregate_stream_usage,
    finalize_request_id_capture,
    finalize_stream_capture_usage,
    normalize_usage_map,
    observe_run_stream,
)


@dataclass
class _FakeToolCallMessage:
    call_id: str
    name: str
    status: str
    args: Any = None
    result: Any = None
    truncated: dict[str, bool] | None = None
    type: str = "tool_call"


@dataclass
class _FakeStatusMessage:
    type: str = "status"


@dataclass
class _FakeTurnEndedMessage:
    type: str = "turn-ended"
    usage: dict[str, Any] | None = None


@dataclass
class _FakeTokenDeltaMessage:
    type: str = "token-delta"
    tokens: int = 0


@dataclass
class _FakeUsageMessage:
    type: str = "usage"
    usage: Any = None


@dataclass
class _FakeRun:
    messages: list[Any]
    raise_after: int | None = None
    emitted: list[Any] = field(default_factory=list)

    def stream(self):
        for i, message in enumerate(self.messages):
            if self.raise_after is not None and i == self.raise_after:
                raise RuntimeError("stream disconnected")
            yield message


@pytest.fixture(autouse=True)
def _capture_emitted(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    emitted: list[Any] = []
    monkeypatch.setattr(
        capture_mod, "emit_frontier_event", lambda event: emitted.append(event)
    )
    return emitted


@dataclass
class _FakeStreamEvent:
    sdk_message: Any = None
    interaction_update: Any = None


@dataclass
class _FakeRunWithEvents:
    events_list: list[Any]

    def events(self):
        return iter(self.events_list)

    def stream(self):
        return iter([])


def test_turn_ended_on_interaction_update_events_path(
    _capture_emitted: list[Any],
) -> None:
    run = _FakeRunWithEvents(
        events_list=[
            _FakeStreamEvent(
                interaction_update=_FakeTurnEndedMessage(
                    usage={"input_tokens": 12, "output_tokens": 8}
                )
            ),
        ]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.usage == {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
    assert result.usage_capture_status == "captured"


def test_token_delta_on_interaction_update_events_path(
    _capture_emitted: list[Any],
) -> None:
    run = _FakeRunWithEvents(
        events_list=[_FakeStreamEvent(interaction_update=_FakeTokenDeltaMessage(tokens=99))]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.usage == {"total_tokens": 99}
    assert result.usage_capture_status == "partial"


def test_tool_call_on_sdk_message_events_path(_capture_emitted: list[Any]) -> None:
    run = _FakeRunWithEvents(
        events_list=[
            _FakeStreamEvent(
                sdk_message=_FakeToolCallMessage(
                    call_id="c-ev",
                    name="shell",
                    status="completed",
                    result={"stdout": "ok"},
                )
            ),
        ]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.tool_call_count == 1
    assert len(_capture_emitted) == 1


def test_emits_once_per_call_on_terminal_status(_capture_emitted: list[Any]) -> None:
    run = _FakeRun(
        messages=[
            _FakeToolCallMessage(call_id="c1", name="fs", status="running"),
            _FakeStatusMessage(),
            _FakeToolCallMessage(
                call_id="c1",
                name="fs",
                status="completed",
                args={"op": "write"},
                result={"ok": True},
            ),
        ]
    )

    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )

    assert result.tool_call_count == 1
    assert result.tool_calls[0].call_id == "c1"
    assert result.tool_calls[0].status == "completed"
    assert not result.tool_calls[0].truncated_any
    assert len(_capture_emitted) == 1


def test_truncated_fields_survive_into_observation(_capture_emitted: list[Any]) -> None:
    run = _FakeRun(
        messages=[
            _FakeToolCallMessage(
                call_id="c2",
                name="fs",
                status="error",
                args={"op": "write", "content": "x" * 200_000},
                result=None,
                truncated={"content": True, "result": False},
            ),
        ]
    )

    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )

    assert result.tool_call_count == 1
    observation = result.tool_calls[0]
    assert observation.truncated_any
    assert observation.truncated_fields == ("content",)
    assert observation.arg_bytes > 0
    assert len(result.truncated_tool_calls) == 1


def test_still_running_call_is_flushed_at_end_of_stream(
    _capture_emitted: list[Any],
) -> None:
    run = _FakeRun(
        messages=[
            _FakeToolCallMessage(call_id="c3", name="fs", status="running"),
        ]
    )

    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )

    assert result.tool_call_count == 1
    assert result.tool_calls[0].status == "running"
    assert len(_capture_emitted) == 1


def test_stream_interruption_returns_partial_capture_without_raising(
    _capture_emitted: list[Any],
) -> None:
    run = _FakeRun(
        messages=[
            _FakeToolCallMessage(
                call_id="c4", name="fs", status="completed", result={"ok": True}
            ),
            _FakeToolCallMessage(call_id="c5", name="fs", status="running"),
        ],
        raise_after=1,
    )

    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )

    # c4 completed and was emitted before the mid-stream disconnect; c5 was
    # never observed (the disconnect happened before it was yielded).
    assert result.tool_call_count == 1
    assert result.tool_calls[0].call_id == "c4"


def test_missing_stream_method_degrades_to_empty_capture(
    _capture_emitted: list[Any],
) -> None:
    class _NoStreamRun:
        pass

    result = observe_run_stream(
        _NoStreamRun(), dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )

    assert result.tool_call_count == 0
    assert result.tool_calls == ()
    assert _capture_emitted == []


def test_mcp_wire_name_resolves_to_logical_tool_in_observation(
    _capture_emitted: list[Any],
) -> None:
    """Production stream shape: message.name=mcp, logical tool in args.toolName."""
    run = _FakeRun(
        messages=[
            _FakeToolCallMessage(
                call_id="c-mcp-cortex",
                name="mcp",
                status="completed",
                args={
                    "providerIdentifier": "user-vortex",
                    "toolName": "cortex",
                    "args": {"tool": "assert", "entity_id": "todo:ac9g-live-falsifier"},
                },
                result={"status": "success", "value": {"item": {"id": 27486}}},
            ),
        ]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.tool_calls[0].tool_name == "cortex"
    assert _capture_emitted[0].payload["tool_name"] == "cortex"


def test_target_path_parsed_from_write_args() -> None:
    run = _FakeRun(
        messages=[
            _FakeToolCallMessage(
                call_id="c-path",
                name="write",
                status="completed",
                args={"path": "services/stream_only.py"},
            ),
        ]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.tool_calls[0].target_path == "services/stream_only.py"


def test_target_path_skipped_for_fs_read() -> None:
    run = _FakeRun(
        messages=[
            _FakeToolCallMessage(
                call_id="c-read",
                name="fs",
                status="completed",
                args={"op": "read", "path": "notes/foo.md"},
            ),
        ]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.tool_calls[0].target_path is None


def test_turn_ended_usage_normalized_on_stream(_capture_emitted: list[Any]) -> None:
    run = _FakeRun(
        messages=[
            _FakeTurnEndedMessage(
                usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
            ),
        ]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.usage == {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    assert result.usage_capture_status == "captured"


def test_mixed_turn_usage_is_partial(_capture_emitted: list[Any]) -> None:
    run = _FakeRun(
        messages=[
            _FakeTurnEndedMessage(usage={"input_tokens": 10, "output_tokens": 5}),
            _FakeTurnEndedMessage(usage=None),
        ]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert result.usage_capture_status == "partial"


def test_token_delta_fallback_when_turn_usage_missing(
    _capture_emitted: list[Any],
) -> None:
    run = _FakeRun(messages=[_FakeTokenDeltaMessage(tokens=42)])
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.usage == {"total_tokens": 42}
    assert result.usage_capture_status == "partial"


def test_missing_usage_degrades_without_zeroing() -> None:
    usage, status = aggregate_stream_usage(turn_usages=(), token_delta_sum=0)
    assert usage is None
    assert status == "missing"


def test_normalize_usage_map_handles_prompt_completion_aliases() -> None:
    normalized, mappable = normalize_usage_map(
        {"prompt_tokens": 7, "completion_tokens": 3}
    )
    assert mappable is True
    assert normalized == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
        "_total_derived": True,
    }


def test_normalize_usage_map_handles_camel_case_bridge_keys() -> None:
    normalized, mappable = normalize_usage_map(
        {
            "inputTokens": 100,
            "outputTokens": 50,
            "cacheReadTokens": 10,
            "cacheWriteTokens": 2,
            "totalTokens": 162,
        }
    )
    assert mappable is True
    assert normalized == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_write_tokens": 2,
        "total_tokens": 162,
    }


def test_opaque_usage_keys_mark_partial() -> None:
    usage, status = aggregate_stream_usage(
        turn_usages=({"opaque_meter": {"nested": True}},),
        token_delta_sum=0,
    )
    assert status == "partial"
    assert usage == {"usage_raw": {"opaque_meter": {"nested": True}}}


def test_sdk_usage_message_on_sdk_message_events_path(
    _capture_emitted: list[Any],
) -> None:
    run = _FakeRunWithEvents(
        events_list=[
            _FakeStreamEvent(
                sdk_message=_FakeUsageMessage(
                    usage={"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
                )
            ),
        ]
    )
    result = observe_run_stream(
        run, dispatch_id="d1", thread_id="t1", resolved_model="composer-2.5"
    )
    assert result.usage == {
        "input_tokens": 20,
        "output_tokens": 10,
        "total_tokens": 30,
    }
    assert result.usage_capture_status == "captured"


def test_finalize_stream_capture_usage_from_run_wait() -> None:
    capture = StreamCapture(tool_calls=(), usage=None, usage_capture_status="missing")

    @dataclass
    class _FakeTokenUsage:
        input_tokens: int
        output_tokens: int
        total_tokens: int
        cache_read_tokens: int = 0
        cache_write_tokens: int = 0

    @dataclass
    class _FakeRunResult:
        usage: Any

    finalized = finalize_stream_capture_usage(
        capture,
        run=_FakeRunResult(usage=_FakeTokenUsage(100, 50, 150, 20, 5)),
        result=_FakeRunResult(usage=None),
    )
    assert finalized.usage == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 20,
        "cache_write_tokens": 5,
        "total_tokens": 150,
    }
    assert finalized.usage_capture_status == "captured"


def test_finalize_prefers_post_wait_and_marks_reconciled_delta() -> None:
    capture = StreamCapture(
        tool_calls=(),
        usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        usage_capture_status="captured",
    )

    @dataclass
    class _FakeRunResult:
        usage: dict[str, int]

    finalized = finalize_stream_capture_usage(
        capture,
        run=_FakeRunResult(
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 10,
                "total_tokens": 160,
            }
        ),
    )
    assert finalized.usage == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "total_tokens": 160,
    }
    assert finalized.usage_capture_status == "reconciled_delta"


def test_finalize_prefers_captured_when_post_wait_has_total_over_stream_partial() -> None:
    """R finding #3 — authoritative post-wait with total is not understated as partial."""
    capture = StreamCapture(
        tool_calls=(),
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        usage_capture_status="partial",
    )

    @dataclass
    class _FakeRunResult:
        usage: dict[str, int]

    finalized = finalize_stream_capture_usage(
        capture,
        run=_FakeRunResult(
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        ),
    )
    assert finalized.usage_capture_status == "captured"
    assert finalized.usage == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_finalize_skips_reconciled_delta_when_stream_total_was_recomputed() -> None:
    """R finding #1 — do not compare recomputed stream total vs wire post-wait."""
    stream_usage, _status = normalize_usage_map(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 10,
            # no wire total → recompute 25 + _total_derived
        }
    )
    assert stream_usage is not None
    assert stream_usage.get("_total_derived") is True
    assert stream_usage["total_tokens"] == 25

    capture = StreamCapture(
        tool_calls=(),
        usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 10,
            "total_tokens": 25,
        },
        usage_capture_status="captured",
        usage_total_derived=True,
    )

    @dataclass
    class _FakeRunResult:
        usage: dict[str, int]

    finalized = finalize_stream_capture_usage(
        capture,
        run=_FakeRunResult(
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_tokens": 10,
                "total_tokens": 15,  # wire total ≠ recomputed 25
            }
        ),
    )
    assert finalized.usage_capture_status == "captured"
    assert finalized.usage == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 10,
        "total_tokens": 15,
    }
    assert "_total_derived" not in (finalized.usage or {})


@dataclass
class _FakeRequestMessage:
    type: str = "request"
    request_id: str = ""


def test_observe_run_stream_request_id_source_stream() -> None:
    run = _FakeRunWithEvents(
        events_list=[
            _FakeStreamEvent(
                sdk_message=_FakeRequestMessage(request_id="req-stream-abc")
            ),
        ]
    )
    capture = observe_run_stream(
        run, dispatch_id="d-req", thread_id="t-req", resolved_model="composer-2.5"
    )
    assert capture.sdk_request_id == "req-stream-abc"
    assert capture.request_id_source == "stream"


def test_finalize_request_id_post_wait_from_result() -> None:
    capture = StreamCapture(tool_calls=())
    finalized = finalize_request_id_capture(
        capture,
        run=type("Run", (), {"request_id": "run-id"})(),
        result=type("Result", (), {"request_id": "result-id"})(),
    )
    assert finalized.sdk_request_id == "result-id"
    assert finalized.request_id_source == "post_wait"


def test_finalize_request_id_post_wait_from_run_when_result_missing() -> None:
    capture = StreamCapture(tool_calls=())
    finalized = finalize_request_id_capture(
        capture,
        run=type("Run", (), {"request_id": "run-only"})(),
        result=type("Result", (), {})(),
    )
    assert finalized.sdk_request_id == "run-only"
    assert finalized.request_id_source == "post_wait"


def test_finalize_request_id_stream_precedence() -> None:
    capture = StreamCapture(
        tool_calls=(),
        sdk_request_id="stream-id",
        request_id_source="stream",
    )
    finalized = finalize_request_id_capture(
        capture,
        run=type("Run", (), {"request_id": "run-id"})(),
        result=type("Result", (), {"request_id": "result-id"})(),
    )
    assert finalized.sdk_request_id == "stream-id"
    assert finalized.request_id_source == "stream"


def test_finalize_request_id_absent_when_both_miss() -> None:
    capture = StreamCapture(tool_calls=())
    finalized = finalize_request_id_capture(
        capture,
        run=type("Run", (), {})(),
        result=type("Result", (), {})(),
    )
    assert finalized.sdk_request_id is None
    assert finalized.request_id_source is None


def test_request_id_from_sdk_error() -> None:
    from services.git_integration_worker.cursor_sdk_stream_capture import (
        request_id_from_sdk_error,
    )

    exc = type("CursorSDKError", (Exception,), {"request_id": "err-req-1"})()
    request_id, source = request_id_from_sdk_error(exc)
    assert request_id == "err-req-1"
    assert source == "error"
