"""Regression tests for MCP request telemetry helpers."""

from __future__ import annotations


def test_suspected_fs_timeout_matches_long_tiny_fs_response() -> None:
    from mcp_request_middleware import _is_suspected_fs_timeout

    assert _is_suspected_fs_timeout("fs", 30.101, 90)


def test_suspected_fs_timeout_ignores_normal_responses() -> None:
    from mcp_request_middleware import _is_suspected_fs_timeout

    assert not _is_suspected_fs_timeout("fs", 0.5, 90)
    assert not _is_suspected_fs_timeout("fs", 30.101, 320)
    assert not _is_suspected_fs_timeout("cortex", 30.101, 90)


def test_seat_class_claude_path() -> None:
    from mcp_request_middleware import _seat_class_from_path

    assert _seat_class_from_path("/mcp") == "claude"


def test_seat_class_unknown_path() -> None:
    from mcp_request_middleware import _seat_class_from_path

    assert _seat_class_from_path("/health") == "unknown"
    assert _seat_class_from_path("/") == "unknown"
    assert _seat_class_from_path("/mcp-other") == "unknown"
