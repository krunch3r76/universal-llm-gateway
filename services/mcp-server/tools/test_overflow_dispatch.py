"""Tests for overflow dispatch shape validation (op + nested arguments)."""

from __future__ import annotations

from tools._overflow_dispatch import (
    flat_op_args_in_dispatch_payload,
    nested_arguments_dispatch_error,
    preflight_nested_op_dispatch,
    tool_schema_has_nested_arguments,
)


def test_tool_schema_has_nested_arguments() -> None:
    assert tool_schema_has_nested_arguments({"properties": {"op": {}, "arguments": {}}})
    assert not tool_schema_has_nested_arguments(
        {"properties": {"op": {}, "pipeline_id": {}}}
    )


def test_flat_op_args_detects_mistaken_dispatch_shape() -> None:
    parsed = {"op": "review_extract", "message_id": "abc"}
    assert flat_op_args_in_dispatch_payload(parsed) == {"message_id": "abc"}
    assert (
        flat_op_args_in_dispatch_payload(
            {"op": "get", "arguments": '{"message_id": "abc"}'}
        )
        == {}
    )


def test_preflight_returns_actionable_error() -> None:
    schema = {"properties": {"op": {"type": "string"}, "arguments": {"type": "string"}}}
    parsed = {"op": "review_extract", "message_id": "msg-1"}
    err = preflight_nested_op_dispatch("email", parsed, schema)
    assert err is not None
    assert "unexpected keyword argument" in err["error"]
    assert "arguments" in err["hint"]
    assert err["accepted_params"] == ["op", "arguments"]


def test_nested_arguments_dispatch_error_includes_example() -> None:
    err = nested_arguments_dispatch_error("email", op="get", flat_keys=["message_id"])
    assert 'dispatch(tool="email"' in err["example"]
    assert "message_id" in err["example"]
