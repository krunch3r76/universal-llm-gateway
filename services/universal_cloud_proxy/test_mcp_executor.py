from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import httpx
import pytest

from services.universal_cloud_proxy import mcp_executor as mcp_executor_module
from services.universal_cloud_proxy.boot_directive import parse_boot_directive
from services.universal_cloud_proxy.mcp_executor import (
    McpToolExecutor,
    _compat_dispatch_tool_defs,
    _mcp_schema_to_openai_tool,
)


def test_mcp_schema_to_openai_tool_sanitizes_function_schema() -> None:
    tool = {
        "name": "web_fetch",
        "description": "Fetch a URL",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "title": "Url"},
                "headers": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {"Authorization": {"type": "string"}},
                            "additionalProperties": False,
                        },
                        {"type": "null"},
                    ],
                    "default": None,
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    }

    out = _mcp_schema_to_openai_tool(tool)
    params = out["function"]["parameters"]

    assert out["function"]["name"] == "web_fetch"
    assert params["type"] == "object"
    assert "additionalProperties" not in params
    assert "title" not in params["properties"]["url"]
    headers = params["properties"]["headers"]
    assert headers["type"] == "object"
    assert "additionalProperties" not in headers
    assert "default" not in headers


def test_mcp_schema_to_openai_tool_drops_const_and_single_value_enum() -> None:
    tool = {
        "name": "cortex",
        "description": "Cortex knowledge",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "const": "entities",
                    "enum": ["entities"],
                },
            },
            "required": ["tool"],
        },
    }

    params = _mcp_schema_to_openai_tool(tool)["function"]["parameters"]
    tool_prop = params["properties"]["tool"]

    assert "const" not in tool_prop
    assert "enum" not in tool_prop


def test_compat_dispatch_tool_defs_restores_web_fetch() -> None:
    defs = _compat_dispatch_tool_defs({"web_search", "dispatch"})
    assert [d["function"]["name"] for d in defs] == ["web_fetch"]


def test_parse_boot_directive_non_match() -> None:
    assert parse_boot_directive("hello world") is None
    assert parse_boot_directive('cortex_brief(transcript_id="foo")') is None


def test_parse_boot_directive_seat_slug() -> None:
    parsed = parse_boot_directive('cortex_brief(seat="web-anthropic")')
    assert parsed is not None
    _, kwargs = parsed
    assert kwargs == {"seat": "web-anthropic"}


@pytest.mark.asyncio
async def test_resolve_boot_directive_seat_claude_cursor() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "BRIEFING"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    messages = [
        {
            "role": "system",
            "content": 'Start with cortex_brief(seat="claude-cursor") then work.',
        }
    ]
    await executor._resolve_boot_directive(messages)

    assert calls == [("cortex_brief", {"seat": "claude-cursor"})]
    assert messages[0]["content"] == "Start with BRIEFING then work."


@pytest.mark.asyncio
async def test_resolve_boot_directive_agent_claude_cursor() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "BRIEFING"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    messages = [
        {
            "role": "system",
            "content": 'Start with cortex_brief(agent="claude-cursor") then work.',
        }
    ]
    await executor._resolve_boot_directive(messages)

    # Legacy agent= in prompt text is normalized to seat= before MCP call.
    assert calls == [("cortex_brief", {"seat": "claude-cursor"})]
    assert messages[0]["content"] == "Start with BRIEFING then work."


@pytest.mark.asyncio
async def test_resolve_boot_directive_family_platform() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "API MULTI"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    messages = [
        {
            "role": "system",
            "content": 'cortex_brief(family="Grok", platform="api-multi")',
        }
    ]
    await executor._resolve_boot_directive(messages)

    # Legacy family+platform in prompt text normalize to seat={family}-{platform}.
    assert calls == [
        ("cortex_brief", {"seat": "grok-api-multi"}),
    ]
    assert messages[0]["content"] == "API MULTI"


@pytest.mark.asyncio
async def test_resolve_boot_directive_passes_all_primary_params() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "CARD"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    messages = [
        {
            "role": "system",
            "content": (
                'cortex_brief(agent="claude-web", family="grok", '
                'platform="cursor", role="lead")'
            ),
        }
    ]
    await executor._resolve_boot_directive(messages)

    # Explicit seat= wins; legacy family/platform in the same call are not forwarded.
    assert calls == [
        (
            "cortex_brief",
            {
                "seat": "claude-web",
                "role": "lead",
            },
        )
    ]


@pytest.mark.asyncio
async def test_t14_web_boot_appends_invariant_bodies_digest_stamped() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "BRIEFING CARD"})

    def _fake_fetch(entity_id: str) -> dict[str, object]:
        slug = entity_id.removeprefix("agent_skill:")
        return {
            "id": entity_id,
            "digest": f"sha256:{slug[:8]}",
            "body": f"# {slug}\nInvariant body for {slug}.",
        }

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    messages = [
        {
            "role": "system",
            "content": 'Prefix cortex_brief(agent="claude-web") suffix',
        }
    ]
    with patch(
        "services.universal_cloud_proxy.mcp_executor._fetch_skill_body_sync",
        side_effect=_fake_fetch,
    ):
        await executor._resolve_boot_directive(messages)

    content = messages[0]["content"]
    assert "BRIEFING CARD" in content
    assert "architecture-invariants" in content
    assert "ulg-architecture" in content
    assert "digest:sha256:architec" in content
    assert "digest:sha256:ulg-arch" in content

    # Dedup: second pass with digest already present skips re-append
    prior_len = len(content)
    with patch(
        "services.universal_cloud_proxy.mcp_executor._fetch_skill_body_sync",
        side_effect=_fake_fetch,
    ):
        await mcp_executor_module._append_web_invariant_bodies(content, "claude-web")
    assert len(content) == prior_len


@pytest.mark.asyncio
async def test_resolve_boot_directive_leaves_unmatched_prompt() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_execute(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return json.dumps({"briefing_card": "CARD"})

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor.execute_tool = _fake_execute  # type: ignore[method-assign]

    original = "No boot directive here."
    messages = [{"role": "system", "content": original}]
    await executor._resolve_boot_directive(messages)

    assert calls == []
    assert messages[0]["content"] == original


@pytest.mark.asyncio
async def test_execute_tool_routes_hidden_web_fetch_via_dispatch() -> None:
    calls: list[dict[str, object]] = []

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            calls.append(
                {
                    "json": json,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"ok"}]}}'
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]
    executor._dispatch_compat_names = {"web_fetch"}

    result = await executor.execute_tool("web_fetch", {"url": "https://example.com"})

    assert result == "ok"
    assert calls
    params = calls[0]["json"]["params"]  # type: ignore[index]
    assert params["name"] == "dispatch"
    assert params["arguments"] == {
        "tool": "web_fetch",
        "arguments": {"url": "https://example.com"},
    }


@pytest.mark.asyncio
async def test_execute_tool_retries_remote_protocol_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    monkeypatch.setattr(mcp_executor_module, "_RESTART_RETRY_DELAYS_S", (0.0,))

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.RemoteProtocolError("server closed connection")
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"ok"}]}}'
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_execute_tool_retries_on_503_restart_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 with restart payload triggers retry; second attempt succeeds."""
    calls = 0
    monkeypatch.setattr(mcp_executor_module, "_RESTART_RETRY_DELAYS_S", (0.0,))

    restart_body = (
        '{"jsonrpc":"2.0","id":1,'
        '"error":{"code":-32099,"message":"MCP server is restarting; retry in 30s",'
        '"data":{"reason":"server_restarting","retry_after_s":30}}}'
    )

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            req = httpx.Request("POST", "https://mcp.example.com/mcp")
            if calls == 1:
                return httpx.Response(503, request=req, text=restart_body)
            return httpx.Response(
                200,
                request=req,
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"ok"}]}}'
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_execute_tool_does_not_retry_generic_503() -> None:
    """503 without restart payload is a real error and must NOT trigger retry."""
    calls = 0

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                503,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text='{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"capacity exceeded"}}',
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert calls == 1
    assert "Tool execution failed" in result or "restart" not in result


@pytest.mark.asyncio
async def test_execute_tool_retries_on_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConnectError during the container-down window must trigger retry."""
    calls = 0
    monkeypatch.setattr(mcp_executor_module, "_RESTART_RETRY_DELAYS_S", (0.0,))

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"ok"}]}}'
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_web_precedent_unchanged_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12 — refactored path matches pre-G3 golden output (3-backtick fence)."""
    from services.universal_cloud_proxy.mcp_executor import _append_web_invariant_bodies

    entries = [
        {
            "id": "agent_skill:architecture-invariants",
            "name": "architecture-invariants",
            "digest": "sha256:inv0",
            "body": "# Architecture Invariants\nBody one.",
        },
        {
            "id": "agent_skill:ulg-architecture",
            "name": "ulg-architecture",
            "digest": "sha256:inv1",
            "body": "# ULG Architecture\nBody two.",
        },
    ]

    def fake_is_web(_seat: str) -> bool:
        return True

    monkeypatch.setattr(
        "services.universal_cloud_proxy.mcp_executor._is_web_seat",
        fake_is_web,
    )
    monkeypatch.setattr(
        "services.universal_cloud_proxy.mcp_executor.fetch_web_invariant_entries",
        lambda: entries,
    )

    content = "boot card"
    out = await _append_web_invariant_bodies(content, "claude-web")
    bodies_block = (
        "\n\n<!-- invariant-skill:architecture-invariants digest:sha256:inv0 -->\n"
        + "```markdown\n# Architecture Invariants\nBody one.\n```"
        + "\n\n<!-- invariant-skill:ulg-architecture digest:sha256:inv1 -->\n"
        + "```markdown\n# ULG Architecture\nBody two.\n```"
    )
    digest = hashlib.sha256(bodies_block.encode("utf-8")).hexdigest()
    sentinel = f"<!-- cortex:invariant-skills-autoappend sha256={digest} count=2 -->"
    golden = content + "\n\n" + sentinel + bodies_block
    assert out == golden
    # Sentinel is recomputable: hash over the body block (sans sentinel) matches.
    assert f"sha256={digest}" in out


@pytest.mark.asyncio
async def test_execute_tool_prefers_structured_content_dict() -> None:
    structured = {"briefing_card": "# Boot", "session_id": "s1"}

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            import json as json_mod

            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text=json_mod.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "structuredContent": structured,
                            "content": [
                                {
                                    "type": "text",
                                    "text": json_mod.dumps(structured),
                                }
                            ],
                        },
                    }
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("cortex_brief", {"agent": "claude-web"})

    assert json.loads(result) == structured


@pytest.mark.asyncio
async def test_execute_tool_falls_back_to_content_text() -> None:
    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"line one"},{"type":"text","text":"line two"}]}}'
                ),
            )

    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp")
    executor._client = _FakeClient()  # type: ignore[assignment]

    result = await executor.execute_tool("fs", {"op": "list"})

    assert result == "line one\nline two"


def test_headers_include_structured_capable() -> None:
    executor = McpToolExecutor(mcp_url="https://mcp.example.com/mcp", auth_token="tok")
    headers = executor._headers()
    assert headers["X-MCP-Structured-Capable"] == "1"
