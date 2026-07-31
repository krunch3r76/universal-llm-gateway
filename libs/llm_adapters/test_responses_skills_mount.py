"""Unit tests for Responses API adapter native skills mount handling."""

from __future__ import annotations

import pytest

from llm_adapters import FrontierRequest
from llm_adapters.responses import ResponsesAPIAdapter


def _adapter(vendor: str = "openai") -> ResponsesAPIAdapter:
    return ResponsesAPIAdapter(
        api_key="k-test",
        base_url="https://api.openai.com/v1",
        vendor=vendor,
    )


def _base_req(**overrides) -> FrontierRequest:
    base = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "gpt-5.5",
        "max_tokens": 4096,
    }
    base.update(overrides)
    return FrontierRequest(**base)


_SKILLS_MOUNT = [
    {
        "name": "agent-identity-signoff",
        "description": "Identity sign-off discipline for assistant turns.",
        "data_base64": "UEsDBBQAAAAI",
    }
]


def test_skills_mount_absent_matches_prechange_tools() -> None:
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    req = _base_req(tools=tools)
    _url, _headers, body = _adapter().build_frontier_request(req)
    assert body["tools"] == [
        {
            "type": "function",
            "name": "f",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


def test_skills_mount_appends_shell_tool_default_branch() -> None:
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    req = _base_req(tools=tools, skills_mount=_SKILLS_MOUNT)
    _url, _headers, body = _adapter().build_frontier_request(req)
    assert len(body["tools"]) == 2
    shell = body["tools"][-1]
    assert shell == {
        "type": "shell",
        "environment": {
            "type": "container_auto",
            "skills": [
                {
                    "type": "inline",
                    "name": "agent-identity-signoff",
                    "description": "Identity sign-off discipline for assistant turns.",
                    "source": {
                        "type": "base64",
                        "media_type": "application/zip",
                        "data": "UEsDBBQAAAAI",
                    },
                }
            ],
        },
    }


@pytest.fixture(autouse=True)
def _mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.example.com/mcp")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token-xyz")


def test_skills_mount_appends_shell_tool_remote_mcp_branch() -> None:
    req = _base_req(remote_mcp=True, skills_mount=_SKILLS_MOUNT)
    _url, _headers, body = _adapter().build_frontier_request(req)
    assert body["tools"][-1]["type"] == "shell"
    assert body["tools"][0]["type"] == "mcp"


def test_skills_mount_non_openai_vendor_raises() -> None:
    req = _base_req(skills_mount=_SKILLS_MOUNT)
    with pytest.raises(ValueError, match="only supported for OpenAI"):
        _adapter("xai").build_frontier_request(req)


def test_parse_frontier_response_routes_shell_call_to_server_tools() -> None:
    raw = {
        "output": [
            {
                "type": "shell_call",
                "id": "shell_1",
                "action": {"commands": ["cat agent-identity-signoff/SKILL.md"]},
            },
            {
                "type": "function_call",
                "call_id": "fc_1",
                "name": "lookup",
                "arguments": "{}",
            },
        ]
    }
    parsed = _adapter().parse_frontier_response(raw)
    assert parsed["tool_calls"] == [
        {"id": "fc_1", "name": "lookup", "arguments": "{}"}
    ]
    assert parsed["server_tool_calls"][0]["type"] == "shell_call"


def test_append_tool_round_replays_shell_call_items() -> None:
    adapter = _adapter()
    body = {"input": [{"role": "user", "content": "hi"}]}
    raw_response = {
        "output": [
            {"type": "message", "role": "assistant", "content": []},
            {
                "type": "shell_call",
                "id": "shell_1",
                "action": {"commands": ["cat SKILL.md"]},
            },
            {
                "type": "function_call",
                "call_id": "fc_1",
                "name": "lookup",
                "arguments": "{}",
            },
        ]
    }
    adapter.append_tool_round(
        body,
        raw_response,
        [{"id": "fc_1", "name": "lookup", "content": "ok"}],
    )
    types = [item.get("type") for item in body["input"][1:] if isinstance(item, dict)]
    assert types == [
        "message",
        "shell_call",
        "function_call",
        "function_call_output",
    ]
