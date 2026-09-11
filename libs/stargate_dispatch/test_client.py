"""Tests for Stargate dispatch client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from stargate_dispatch.client import submit_team_dispatch


def test_refuses_giw_direct_url() -> None:
    payload, status = submit_team_dispatch(
        {"op": "generate", "seat": "cursor-sdk"},
        base_url="http://127.0.0.1:8091/api/v1/cursor/dispatch",
    )
    assert status == 400
    assert "refuse_giw_direct" in payload["error"]["message"]


@patch("stargate_dispatch.client.httpx.post")
def test_posts_team_dispatch_fields(mock_post: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"execution_id": "abc", "thread_id": "10496"}
    mock_post.return_value = mock_resp
    body = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "none",
        "lane": "A",
        "model": "cursor/claude-opus-5",
        "packet_path": "tmp/prompts/liaison-successor-10479.md",
        "dispatch_thread_id": "10479",
        "work_key": "agent-bus:10479",
        "timeout_seconds": 5400,
        "tags": ["liaison-successor"],
        "extra_ignored": "drop",
    }
    payload, status = submit_team_dispatch(body, base_url="http://localhost:9999")
    assert status == 200
    assert payload["execution_id"] == "abc"
    sent = mock_post.call_args.kwargs["json"]
    assert sent["work_key"] == "agent-bus:10479"
    assert "extra_ignored" not in sent
    assert mock_post.call_args.args[0].endswith("/api/v1/team/dispatch")
