"""Lock execute_op offload-hint wiring for large-payload cortex ops (friction 17357)."""

from __future__ import annotations

from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops.workflow_hints import _CORTEX_LARGE_PAYLOAD_OPS

_FORMAT_HINT = "arguments must be a JSON-encoded object string"
_OFFLOAD_SUBSTRINGS = ("handoff_source_path", "transcript_jsonl_path", "/agent-bus")


def test_large_payload_op_malformed_string_includes_offload_hint() -> None:
    result = execute_op("session_close", '{"session_id": "x"')
    assert "error" in result
    msg = result["error"]
    assert _FORMAT_HINT in msg
    for fragment in _OFFLOAD_SUBSTRINGS:
        assert fragment in msg


def test_non_large_payload_op_malformed_string_omits_offload_hint() -> None:
    result = execute_op("entity_get", "{bad")
    assert "error" in result
    msg = result["error"]
    assert _FORMAT_HINT in msg
    for fragment in _OFFLOAD_SUBSTRINGS:
        assert fragment not in msg


def test_cortex_large_payload_ops_membership() -> None:
    assert _CORTEX_LARGE_PAYLOAD_OPS == frozenset(
        {
            "session_close",
            "session_close_preflight",
            "session_handoff_upsert",
            "journal_write",
        }
    )
