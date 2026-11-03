"""Park-resume silence and MCP handshake fast-fail (friction a:41850).

Fresh runs keep the outer idle budget. A park-resumed run with no successful
tool call fails at ``CURSOR_SDK_RESUME_NO_TOOLCALL_S`` (default 300). A failed
MCP handshake on that path fails immediately with its own code.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_closeout.bus_body_budget import (
    MAX_TURN_BODY_CHARS,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest
from services.git_integration_worker.tests.test_cursor_sdk_route import (
    _completed_observation,
    _make_controller,
    _mock_bus,
)


def _envelope(body: str) -> dict[str, Any]:
    raw = body
    if "```json" in body:
        raw = body.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    return json.loads(raw)


def _park_resume_request(dispatch_id: str) -> CursorDispatchRequest:
    return CursorDispatchRequest(
        thread_id="41850",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="continue",
        admitted_via="giw_park_resume",
        handoff_contract="none",
    )


def _install_fake_idle_clock(
    monkeypatch: pytest.MonkeyPatch,
    route_mod: Any,
    clock: dict[str, float],
) -> None:
    original = route_mod._LiveToolCallCounter

    class _ClockCounter(original):
        def __init__(self, *, now_fn: Any = None) -> None:
            super().__init__(now_fn=now_fn or (lambda: clock["t"]))

    async def _jump(
        fs: set[asyncio.Task[Any]], *, timeout: float | None = None
    ) -> tuple[set[asyncio.Task[Any]], set[asyncio.Task[Any]]]:
        # Yield so a worker that fails in-thread (handshake) is visible
        # before the fake clock burns the idle budget.
        await asyncio.sleep(0.01)
        done = {task for task in fs if task.done()}
        if done:
            return done, fs - done
        clock["t"] += float(timeout or 0.0)
        done = {task for task in fs if task.done()}
        return done, fs - done

    monkeypatch.setattr(route_mod, "_LiveToolCallCounter", _ClockCounter)
    monkeypatch.setattr(route_mod.asyncio, "wait", _jump)
    monkeypatch.setattr(route_mod, "_SDK_TIMEOUT_S", 1800.0)
    monkeypatch.setattr(route_mod, "_SDK_TIMEOUT_BUFFER_S", 120.0)
    monkeypatch.setattr(route_mod, "_JUDGMENT_IDLE_BUDGET_S", 1800.0)
    monkeypatch.setenv("CURSOR_SDK_RESUME_NO_TOOLCALL_S", "300")
    monkeypatch.setattr(route_mod, "emit_sdk_worker_timeout", lambda **_k: None)
    monkeypatch.setattr(route_mod, "emit_sdk_worker_orphaned", lambda **_k: None)
    monkeypatch.setattr(route_mod, "mark_dispatch_orphaned", lambda **_k: None)
    monkeypatch.setattr(route_mod, "abort_orphaned_bridge", lambda **_k: False)
    monkeypatch.setattr(route_mod, "_terminate_link", _async_none)
    monkeypatch.setattr(route_mod, "_mark_terminal_and_promote", _async_none)
    monkeypatch.setattr(route_mod, "persist_timeout_retain", lambda **_k: None)


async def _async_none(*_a: object, **_k: object) -> None:
    return None


async def _drop_slot(dispatch_id: str) -> None:
    from services.git_integration_worker.cursor_sdk_gate import (
        force_release_sdk_dispatch_slot,
    )

    await force_release_sdk_dispatch_slot(dispatch_id=dispatch_id)


def _hold_worker(release: threading.Event) -> Any:
    def _slow(**_kwargs: object) -> SdkRunOutcome:
        release.wait(timeout=30.0)
        return SdkRunOutcome(
            body="late",
            status="finished",
            duration_ms=1,
            tool_call_count=0,
        )

    return _slow


@pytest.mark.asyncio
async def test_park_resume_no_successful_toolcall_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero successful tool calls on giw_park_resume fail at N, not 1920s."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    clock = {"t": 0.0}
    release = threading.Event()
    _install_fake_idle_clock(monkeypatch, route_mod, clock)
    monkeypatch.setattr(route_mod, "_run_sdk_sync", _hold_worker(release))
    req = _park_resume_request("disp-resume-silent")
    bus = _mock_bus()
    try:
        await route_mod._run_sdk_dispatch_gated(
            req=req,
            ctx=_ctx_for(route_mod, req),
            bus=bus,
            controller=_make_controller(),
        )
    finally:
        release.set()
        await _drop_slot(req.dispatch_id)

    env = _envelope(bus.reply.await_args.kwargs["body"])
    assert env["code"] == "CURSOR_SDK_RESUME_NO_TOOLCALL"
    assert clock["t"] <= 330.0
    assert clock["t"] >= 300.0
    message = env["message"]
    assert "first_toolcall_s=" in message
    assert "mcp_handshake=" in message
    assert "admitted_via=giw_park_resume" in message
    assert env["data"]["first_toolcall_s"] is None
    assert env["data"]["stream_tool_call_count"] == 0
    assert env["data"]["mcp_handshake"] in {"ok", "not_observed"} or str(
        env["data"]["mcp_handshake"]
    ).startswith("failed:")


@pytest.mark.asyncio
async def test_fresh_run_without_toolcalls_keeps_full_idle_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh run still dies as CURSOR_SDK_TIMEOUT at the 1920s budget."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    clock = {"t": 0.0}
    release = threading.Event()
    _install_fake_idle_clock(monkeypatch, route_mod, clock)
    monkeypatch.setattr(route_mod, "_run_sdk_sync", _hold_worker(release))
    req = CursorDispatchRequest(
        thread_id="41850-fresh",
        model="cursor/composer-2.5",
        dispatch_id="disp-fresh-silent",
        execution_id="exec-fresh-silent",
        message="hello",
        handoff_contract="none",
    )
    bus = _mock_bus()
    try:
        await route_mod._run_sdk_dispatch_gated(
            req=req,
            ctx=_ctx_for(route_mod, req),
            bus=bus,
            controller=_make_controller(),
        )
    finally:
        release.set()
        await _drop_slot(req.dispatch_id)

    env = _envelope(bus.reply.await_args.kwargs["body"])
    assert env["code"] == "CURSOR_SDK_TIMEOUT"
    assert clock["t"] >= 1920.0
    assert "budget 1920s" in env["message"]
    assert "mcp_handshake=" not in env["message"]
    assert env["data"]["first_toolcall_s"] is None
    assert "stream_tool_call_count" in env["data"]


@pytest.mark.asyncio
async def test_park_resume_mcp_handshake_failure_fails_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed MCP handshake on park-resume is its own code, not the idle budget."""
    from services.git_integration_worker import cursor_sdk_resume as resume_mod
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    assert hasattr(resume_mod, "ResumeMcpHandshakeError")
    clock = {"t": 0.0}
    _install_fake_idle_clock(monkeypatch, route_mod, clock)

    def _fail(**_kwargs: object) -> SdkRunOutcome:
        raise resume_mod.ResumeMcpHandshakeError("failed:stdio_exited")

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _fail)
    req = _park_resume_request("disp-resume-handshake")
    bus = _mock_bus()
    try:
        await route_mod._run_sdk_dispatch_gated(
            req=req,
            ctx=_ctx_for(route_mod, req),
            bus=bus,
            controller=_make_controller(),
        )
    finally:
        await _drop_slot(req.dispatch_id)
    env = _envelope(bus.reply.await_args.kwargs["body"])
    assert env["code"] == "CURSOR_SDK_RESUME_MCP_HANDSHAKE"
    assert clock["t"] < 300.0
    assert "mcp_handshake=failed:stdio_exited" in env["message"]
    assert "first_toolcall_s=" in env["message"]
    assert "admitted_via=giw_park_resume" in env["message"]


def test_resume_agent_applies_options_and_handshake_fail_skips_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resume_agent receives rebuilt options; a failed handshake does not send."""
    from services.git_integration_worker import cursor_sdk_resume as resume_mod
    from services.git_integration_worker.cursor_sdk_resume import (
        ResumeRunContext,
        start_or_resume_agent,
    )

    observed: list[str] = []

    def _fail(**_kwargs: object) -> str:
        observed.append("called")
        return "failed:stdio_exited"

    monkeypatch.setattr(resume_mod, "observe_resume_mcp_handshake", _fail)
    client = MagicMock()
    agent = MagicMock(agent_id="agent-1")
    client.resume_agent.return_value = agent
    options = MagicMock()
    ctx = ResumeRunContext(
        resume_of="parent",
        state_root="/tmp/state",
        sdk_agent_id="agent-parent",
    )
    with pytest.raises(resume_mod.ResumeMcpHandshakeError, match="failed:stdio_exited"):
        start_or_resume_agent(
            client=client,
            agent_options=options,
            prompt="continue",
            resume_ctx=ctx,
            fail_on_mcp_handshake=True,
        )
    client.resume_agent.assert_called_once_with("agent-parent", options)
    agent.send.assert_not_called()
    assert observed == ["called"]


def test_classify_mcp_handshake_ignores_bridge_discovery_line() -> None:
    from services.git_integration_worker.cursor_sdk_resume import (
        classify_mcp_handshake_text,
    )

    assert (
        classify_mcp_handshake_text("cursor-sdk-bridge ready token=abc\n")
        == "not_observed"
    )
    assert classify_mcp_handshake_text("") == "not_observed"
    assert (
        classify_mcp_handshake_text("MCP server exited before initialize\n")
        == "failed:mcp_server_exited"
    )


def test_first_toolcall_s_recorded_on_normal_run() -> None:
    """A completed tool call stamps first_toolcall_s on the counter and closeout."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    clock = {"t": 0.0}
    counter = route_mod._LiveToolCallCounter(now_fn=lambda: clock["t"])
    clock["t"] = 4.0
    counter.note_progress(_completed_observation())
    assert counter.first_toolcall_s() == 4.0
    assert counter.value() == 1

    outcome = SdkRunOutcome(
        body="status: complete — done",
        status="finished",
        duration_ms=4000,
        tool_call_count=1,
        first_toolcall_s=counter.first_toolcall_s(),
        stream_tool_call_count=counter.value(),
    )
    body = build_implement_closeout_body(
        dispatch_id="d-normal",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("d-normal"),
        result_bytes=4,
        thread_id="t-normal",
        work_item_ref=None,
        sidecar_markdown="status: complete — done",
    )
    payload = json.loads(body)
    assert payload["first_toolcall_s"] == 4.0
    assert payload["stream_tool_call_count"] == 1
    assert len(body) <= MAX_TURN_BODY_CHARS


def _ctx_for(route_mod: Any, req: CursorDispatchRequest) -> Any:
    from services.git_integration_worker.tests.conftest import _ctx

    return _ctx(
        route_mod._CONFIG.source_repo,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        contract=req.handoff_contract or "none",
    )
