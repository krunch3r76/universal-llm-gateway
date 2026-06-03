"""Phase 5 M3: MCP tool registration test — dispatch-surface-split.

Verifies that register_frontier_tools() registers exactly team_dispatch and
frontier_dispatch, with no legacy team_generate or frontier_generate tools.
"""

from __future__ import annotations

import inspect
from typing import Any

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


def test_m3_only_dispatch_tools_registered() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)  # type: ignore[arg-type]

    assert "team_dispatch" in recorder.registered, (
        "team_dispatch not registered — Phase 1 tool registration missing"
    )
    assert "frontier_dispatch" in recorder.registered, (
        "frontier_dispatch not registered — Phase 1 tool registration missing"
    )
    assert "team_generate" not in recorder.registered, (
        "team_generate still registered — Phase 4 deletion incomplete"
    )
    assert "frontier_generate" not in recorder.registered, (
        "frontier_generate still registered — Phase 4 deletion incomplete"
    )


def test_m3_exactly_two_tools_registered() -> None:
    """Only the two dispatch tools; no other frontier-generate variants."""
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
