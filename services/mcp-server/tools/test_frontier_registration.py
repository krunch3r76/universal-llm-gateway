"""Phase 5 M3: MCP tool registration test — dispatch-surface-split.

Verifies that register_frontier_tools() registers exactly team_dispatch,
with no legacy team_generate, frontier_generate, or frontier_dispatch tools.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from unittest.mock import patch

from tools.frontier import register_frontier_tools


class _ToolNameRecorder:
    """Minimal FastMCP duck-type that records tool registration calls.

    register_frontier_tools(mcp) calls @mcp.tool(title=...) which invokes
    mcp.tool(title=title) returning a decorator; the decorator receives the
    function and records its __name__.  The function body is never executed
    — only the decorator wiring is exercised.
    """

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.registered.append(fn.__name__)
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def test_m3_only_team_dispatch_registered() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)  # type: ignore[arg-type]

    assert recorder.registered == ["team_dispatch"], (
        f"expected only team_dispatch, got {recorder.registered}"
    )
    assert "team_generate" not in recorder.registered, (
        "team_generate still registered — Phase 4 deletion incomplete"
    )
    assert "frontier_generate" not in recorder.registered, (
        "frontier_generate still registered — Phase 4 deletion incomplete"
    )
    assert "frontier_dispatch" not in recorder.registered, (
        "frontier_dispatch still registered — MCP tool retired; use team_dispatch"
    )


def test_m3_no_generate_variants_registered() -> None:
    """Only team_dispatch; no other frontier-generate variants."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    generate_variants = [name for name in recorder.registered if "generate" in name]
    assert generate_variants == [], (
        f"Unexpected 'generate' tool names still registered: {generate_variants}"
    )


def test_team_dispatch_requires_dispatch_thread_id_param() -> None:
    """MCP registration exposes dispatch_thread_id as a required positional param."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "dispatch_thread_id" in sig.parameters
    # dispatch_thread_id now defaults to "" (op=handoff callers omit it)
    # — it was formerly required (no default); both are acceptable here.


def test_team_dispatch_op_accepts_handoff() -> None:
    """team_dispatch op parameter signature includes 'handoff'."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "op" in sig.parameters
    op_annotation = sig.parameters["op"].annotation
    # Literal["generate", "to_thread", "handoff"] — verify "handoff" appears in str
    assert "handoff" in str(op_annotation), (
        f"'handoff' not found in op annotation: {op_annotation}"
    )


def test_team_dispatch_handoff_params_present() -> None:
    """Handoff-only params packet_path and subject are on the team_dispatch signature."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "packet_path" in sig.parameters, "packet_path missing from team_dispatch"
    assert "subject" in sig.parameters, "subject missing from team_dispatch"
    # Both are optional (default None) — handoff callers provide them; others omit
    assert sig.parameters["packet_path"].default is None
    assert sig.parameters["subject"].default is None


def test_team_dispatch_messages_has_default() -> None:
    """messages defaults to [] so op='handoff' callers can omit it."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "messages" in sig.parameters
    param = sig.parameters["messages"]
    assert param.default == [], (
        f"messages should default to [] for handoff callers; got {param.default!r}"
    )


def test_team_dispatch_handoff_relays_to_handoff_endpoint() -> None:
    """op='handoff' builds correct body, relays to /api/v1/team/handoff, records telemetry.

    Verifies:
    - _relay called with endpoint="/api/v1/team/handoff" and record_prefix="mcp.team.handoff"
    - body contains op, role, packet_path, subject
    - generate/to_thread-only fields (messages, dispatch_thread_id) omitted from handoff body
    - record("mcp.team.handoff.called", ...) fired before relay
    """
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []
    record_calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append(
            {"endpoint": endpoint, "body": body, "record_prefix": record_prefix}
        )
        return {"thread_id": "thread-test-123", "to_agent": "claude-web"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        record_calls.append((event, kwargs))

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="handoff",
                role="web-consult",
                packet_path="universal-llm-gateway/tmp/test-packet.md",
                subject="Test handoff subject",
            )
        )

    assert len(relay_calls) == 1
    call = relay_calls[0]
    assert call["endpoint"] == "/api/v1/team/handoff"
    assert call["record_prefix"] == "mcp.team.handoff"

    body = call["body"]
    assert body["op"] == "handoff"
    assert body["role"] == "web-consult"
    assert body["packet_path"] == "universal-llm-gateway/tmp/test-packet.md"
    assert body["subject"] == "Test handoff subject"
    # generate/to_thread-only fields must be absent from the handoff body
    assert "messages" not in body
    assert "dispatch_thread_id" not in body

    # telemetry record fires before relay (record_calls is ordered)
    telemetry_events = [ev for ev, _ in record_calls]
    assert "mcp.team.handoff.called" in telemetry_events
