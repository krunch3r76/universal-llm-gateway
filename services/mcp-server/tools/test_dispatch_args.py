"""Dispatch-style ``arguments`` parse + error-builder contract.

Covers ``parse_dispatch_arguments``, ``dispatch_arguments_error``, and
``JsonArgStr`` coercion in ``tools._agent_tools``. Pins the narrowed contract
from decision:dispatch-arguments-string-wire-form (agent-bus 1741):

- the inner ``arguments`` wire form stays a JSON-encoded object string;
- correctly-escaped large/quote-heavy payloads round-trip to a dict;
- a malformed *string* parse failure yields an actionable offload hint;
- non-object JSON and dict passthrough keep their existing behavior;
- ``JsonArgStr`` coerces dict → JSON string at the MCP boundary while keeping
  the JSON Schema as ``{"type": "string"}``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import _agent_tools as at  # noqa: E402

parse_dispatch_arguments = at.parse_dispatch_arguments
dispatch_arguments_error = at.dispatch_arguments_error
DISPATCH_STYLE_TOOLS = at.DISPATCH_STYLE_TOOLS
JsonArgStr = at.JsonArgStr


# ── parse_dispatch_arguments ────────────────────────────────────────────────


def test_parse_accepts_plain_json_string() -> None:
    assert parse_dispatch_arguments('{"entity_id": "decision:foo"}') == {
        "entity_id": "decision:foo"
    }


def test_parse_dict_passthrough_for_direct_callers() -> None:
    payload = {"thread": "1741", "after_turn": 1}
    # Identity passthrough — direct/internal callers may hand in a dict.
    assert parse_dispatch_arguments(payload) is payload


def test_parse_large_quote_heavy_markdown_round_trips() -> None:
    """Friction 12886/17227: large transcript_md with embedded quotes, newlines,
    code fences, and an embedded JSON object must round-trip when correctly
    JSON-escaped (the wire contract holds; the footgun is hand-escaping)."""
    transcript_md = (
        '# Session\n\nHe said "ship it" and pasted:\n\n'
        "```json\n"
        '{"handoff": {"next": "open thread 1741", "note": "use \\"poll_hint\\""}}\n'
        "```\n\n"
        'Then a quote: "the schema declares arguments: string".\n'
    )
    inner = {"session_id": "web-1", "transcript_md": transcript_md}
    # A correct serializer (json.dumps) escapes the payload; the model's
    # hand-built string is what historically broke.
    raw = json.dumps(inner)
    parsed = parse_dispatch_arguments(raw)
    assert parsed is not None
    assert parsed["transcript_md"] == transcript_md
    # The embedded JSON snippet survives verbatim inside the markdown.
    assert '{"handoff":' in parsed["transcript_md"]


def test_parse_handoff_prompt_with_nested_poll_hint_round_trips() -> None:
    """Friction 17357: handoff_prompt embedding poll-hint JSON must round-trip
    when correctly JSON-escaped (the wire contract holds; hand-escaping breaks)."""
    poll_hint = {
        "tool": "wait",
        "arguments": json.dumps({"thread": "1810", "after_turn": 2}),
    }
    handoff_prompt = (
        "## Deferred\n\nthread 1810 — poll:\n```json\n"
        + json.dumps(poll_hint)
        + "\n```"
    )
    inner = {"session_id": "web-1", "handoff_prompt": handoff_prompt}
    raw = json.dumps(inner)
    parsed = parse_dispatch_arguments(raw)
    assert parsed is not None
    assert parsed["handoff_prompt"] == handoff_prompt


def test_parse_handoff_prompt_hand_mis_escaped_returns_none() -> None:
    poll_hint = {
        "tool": "wait",
        "arguments": json.dumps({"thread": "1810", "after_turn": 2}),
    }
    handoff_prompt = (
        "## Deferred\n\nthread 1810 — poll:\n```json\n"
        + json.dumps(poll_hint)
        + "\n```"
    )
    # Unescaped inner quote in handoff_prompt — canonical escaping failure mode.
    malformed = (
        '{"session_id": "web-1", "handoff_prompt": '
        + json.dumps(handoff_prompt).replace('\\"', '"', 1)
        + "}"
    )
    assert parse_dispatch_arguments(malformed) is None


def test_parse_malformed_string_returns_none() -> None:
    # Unescaped inner quote — the canonical escaping failure mode.
    assert parse_dispatch_arguments('{"transcript_md": "he said "hi""}') is None


def test_parse_non_object_json_string_returns_none() -> None:
    assert parse_dispatch_arguments("[1, 2, 3]") is None
    assert parse_dispatch_arguments("42") is None
    assert parse_dispatch_arguments('"just a string"') is None


def test_parse_non_str_non_dict_returns_none() -> None:
    assert parse_dispatch_arguments(None) is None
    assert parse_dispatch_arguments(12) is None


# ── dispatch_arguments_error ────────────────────────────────────────────────


def test_error_on_string_input_appends_offload_hint() -> None:
    err = dispatch_arguments_error('{"bad": ', example='{"thread": "111"}')
    msg = err["error"]
    assert "arguments must be a JSON-encoded object string" in msg
    assert '{"thread": "111"}' in msg
    # The actionable offload guidance is the point of the narrowed contract.
    assert "file-path parameter" in msg
    assert "session_summary_md_path" in msg
    assert "transcript_jsonl_path" in msg
    assert "/agent-bus" in msg


def test_error_on_non_string_input_omits_offload_hint() -> None:
    # A non-string (e.g. a dict that somehow failed) is not an escaping problem,
    # so the escaping-specific hint is not appended.
    err = dispatch_arguments_error(12, example='{"key": "value"}')
    msg = err["error"]
    assert "arguments must be a JSON-encoded object string" in msg
    assert "file-path parameter" not in msg


def test_error_reports_received_type() -> None:
    assert "int" in dispatch_arguments_error(5, example="{}")["error"]
    assert "str" in dispatch_arguments_error("x", example="{}")["error"]


# ── DISPATCH_STYLE_TOOLS constant ───────────────────────────────────────────


def test_dispatch_style_tool_set_is_the_single_source_of_truth() -> None:
    assert DISPATCH_STYLE_TOOLS == frozenset(
        {"cortex", "agent_bus", "agent_bus_read", "rag", "dispatch"}
    )


# ── JsonArgStr coercion ──────────────────────────────────────────────────────


def test_json_arg_str_coerces_dict_to_json_string() -> None:
    """Agents that pass an object literal instead of a JSON string are coerced
    transparently — the MCP boundary normalises the value before the handler
    receives it, eliminating mid-session self-correction."""
    from pydantic import TypeAdapter

    ta = TypeAdapter(JsonArgStr)
    result = ta.validate_python({"entity_id": "decision:foo", "intent": "card"})
    assert result == '{"entity_id": "decision:foo", "intent": "card"}'
    # Round-trips: the coerced string is valid JSON.
    assert json.loads(result) == {"entity_id": "decision:foo", "intent": "card"}


def test_json_arg_str_passes_string_through_unchanged() -> None:
    from pydantic import TypeAdapter

    ta = TypeAdapter(JsonArgStr)
    raw = '{"entity_id": "todo:foo"}'
    assert ta.validate_python(raw) == raw


def test_json_arg_str_schema_stays_string_type() -> None:
    """JSON Schema must remain ``{"type": "string"}`` — the mcp-tool-param-types
    invariant forbids ``anyOf/object`` on optional params because Claude.ai's
    MCP client silently drops them (decision:dispatch-arguments-string-wire-form).
    """
    from pydantic import TypeAdapter

    schema = TypeAdapter(JsonArgStr).json_schema()
    assert schema == {"type": "string"}


# ── raw_kind classification + instrumentation (spec §9 AC6) ──────────────────


def test_classify_dispatch_args_raw_whole_object_literal() -> None:
    assert (
        at.classify_dispatch_args_raw({"entity_id": "decision:foo"})
        == "whole_object_literal"
    )


def test_classify_dispatch_args_raw_malformed_string() -> None:
    assert at.classify_dispatch_args_raw('{"bad": ') == "malformed_string"
    # Non-dict, non-string failures fall under the residual (string) class.
    assert at.classify_dispatch_args_raw(None) == "malformed_string"


def test_dispatch_arguments_error_emits_raw_kind_event_both_classes(monkeypatch) -> None:
    """Both failure classes route through dispatch_arguments_error and emit a
    structured raw_kind event — the measurable gate for the deferred
    client-serialization helper (spec §9 / todo:dispatch-args-client-serialization-helper).
    """
    import mcp_events

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        mcp_events, "record", lambda name, **kw: events.append((name, kw))
    )

    dispatch_arguments_error('{"bad": ', example="{}", tool="cortex")
    dispatch_arguments_error({"obj": 1}, example="{}", tool="rag")

    assert [e[0] for e in events] == [
        "mcp.dispatch.arguments.invalid",
        "mcp.dispatch.arguments.invalid",
    ]
    assert events[0][1]["raw_kind"] == "malformed_string"
    assert events[0][1]["tool"] == "cortex"
    assert events[1][1]["raw_kind"] == "whole_object_literal"
    assert events[1][1]["tool"] == "rag"


def test_dispatch_arguments_error_event_failure_never_breaks_error_path(
    monkeypatch,
) -> None:
    """Instrumentation is best-effort: a failing event bus must not break the
    error response the caller depends on."""
    import mcp_events

    def _boom(*_a, **_k):
        raise RuntimeError("event bus down")

    monkeypatch.setattr(mcp_events, "record", _boom)
    err = dispatch_arguments_error('{"bad": ', example='{"thread": "1"}')
    assert "arguments must be a JSON-encoded object string" in err["error"]
    assert "file-path parameter" in err["error"]
