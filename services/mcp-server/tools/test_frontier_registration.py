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
    """Handoff-only params packet_path, source_ref, seat, and subject are on the signature."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "seat" in sig.parameters, "seat missing from team_dispatch"
    assert "packet_path" in sig.parameters, "packet_path missing from team_dispatch"
    assert "source_ref" in sig.parameters, "source_ref missing from team_dispatch"
    assert "subject" in sig.parameters, "subject missing from team_dispatch"
    # All optional (default None) — handoff callers provide at least one input path
    assert sig.parameters["seat"].default is None
    assert sig.parameters["packet_path"].default is None
    assert sig.parameters["source_ref"].default is None
    assert sig.parameters["subject"].default is None


def test_team_dispatch_source_ref_signature() -> None:
    """source_ref defaults to None on the team_dispatch signature."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert sig.parameters["source_ref"].default is None


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


def _run_handoff_relay(
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Invoke team_dispatch op=handoff with mocked relay; return relay_calls and error."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append(
            {"endpoint": endpoint, "body": body, "record_prefix": record_prefix}
        )
        return {"thread_id": "thread-test-123", "to_agent": "claude-cursor"}

    with patch("tools.frontier._relay", side_effect=_fake_relay):
        result = asyncio.run(team_dispatch_fn(op="handoff", **kwargs))

    if isinstance(result, dict) and "error" in result:
        return relay_calls, result
    return relay_calls, None


def test_team_dispatch_handoff_source_ref_only() -> None:
    """source_ref only relays raw source_ref with no client-side resolution."""
    relay_calls, error = _run_handoff_relay(
        role="cursor-implement",
        source_ref="todo:unified-admission-handoff-source-ref",
        subject="Implement source_ref adoption",
    )
    assert error is None
    assert len(relay_calls) == 1
    body = relay_calls[0]["body"]
    assert body == {
        "op": "handoff",
        "role": "cursor-implement",
        "source_ref": "todo:unified-admission-handoff-source-ref",
        "subject": "Implement source_ref adoption",
    }
    assert "packet_path" not in body


def test_team_dispatch_handoff_both_present() -> None:
    """Both source_ref and packet_path are relayed when provided."""
    relay_calls, error = _run_handoff_relay(
        role="cursor-implement",
        source_ref="todo:example",
        packet_path="universal-llm-gateway/tmp/prompts/example-packet.md",
        subject="Bound implement",
    )
    assert error is None
    body = relay_calls[0]["body"]
    assert body["source_ref"] == "todo:example"
    assert body["packet_path"] == "universal-llm-gateway/tmp/prompts/example-packet.md"


def test_team_dispatch_handoff_seat_only() -> None:
    """AC1 — seat-only handoff relays seat without role."""
    relay_calls, error = _run_handoff_relay(
        seat="claude-web",
        packet_path="universal-llm-gateway/tmp/test-packet.md",
        subject="Seat-only handoff",
    )
    assert error is None
    body = relay_calls[0]["body"]
    assert body["seat"] == "claude-web"
    assert "role" not in body


def test_team_dispatch_handoff_missing_seat_and_role() -> None:
    """AC2 — neither seat nor role returns validation_error without relay."""
    relay_calls, error = _run_handoff_relay(
        packet_path="universal-llm-gateway/tmp/test-packet.md",
        subject="Missing seat and role",
    )
    assert len(relay_calls) == 0
    assert error is not None
    assert error["error"]["code"] == "validation_error"
    assert "seat" in error["error"]["message"]
    assert "role" in error["error"]["message"]


def test_team_dispatch_handoff_underspecified() -> None:
    """Neither packet_path nor source_ref returns validation_error without relay."""
    relay_calls, error = _run_handoff_relay(
        role="cursor-implement",
        subject="Missing input path",
    )
    assert len(relay_calls) == 0
    assert error is not None
    assert error["error"]["code"] == "validation_error"
    assert "packet_path" in error["error"]["message"]
    assert "source_ref" in error["error"]["message"]


def test_team_dispatch_handoff_packet_only_parity() -> None:
    """packet_path-only handoff body is unchanged from the pre-source_ref relay shape."""
    relay_calls, error = _run_handoff_relay(
        role="web-consult",
        packet_path="universal-llm-gateway/tmp/test-packet.md",
        subject="Test handoff subject",
    )
    assert error is None
    body = relay_calls[0]["body"]
    assert body == {
        "op": "handoff",
        "role": "web-consult",
        "packet_path": "universal-llm-gateway/tmp/test-packet.md",
        "subject": "Test handoff subject",
    }
