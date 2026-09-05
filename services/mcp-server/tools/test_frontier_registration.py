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


def test_team_dispatch_packet_kind_signature() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "packet_kind" in sig.parameters
    assert sig.parameters["packet_kind"].default is None
    assert "conductor" in str(sig.parameters["packet_kind"].annotation)


def test_team_dispatch_inline_prompt_params_present() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert sig.parameters["prompt"].default is None
    assert sig.parameters["sidecar_ref"].default is None


def test_team_dispatch_messages_removed_from_signature() -> None:
    """messages[] is not part of the team_dispatch wire contract."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "messages" not in sig.parameters


def test_team_dispatch_contract_enum_excludes_consult() -> None:
    """Public contract enum is light-bounded/pure-mechanical/implement."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    annotation = str(sig.parameters["contract"].annotation)
    assert "light-bounded" in annotation
    assert "pure-mechanical" in annotation
    assert "implement" in annotation
    assert "consult" not in annotation


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


def test_team_dispatch_server_tools_param_present() -> None:
    """server_tools is exposed on the team_dispatch MCP signature."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)

    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "server_tools" in sig.parameters
    assert sig.parameters["server_tools"].default is None


def test_team_dispatch_generate_forwards_server_tools() -> None:
    """op='generate' forwards server_tools when supplied."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "thread_id": "thread-test"}

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record"),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                role="reviewer",
                contract="light-bounded",
                dispatch_thread_id="thread-dispatch-1",
                server_tools=False,
            )
        )

    assert len(relay_calls) == 1
    assert relay_calls[0]["body"]["server_tools"] is False


def test_team_dispatch_generate_forwards_inline_prompt() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "thread_id": "thread-test"}

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record"),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                role="reviewer",
                contract="light-bounded",
                dispatch_thread_id="thread-dispatch-1",
                prompt="Review this exact brief.",
            )
        )

    assert relay_calls[0]["body"]["prompt"] == "Review this exact brief."


def test_team_dispatch_implement_rejects_inline_prompt_without_relay() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    with patch("tools.frontier._relay") as relay:
        result = asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="implement",
                dispatch_thread_id="thread-dispatch-1",
                source_ref="todo:x",
                prompt="This must not be silently ignored.",
            )
        )

    relay.assert_not_called()
    assert result["error"]["code"] == "inline_prompt_not_supported"


def test_team_dispatch_generate_forwards_source_ref() -> None:
    """op='generate' forwards source_ref so the first-class wrap transport works.

    Regression: the MCP layer previously dropped source_ref on op=generate
    (TeamDispatchGenerateBody was extra="forbid" with no source_ref). The
    first-class wrap transport added source_ref to the generate body so a bare
    source_ref (no packet_path) materializes the implement packet server-side.
    """
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "thread_id": "thread-test"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="implement",
                source_ref="todo:first-class-wrap-transport",
                dispatch_thread_id="todo:first-class-wrap-transport",
                lane="B",
            )
        )

    assert len(relay_calls) == 1
    body = relay_calls[0]["body"]
    assert body["op"] == "generate"
    assert body["source_ref"] == "todo:first-class-wrap-transport"
    assert body["contract"] == "implement"
    # bare source_ref → no caller packet_path forwarded
    assert "packet_path" not in body


def test_team_dispatch_generate_forwards_conductor_packet_kind() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-conductor", "thread_id": "thread-test"}

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=lambda *a, **k: None),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="light-bounded",
                source_ref="todo:s4-attended-summon-probe",
                packet_kind="conductor",
                dispatch_thread_id="9638",
                lane="B",
            )
        )

    assert len(relay_calls) == 1
    body = relay_calls[0]["body"]
    assert body["packet_kind"] == "conductor"
    assert body["source_ref"] == "todo:s4-attended-summon-probe"
    assert body["contract"] == "light-bounded"
    assert "packet_path" not in body


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
        seat="claude-cursor",
        contract="implement",
        source_ref="todo:unified-admission-handoff-source-ref",
        subject="Implement source_ref adoption",
    )
    assert error is None
    assert len(relay_calls) == 1
    body = relay_calls[0]["body"]
    assert body == {
        "op": "handoff",
        "seat": "claude-cursor",
        "contract": "implement",
        "source_ref": "todo:unified-admission-handoff-source-ref",
        "subject": "Implement source_ref adoption",
    }
    assert "packet_path" not in body


def test_team_dispatch_handoff_both_present() -> None:
    """Both source_ref and packet_path are relayed when provided."""
    relay_calls, error = _run_handoff_relay(
        seat="claude-cursor",
        contract="implement",
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


def test_team_dispatch_handoff_rejects_inline_prompt() -> None:
    relay_calls, error = _run_handoff_relay(
        role="web-consult",
        packet_path="tmp/p.md",
        prompt="This is not a handoff input.",
        subject="Review",
    )
    assert relay_calls == []
    assert error is not None
    assert error["error"]["code"] == "inline_prompt_not_supported"


class TestCursorSeatExplicitGuard:
    """AC1-AC6 + negative cases for handoff_claude_cursor_requires_explicit_seat."""

    def test_cursor_consult_role_rejected(self) -> None:
        """AC1: role=cursor-consult without seat= is rejected; no relay."""
        relay_calls, error = _run_handoff_relay(
            role="cursor-consult",
            subject="s",
            packet_path="tmp/p.md",
        )
        assert error is not None
        assert error["error"]["code"] == "handoff_claude_cursor_requires_explicit_seat"
        assert error["field"] == "seat"
        assert len(relay_calls) == 0

    def test_cursor_implement_role_rejected(self) -> None:
        """AC2: role=cursor-implement without seat= is rejected; no relay."""
        relay_calls, error = _run_handoff_relay(
            role="cursor-implement",
            subject="s",
            packet_path="tmp/p.md",
        )
        assert error is not None
        assert error["error"]["code"] == "handoff_claude_cursor_requires_explicit_seat"
        assert len(relay_calls) == 0

    def test_explicit_cursor_seat_admitted(self) -> None:
        """AC3: seat=claude-cursor relays."""
        relay_calls, error = _run_handoff_relay(
            seat="claude-cursor",
            subject="s",
            packet_path="tmp/p.md",
        )
        assert error is None
        assert len(relay_calls) == 1

    def test_cursor_alias_admitted(self) -> None:
        """AC4: seat=cursor alias relays."""
        relay_calls, error = _run_handoff_relay(
            seat="cursor",
            subject="s",
            packet_path="tmp/p.md",
        )
        assert error is None
        assert len(relay_calls) == 1

    def test_web_seat_with_cursor_role_admitted(self) -> None:
        """AC5: seat=claude-web + role=cursor-implement → web seat wins, not cursor target."""
        relay_calls, error = _run_handoff_relay(
            seat="claude-web",
            role="cursor-implement",
            subject="s",
            packet_path="tmp/p.md",
        )
        assert error is None
        assert len(relay_calls) == 1

    def test_compat_bridge_cursor_seat_and_role(self) -> None:
        """AC6: seat=claude-cursor + role=cursor-implement → explicit seat satisfies guard."""
        relay_calls, error = _run_handoff_relay(
            seat="claude-cursor",
            role="cursor-implement",
            subject="s",
            packet_path="tmp/p.md",
        )
        assert error is None
        assert len(relay_calls) == 1

    def test_cursor_implement_as_seat_not_explicit(self) -> None:
        """Negative: seat='cursor-implement' is not in alias table; role also cursor → rejected."""
        relay_calls, error = _run_handoff_relay(
            seat="cursor-implement",
            role="cursor-implement",
            subject="s",
            packet_path="tmp/p.md",
        )
        assert error is not None
        assert error["error"]["code"] == "handoff_claude_cursor_requires_explicit_seat"
        assert len(relay_calls) == 0


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


def test_team_dispatch_generate_requires_contract() -> None:
    """op='generate' with contract=None is rejected at intake — no relay (F17362)."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "should-not-be-reached"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        result = asyncio.run(
            team_dispatch_fn(
                op="generate",
                role="reviewer",
                dispatch_thread_id="arc-f17362",
                contract=None,
            )
        )

    assert len(relay_calls) == 0
    assert result["error"]["code"] == "validation_error"
    assert result["field"] == "contract"
    assert "contract is required" in result["error"]["message"]


def test_team_dispatch_generate_accepts_subject_with_warning() -> None:
    """Friction 19803: op='generate' + subject no longer 422s.

    subject is accepted, dropped from the forwarded body, and a
    subject_ignored_on_generate warning is attached to the success envelope.
    thread stays rejected (see test_team_dispatch_generate_rejects_thread).
    """
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "thread_id": "thread-test"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        result = asyncio.run(
            team_dispatch_fn(
                op="generate",
                role="reviewer",
                contract="light-bounded",
                dispatch_thread_id="arc-friction-19803",
                subject="Review CF-1: subject guard",
            )
        )

    # relay happened — no hard rejection
    assert len(relay_calls) == 1
    body = relay_calls[0]["body"]
    assert body["op"] == "generate"
    # subject is dropped from the forwarded body (not persisted upstream)
    assert "subject" not in body
    # success envelope carries the non-fatal warning
    assert "error" not in result
    assert any(
        w.startswith("subject_ignored_on_generate") for w in result.get("warnings", [])
    )


def test_team_dispatch_generate_rejects_thread() -> None:
    """op='generate' + thread is still a hard validation_error (no relay)."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "should-not-be-reached"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        result = asyncio.run(
            team_dispatch_fn(
                op="generate",
                role="reviewer",
                contract="light-bounded",
                dispatch_thread_id="arc-friction-19803",
                thread="111",
            )
        )

    assert len(relay_calls) == 0
    assert result["error"]["code"] == "validation_error"
    assert "thread" in result["error"]["message"]


def test_team_dispatch_to_thread_forwards_subject() -> None:
    """op='to_thread' continues to forward subject into the body (unchanged)."""
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "thread_id": "111"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        result = asyncio.run(
            team_dispatch_fn(
                op="to_thread",
                role="reviewer",
                contract="light-bounded",
                dispatch_thread_id="arc-friction-19803",
                thread="111",
                subject="Reviewer reply — labelled",
            )
        )

    assert len(relay_calls) == 1
    body = relay_calls[0]["body"]
    assert body["op"] == "to_thread"
    assert body["thread"] == "111"
    # to_thread still forwards subject into the body
    assert body["subject"] == "Reviewer reply — labelled"
    # and must NOT carry the generate-only warning
    assert not any(
        w.startswith("subject_ignored_on_generate") for w in result.get("warnings", [])
    )


def test_team_dispatch_forwards_cost_intent_on_generate_and_to_thread() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                role="reviewer",
                contract="light-bounded",
                dispatch_thread_id="arc-override-gate",
                model="anthropic/claude-opus-4-8",
                cost_intent="deliberate_high_cost",
                cost_intent_reason="operator authorized",
            )
        )
        asyncio.run(
            team_dispatch_fn(
                op="to_thread",
                role="reviewer",
                contract="light-bounded",
                dispatch_thread_id="arc-override-gate",
                thread="111",
                model="anthropic/claude-opus-4-8",
                cost_intent="deliberate_high_cost",
                cost_intent_reason="operator authorized",
                spawn_review_provenance="generate_review_child",
            )
        )

    assert len(relay_calls) == 2
    gen_body = relay_calls[0]["body"]
    thread_body = relay_calls[1]["body"]
    assert gen_body["cost_intent"] == "deliberate_high_cost"
    assert gen_body["cost_intent_reason"] == "operator authorized"
    assert thread_body["cost_intent"] == "deliberate_high_cost"
    assert thread_body["cost_intent_reason"] == "operator authorized"
    assert thread_body["spawn_review_provenance"] == "generate_review_child"


def test_team_dispatch_nest_under_param_present() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "nest_under" in sig.parameters


def test_team_dispatch_nest_under_descriptor_contract() -> None:
    """AC1: nest_under inputSchema description + docstring advertise depth-10 + sdk-only."""
    from typing import get_args, get_type_hints

    from pydantic.fields import FieldInfo

    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    fn = recorder.functions["team_dispatch"]
    hint = get_type_hints(fn, include_extras=True)["nest_under"]
    field_infos = [a for a in get_args(hint) if isinstance(a, FieldInfo)]
    assert field_infos, "nest_under must carry Annotated[..., Field(description=...)]"
    desc = field_infos[0].description or ""
    assert "depth 10" in desc or "depth-10" in desc, desc
    assert "cursor-sdk" in desc, desc
    assert "nest_under_sdk_only" in desc, desc
    doc = fn.__doc__ or ""
    assert "nest_under" in doc
    assert "depth 10" in doc
    assert "nest_under_sdk_only" in doc
    assert "cursor-sdk" in doc


def test_team_dispatch_generate_forwards_nest_under() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "dispatch_id": "child-1"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="light-bounded",
                dispatch_thread_id="5777",
                nest_under="parent-dispatch-id",
            )
        )

    assert len(relay_calls) == 1
    assert relay_calls[0]["body"]["nest_under"] == "parent-dispatch-id"


def test_team_dispatch_nest_under_rejects_non_sdk_seat() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    result = asyncio.run(
        team_dispatch_fn(
            op="generate",
            role="reviewer",
            contract="light-bounded",
            dispatch_thread_id="5777",
            nest_under="parent-dispatch-id",
        )
    )
    assert result["error"]["code"] == "nest_under_sdk_only"


def test_team_dispatch_resume_of_param_present() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "resume_of" in sig.parameters


def test_team_dispatch_resume_of_descriptor_contract() -> None:
    from typing import get_args, get_type_hints

    from pydantic.fields import FieldInfo

    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    fn = recorder.functions["team_dispatch"]
    hint = get_type_hints(fn, include_extras=True)["resume_of"]
    field_infos = [a for a in get_args(hint) if isinstance(a, FieldInfo)]
    assert field_infos, "resume_of must carry Annotated[..., Field(description=...)]"
    desc = field_infos[0].description or ""
    assert "reuse_thread" in desc, desc
    assert "resume_of_sdk_only" in desc, desc


def test_team_dispatch_generate_forwards_resume_of() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "dispatch_id": "child-1"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="light-bounded",
                dispatch_thread_id="5777",
                reuse_thread="5777",
                resume_of="parent-dispatch-id",
            )
        )

    assert len(relay_calls) == 1
    assert relay_calls[0]["body"]["resume_of"] == "parent-dispatch-id"
    assert relay_calls[0]["body"]["reuse_thread"] == "5777"


def test_team_dispatch_resume_of_rejects_non_sdk_seat() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    result = asyncio.run(
        team_dispatch_fn(
            op="generate",
            role="reviewer",
            contract="light-bounded",
            dispatch_thread_id="5777",
            resume_of="parent-dispatch-id",
        )
    )
    assert result["error"]["code"] == "resume_of_sdk_only"


def test_team_dispatch_lane_param_present() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    sig = inspect.signature(recorder.functions["team_dispatch"])
    assert "lane" in sig.parameters


def test_team_dispatch_lane_descriptor_requires_named_lane() -> None:
    from typing import get_args, get_type_hints

    from pydantic.fields import FieldInfo

    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    fn = recorder.functions["team_dispatch"]
    hint = get_type_hints(fn, include_extras=True)["lane"]
    field_infos = [a for a in get_args(hint) if isinstance(a, FieldInfo)]
    assert field_infos, "lane must carry Annotated[..., Field(description=...)]"
    desc = field_infos[0].description or ""
    assert "lane_required" in desc, desc
    assert "nest_under" in desc, desc
    assert "resume_of" in desc, desc
    doc = fn.__doc__ or ""
    assert "lane_required" in doc
    assert "nest_under" in doc
    assert "resume_of" in doc


def test_team_dispatch_generate_forwards_lane() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "dispatch_id": "child-b"}

    def _fake_record(event: str, **kwargs: Any) -> None:
        return None

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=_fake_record),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="implement",
                dispatch_thread_id="5777",
                lane="B",
            )
        )

    assert len(relay_calls) == 1
    assert relay_calls[0]["body"]["lane"] == "B"


def test_team_dispatch_generate_requires_lane_when_unset() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    with patch("tools.frontier._relay") as relay:
        result = asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="implement",
                dispatch_thread_id="5777",
            )
        )

    relay.assert_not_called()
    assert result["error"]["code"] == "lane_required"
    assert result["field"] == "lane"


def test_team_dispatch_generate_model_only_requires_lane() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]

    with patch("tools.frontier._relay") as relay:
        result = asyncio.run(
            team_dispatch_fn(
                op="generate",
                model="cursor/composer-2.5",
                contract="light-bounded",
                dispatch_thread_id="5777",
                prompt="bind this",
            )
        )

    relay.assert_not_called()
    assert result["error"]["code"] == "lane_required"


def test_team_dispatch_lane_rejects_non_sdk_seat() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    result = asyncio.run(
        team_dispatch_fn(
            op="generate",
            role="reviewer",
            contract="light-bounded",
            dispatch_thread_id="5777",
            lane="B",
        )
    )
    assert result["error"]["code"] == "lane_sdk_only"


def test_workspace_sdk_only_on_non_sdk_seat() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    result = asyncio.run(
        team_dispatch_fn(
            op="generate",
            seat="web-anthropic",
            contract="light-bounded",
            dispatch_thread_id="5777",
            workspace="claudeburst",
        )
    )
    assert result["error"]["code"] == "workspace_sdk_only"
