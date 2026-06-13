"""Regression: agent_bus fetch read-path windowing + 422 status enrichment."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastmcp.tools.tool import ToolResult
from request_profile import bind_request
from response_size_guard import (
    _agent_bus_manifest,
    _measure_result,
    _try_window_agent_bus_result,
)
from tools.agent_bus import (  # noqa: E402
    _fetch_dispatch,
    _fetch_impl,
    _fetch_unread_dispatch,
    _format_agent_bus_error,
)


def test_fetch_impl_forwards_compact_and_last_on_thread_only_path() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, kwargs
        qs = urlparse(path).query
        captured.update({k: v[0] for k, v in parse_qs(qs).items()})
        return {"turns": []}

    with patch("tools.agent_bus._relay", side_effect=relay):
        _fetch_impl(
            to=None,
            thread="1138",
            last=3,
            unread=False,
            mark_read=False,
            compact=True,
        )

    assert captured["thread"] == "1138"
    assert captured["last"] == "3"
    assert captured["compact"] == "true"
    assert "unread" not in captured


def test_fetch_dispatch_applies_last_by_default() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, kwargs
        qs = urlparse(path).query
        captured.update({k: v[0] for k, v in parse_qs(qs).items()})
        return {"turns": []}

    with patch("tools.agent_bus._relay", side_effect=relay):
        _fetch_dispatch(thread="1138", last=8, compact=True)

    assert captured["last"] == "8"
    assert captured["compact"] == "true"
    assert "unread" not in captured


def test_fetch_dispatch_defaults_compact_false_projects_body() -> None:
    """BUG 4 (thread 1154): fetch must default compact=false so bodies are projected.

    A compact=True default silently nulled turn `body` on windowed/thread-only
    fetches, making a populated thread read as empty. The MCP fetch op must match
    fetch_unread, get, and the CLI (all bodies-by-default).
    """
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, kwargs
        qs = urlparse(path).query
        captured.update({k: v[0] for k, v in parse_qs(qs).items()})
        return {"turns": []}

    with patch("tools.agent_bus._relay", side_effect=relay):
        _fetch_dispatch(thread="1161", last=10)

    assert "compact" not in captured
    assert captured["last"] == "10"


def test_fetch_dispatch_unread_true_omits_last() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, kwargs
        qs = urlparse(path).query
        captured.update({k: v[0] for k, v in parse_qs(qs).items()})
        return {"turns": []}

    with patch("tools.agent_bus._relay", side_effect=relay):
        _fetch_dispatch(thread="1138", last=8, unread=True, compact=True)

    assert captured["unread"] == "true"
    assert "last" not in captured


@pytest.mark.parametrize(
    ("result", "expected_fragment"),
    [
        (
            {
                "error": "HTTP 422",
                "status_code": 422,
                "detail": [
                    {
                        "type": "enum",
                        "loc": ("body", "status"),
                        "msg": "Input should be 'open', 'resolved', 'superseded' or 'waiting'",
                        "input": "active",
                    }
                ],
            },
            "turn status must be one of: open, resolved, superseded, waiting",
        ),
    ],
)
def test_format_agent_bus_error_surfaces_turn_status_enum(
    result: dict, expected_fragment: str
) -> None:
    message = _format_agent_bus_error(result, op="reply")
    assert expected_fragment in message
    assert "update_thread" in message


def test_threads_manifest_uses_listing_hints_not_last_window() -> None:
    payload = {
        "threads": [
            {
                "id": "1138",
                "subject": "oversize listing",
                "summary": "sample",
                "status": "active",
            }
        ]
    }
    with bind_request("default", agent_bus_tool="threads"):
        manifest = _agent_bus_manifest(
            "rs_test01", payload, size=256_000, threshold=128_000
        )

    assert manifest["adaptive_last"] is None
    assert manifest.get("listing_op") is True
    options = "\n".join(manifest["selective_options"])
    assert "limit" in options
    assert "tags" in options
    assert '"last":' not in options


def test_oversize_fetch_windowed_fixture_passes_through_guard() -> None:
    """todo:agent-bus-oversize-fetch-windowed-fixture — window before size guard."""
    from response_size_guard import _reasoning_target_bytes

    turns = [
        {
            "thread": "1138",
            "turn_number": n,
            "subject": f"turn {n}",
            "body": "x" * 6_000,
        }
        for n in range(20, 0, -1)
    ]
    payload = {"turns": turns}
    full = ToolResult(structured_content=payload)
    threshold = 128_000
    assert _measure_result(full) > _reasoning_target_bytes(threshold)

    with bind_request("default", agent_bus_tool="fetch", agent_bus_last=3):
        windowed = _try_window_agent_bus_result(full, threshold=threshold)

    assert windowed is not None
    out_turns = windowed.structured_content["turns"]
    assert len(out_turns) == 3
    assert all(t.get("body") for t in out_turns)
    assert _measure_result(windowed) < _measure_result(full)


def test_turn_fetch_manifest_still_suggests_last_window() -> None:
    payload = {
        "turns": [
            {
                "thread": "1138",
                "turn_number": 12,
                "subject": "big turn",
                "body": "x" * 50_000,
            }
            for _ in range(20)
        ]
    }
    with bind_request("default", agent_bus_tool="fetch", agent_bus_last=8):
        manifest = _agent_bus_manifest(
            "rs_test02", payload, size=256_000, threshold=128_000
        )

    assert manifest["adaptive_last"] is not None
    options = "\n".join(manifest["selective_options"])
    assert '"last":' in options


def test_fetch_unread_recipient_scope_routes_to_toc_endpoint() -> None:
    """friction 16835: recipient-scoped fetch_unread must hit the bounded
    /turns/unread-toc digest, not the uncapped /turns?unread=true fan-out."""
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, kwargs
        captured["path"] = path
        captured.update({k: v[0] for k, v in parse_qs(urlparse(path).query).items()})
        return {
            "threads": [],
            "total_unread_threads": 0,
            "total_unread_turns": 0,
            "marked_read": 0,
        }

    with patch("tools.agent_bus._relay", side_effect=relay):
        out = _fetch_unread_dispatch(to="claude-web")

    assert captured["path"].startswith("/turns/unread-toc")
    assert captured["to"] == "claude-web"
    assert "unread" not in captured  # not the flat /turns?unread=true path
    assert out["total_unread_threads"] == 0


def test_fetch_unread_recipient_scope_forwards_mark_read() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, kwargs
        captured["path"] = path
        captured.update({k: v[0] for k, v in parse_qs(urlparse(path).query).items()})
        return {"threads": []}

    with patch("tools.agent_bus._relay", side_effect=relay):
        _fetch_unread_dispatch(to="claude-web", mark_read=True)

    assert captured["path"].startswith("/turns/unread-toc")
    assert captured["mark_read"] == "true"


def test_fetch_unread_thread_scope_still_returns_turn_list() -> None:
    """Thread-scoped fetch_unread keeps the flat List[Turn] turn path."""
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, kwargs
        captured["path"] = path
        captured.update({k: v[0] for k, v in parse_qs(urlparse(path).query).items()})
        return {"turns": []}

    with patch("tools.agent_bus._relay", side_effect=relay):
        _fetch_unread_dispatch(thread="1138")

    assert captured["path"].startswith("/turns?")
    assert captured["thread"] == "1138"
    assert captured["unread"] == "true"
    assert "/turns/unread-toc" not in captured["path"]
