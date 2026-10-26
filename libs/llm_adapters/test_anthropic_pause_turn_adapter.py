"""Anthropic adapter pause_turn continuation and container.skills mount."""

from __future__ import annotations

import pytest

from llm_adapters import FrontierRequest
from llm_adapters.anthropic.adapter import AnthropicAdapter


@pytest.mark.offline
def test_append_pause_turn_continuation_sets_container_id() -> None:
    adapter = AnthropicAdapter(api_key="test")
    body: dict = {"messages": [{"role": "user", "content": "hi"}]}
    raw = {
        "content": [{"type": "text", "text": "partial"}],
        "container": {"id": "ctr_pause"},
    }
    adapter.append_pause_turn_continuation(body, raw)
    assert body["messages"][-1]["role"] == "assistant"
    assert body["container"]["id"] == "ctr_pause"


@pytest.mark.offline
def test_build_frontier_request_mounts_container_skills_and_code_execution() -> None:
    adapter = AnthropicAdapter(api_key="test")
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-sonnet-5-5",
        max_tokens=1024,
        skills_mount=[
            {"type": "custom", "skill_id": "sk_demo", "version": "latest"},
        ],
    )
    _url, headers, body = adapter.build_frontier_request(req)
    assert body["container"]["skills"][0]["skill_id"] == "sk_demo"
    assert any(t.get("name") == "code_execution" for t in body.get("tools") or [])
    assert "skills-2025-10-02" in headers.get("anthropic-beta", "")


@pytest.mark.offline
def test_parse_frontier_response_surfaces_container_id() -> None:
    adapter = AnthropicAdapter(api_key="test")
    parsed = adapter.parse_frontier_response(
        {
            "id": "msg_1",
            "model": "claude-sonnet-5-5",
            "stop_reason": "pause_turn",
            "content": [{"type": "text", "text": "x"}],
            "container": {"id": "ctr_abc"},
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }
    )
    assert parsed["container_id"] == "ctr_abc"
    assert parsed["finish_reason"] == "pause_turn"
