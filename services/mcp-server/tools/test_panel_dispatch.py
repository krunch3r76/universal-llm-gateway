"""Unit tests for panel_dispatch MCP tool admission paths."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tools.panel_dispatch import register_panel_dispatch_tools


class _ToolRecorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.functions[fn.__name__] = fn
            return fn

        return decorator


@pytest.fixture
def panel_dispatch_fn() -> Any:
    recorder = _ToolRecorder()
    register_panel_dispatch_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["panel_dispatch"]


@pytest.fixture(autouse=True)
def _no_agent_bus_stage() -> Any:
    with patch(
        "tools.panel_dispatch._stage_panel_member_turn",
        new=AsyncMock(),
    ):
        yield


@pytest.mark.asyncio
async def test_panel_dispatch_distinct_member_dispatch_keys(
    panel_dispatch_fn: Any,
) -> None:
    relay_calls: list[dict[str, Any]] = []

    async def _capture(body: dict[str, Any]) -> dict[str, Any]:
        relay_calls.append(body)
        role = body.get("role") or str(body["dispatch_thread_id"]).rsplit(":", 1)[-1]
        return {"execution_id": f"exec-{role}"}

    with patch(
        "tools.panel_dispatch._relay_team_dispatch",
        new=AsyncMock(side_effect=_capture),
    ):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-1",
            poll=False,
        )

    keys = {call["dispatch_thread_id"] for call in relay_calls}
    assert keys == {"panel-thread-1:skeptic", "panel-thread-1:reviewer"}
    assert len(relay_calls) == 2
    assert "submission_plan" in result
    plan_keys = {entry["dispatch_key"] for entry in result["submission_plan"]}
    assert plan_keys == keys


@pytest.mark.asyncio
async def test_panel_dispatch_rejects_block_array_without_relay(
    panel_dispatch_fn: Any,
) -> None:
    relay = AsyncMock(return_value={"execution_id": "exec-1"})
    with patch("tools.panel_dispatch._relay_team_dispatch", new=relay):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            dispatch_thread_id="panel-thread-2",
            poll=False,
        )

    relay.assert_not_called()
    assert result["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_panel_dispatch_member_models_forwarded_per_member(
    panel_dispatch_fn: Any,
) -> None:
    """Friction 23301: member_models rebinds roster models end to end."""
    relay_calls: list[dict[str, Any]] = []

    async def _capture(body: dict[str, Any]) -> dict[str, Any]:
        relay_calls.append(body)
        role = body.get("role") or str(body["dispatch_thread_id"]).rsplit(":", 1)[-1]
        return {"execution_id": f"exec-{role}"}

    with patch(
        "tools.panel_dispatch._relay_team_dispatch",
        new=AsyncMock(side_effect=_capture),
    ):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-mm-1",
            poll=False,
            member_models={"reviewer": "openai/gpt-5.5"},
        )

    by_role = {call["role"]: call for call in relay_calls}
    assert by_role["reviewer"]["model"] == "openai/gpt-5.5"
    assert result["member_models"]["reviewer"] == "openai/gpt-5.5"
    assert set(result["panel_families"]) == {"grok-4.6@?", "gpt-5.5@?"}


@pytest.mark.asyncio
async def test_panel_dispatch_rejects_role_keys_in_generation_options(
    panel_dispatch_fn: Any,
) -> None:
    """Friction 23301: role-keyed overrides in generation_options were silently
    ignored; they now reject with a pointer to member_models."""
    relay = AsyncMock(return_value={"execution_id": "exec-1"})
    with patch("tools.panel_dispatch._relay_team_dispatch", new=relay):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-mm-2",
            poll=False,
            generation_options={"skeptic": {"model": "xai/grok-4.6"}},
        )

    relay.assert_not_called()
    assert result["error"]["code"] == "validation_error"
    assert "member_models" in result["error"]["message"]


@pytest.mark.asyncio
async def test_panel_dispatch_poll_envelope_partial(panel_dispatch_fn: Any) -> None:
    async def _capture(body: dict[str, Any]) -> dict[str, Any]:
        role = body.get("role") or str(body["dispatch_thread_id"]).rsplit(":", 1)[-1]
        return {"execution_id": f"exec-{role}"}

    def _poll(execution_id: str, wait_seconds: float) -> dict[str, Any]:
        if execution_id == "exec-skeptic":
            return {"status": "running", "execution_id": execution_id}
        return {
            "status": "completed",
            "execution_id": execution_id,
            "result": {
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
        }

    with (
        patch(
            "tools.panel_dispatch._relay_team_dispatch",
            new=AsyncMock(side_effect=_capture),
        ),
        patch("tools.panel_dispatch._poll_execution", side_effect=_poll),
        patch("tools.panel_dispatch.record") as record_mock,
    ):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-3",
            poll=True,
        )

    assert result["status"] == "partial"
    assert result["do_not_resubmit"] is True
    assert result["in_flight_execution_ids"] == ["exec-skeptic"]
    assert result["member_status"]["skeptic"] == "running"
    assert result["member_status"]["reviewer"] == "complete"
    assert result["tokens_in"] == 1000
    assert result["tokens_out"] == 200

    signals = [call.args[0] for call in record_mock.call_args_list]
    assert "mcp.panel.member.admitted" in signals
    assert "mcp.panel.partial" in signals


@pytest.mark.asyncio
async def test_panel_dispatch_dispatched_status_without_poll(
    panel_dispatch_fn: Any,
) -> None:
    with patch(
        "tools.panel_dispatch._relay_team_dispatch",
        new=AsyncMock(return_value={"execution_id": "exec-1"}),
    ):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-4",
            poll=False,
        )

    assert result["status"] == "dispatched"
    assert result["member_status"]["skeptic"] == "running"
    assert result["member_status"]["reviewer"] == "running"
    assert result["tokens_in"] == 0
    assert result["tokens_out"] == 0


def _sample_stored_envelope() -> dict[str, Any]:
    return {
        "disposition": "panel",
        "panel_executions": {"skeptic": "exec-skeptic", "reviewer": "exec-reviewer"},
        "panel_families": ["grok-4.6@?", "gpt-5.6-terra@medium"],
        "status": "dispatched",
        "member_status": {"skeptic": "running", "reviewer": "running"},
    }


@pytest.mark.asyncio
async def test_idempotency_no_second_admission_on_replay(
    panel_dispatch_fn: Any,
) -> None:
    relay = AsyncMock(return_value={"execution_id": "exec-1"})
    stored = _sample_stored_envelope()

    with (
        patch("tools.panel_dispatch._relay_team_dispatch", new=relay),
        patch(
            "tools.panel_dispatch.check_or_reserve",
            return_value=type(
                "R",
                (),
                {"kind": "hit", "envelope": stored, "age_s": 5.0},
            )(),
        ),
        patch("tools.panel_dispatch.record") as record_mock,
    ):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-idem-1",
            poll=False,
            panel_request_id="req-replay",
        )

    relay.assert_not_called()
    assert result["idempotency_hit"] is True
    assert result["panel_request_id"] == "req-replay"
    signals = [call.args[0] for call in record_mock.call_args_list]
    assert "mcp.panel.dispatch.deduped" in signals
    assert "mcp.panel.dispatch.called" not in signals
    assert "mcp.panel.dispatch.dispatched" not in signals


@pytest.mark.asyncio
async def test_idempotency_repoll_on_hit(panel_dispatch_fn: Any) -> None:
    stored = _sample_stored_envelope()

    def _poll(execution_id: str, wait_seconds: float) -> dict[str, Any]:
        return {
            "status": "completed",
            "execution_id": execution_id,
            "result": {"usage": {"prompt_tokens": 50, "completion_tokens": 10}},
        }

    with (
        patch("tools.panel_dispatch._relay_team_dispatch", new=AsyncMock()),
        patch(
            "tools.panel_dispatch.check_or_reserve",
            return_value=type(
                "R",
                (),
                {"kind": "hit", "envelope": dict(stored), "age_s": 3.0},
            )(),
        ),
        patch("tools.panel_dispatch._poll_execution", side_effect=_poll),
        patch("tools.panel_dispatch.record") as record_mock,
    ):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-idem-2",
            poll=True,
            panel_request_id="req-repoll",
        )

    assert result["idempotency_hit"] is True
    assert result["status"] == "complete"
    assert result["member_status"]["skeptic"] == "complete"
    deduped_calls = [
        c
        for c in record_mock.call_args_list
        if c.args[0] == "mcp.panel.dispatch.deduped"
    ]
    assert deduped_calls[0].kwargs.get("repolled") is True


@pytest.mark.asyncio
async def test_idempotency_conflict_on_key_reuse(panel_dispatch_fn: Any) -> None:
    relay = AsyncMock(return_value={"execution_id": "exec-1"})

    with (
        patch("tools.panel_dispatch._relay_team_dispatch", new=relay),
        patch(
            "tools.panel_dispatch.check_or_reserve",
            return_value=type("R", (), {"kind": "conflict"})(),
        ),
        patch("tools.panel_dispatch.record") as record_mock,
    ):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "changed message"}],
            dispatch_thread_id="panel-thread-idem-3",
            poll=False,
            panel_request_id="req-conflict",
        )

    relay.assert_not_called()
    assert result["error"]["code"] == "validation_error"
    assert "non-equivalent inputs" in result["error"]["message"]
    rejected = [
        c
        for c in record_mock.call_args_list
        if c.args[0] == "mcp.panel.dispatch.rejected"
    ]
    assert rejected[0].kwargs.get("reason") == "idempotency_conflict"


@pytest.mark.asyncio
async def test_idempotency_no_key_no_dedupe(panel_dispatch_fn: Any) -> None:
    relay = AsyncMock(return_value={"execution_id": "exec-1"})

    with patch("tools.panel_dispatch._relay_team_dispatch", new=relay):
        await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-idem-4",
            poll=False,
        )
        await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-idem-4",
            poll=False,
        )

    assert relay.call_count == 4


@pytest.mark.asyncio
async def test_idempotency_failed_dispatch_releases_reservation(
    panel_dispatch_fn: Any,
) -> None:
    relay = AsyncMock(return_value={"error": {"code": "dispatch_error"}})

    with patch("tools.panel_dispatch._relay_team_dispatch", new=relay):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-idem-5",
            poll=False,
            panel_request_id="req-fail",
        )

    assert not result.get("panel_executions")
    from agent_seat import panel_idempotency as pidem

    pidem._store.clear()
    with patch("tools.panel_dispatch._relay_team_dispatch", new=relay):
        result2 = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-idem-5",
            poll=False,
            panel_request_id="req-fail",
        )
    assert result2.get("idempotency_hit") is not True


@pytest.mark.asyncio
async def test_idempotency_source_ref_error_releases_reservation(
    panel_dispatch_fn: Any,
) -> None:
    from implement_admission.source_ref import SourceRefError

    relay = AsyncMock(return_value={"execution_id": "exec-1"})

    with (
        patch("tools.panel_dispatch._relay_team_dispatch", new=relay),
        patch(
            "tools.panel_dispatch.read_packet",
            side_effect=SourceRefError(
                code="not_found",
                source_ref="tmp/prompts/missing.md",
                rule="read",
                message="missing packet",
            ),
        ),
    ):
        result = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-idem-6",
            poll=False,
            panel_request_id="req-src",
            source_ref="tmp/prompts/missing.md",
        )

    relay.assert_not_called()
    assert result["error"]["code"] == "validation_error"

    from agent_seat import panel_idempotency as pidem

    pidem._store.clear()
    with patch(
        "tools.panel_dispatch._relay_team_dispatch",
        new=relay,
    ):
        result2 = await panel_dispatch_fn(
            messages=[{"role": "user", "content": "review this"}],
            dispatch_thread_id="panel-thread-idem-6",
            poll=False,
            panel_request_id="req-src",
        )
    assert result2.get("idempotency_hit") is not True
    relay.assert_called()
