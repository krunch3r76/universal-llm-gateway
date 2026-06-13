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
