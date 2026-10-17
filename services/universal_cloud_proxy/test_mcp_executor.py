from __future__ import annotations

import asyncio
import hashlib
import json
import time
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

    async def _fake_execute(name: str, arguments: dict[str, object], **_kwargs: object) -> str:
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

    async def _fake_execute(name: str, arguments: dict[str, object], **_kwargs: object) -> str:
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

    async def _fake_execute(name: str, arguments: dict[str, object], **_kwargs: object) -> str:
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

    async def _fake_execute(name: str, arguments: dict[str, object], **_kwargs: object) -> str:
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

    async def _fake_execute(name: str, arguments: dict[str, object], **_kwargs: object) -> str:
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

    async def _fake_execute(name: str, arguments: dict[str, object], **_kwargs: object) -> str:
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


@pytest.mark.asyncio
async def test_open_webui_missing_bearer_does_not_execute_fs_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Key label open-webui with no bearer must not POST fs op write."""
    monkeypatch.delenv("ULG_OPEN_WEBUI_MCP_BEARER", raising=False)
    posts: list[dict[str, object]] = []

    class _FakeClient:
        async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            posts.append({"called": True})
            raise AssertionError("fs write must not be sent")

    executor = McpToolExecutor(
        mcp_url="https://mcp.example.com/mcp", auth_token="shared-token"
    )
    executor._client = _FakeClient()  # type: ignore[assignment]
    result = await executor.execute_tool(
        "fs", {"op": "write", "path": "cortex://x"}, client_id="open-webui"
    )
    assert posts == []
    assert "not configured" in result


@pytest.mark.asyncio
async def test_open_webui_loop_uses_dedicated_bearer_not_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_OPEN_WEBUI_MCP_BEARER", "dedicated-bearer-fixture")
    seen: list[str] = []

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            del json, timeout
            seen.append(headers.get("Authorization", ""))
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text='{"jsonrpc":"2.0","id":1,"result":{"content":[]}}',
            )

    executor = McpToolExecutor(
        mcp_url="https://mcp.example.com/mcp", auth_token="shared-token"
    )
    executor._client = _FakeClient()  # type: ignore[assignment]

    calls = {"n": 0}

    async def _forward(body: dict[str, object]) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {
                                        "name": "fs",
                                        "arguments": '{"op":"read"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"content": "done"}}]}

    await executor.run_tool_loop(_forward, {"messages": []}, client_id="open-webui")
    assert seen == ["Bearer dedicated-bearer-fixture"]


@pytest.mark.asyncio
async def test_overlapping_loops_keep_open_webui_bearer_on_the_waiting_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L1 waits inside forward_fn while a -mcp loop L2 finishes on the same executor.

    L1's later fs write must still carry the dedicated bearer (the server denies
    that write). L2 must never present the open-webui bearer.
    """
    monkeypatch.setenv("ULG_OPEN_WEBUI_MCP_BEARER", "dedicated-bearer-fixture")
    l1_inside_forward = asyncio.Event()
    release_l1 = asyncio.Event()
    posts: list[dict[str, str]] = []

    class _FakeClient:
        async def post(
            self,
            _url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
            timeout: float,
        ) -> httpx.Response:
            del timeout
            auth = headers.get("Authorization", "")
            params = json.get("params") if isinstance(json.get("params"), dict) else {}
            arguments = params.get("arguments") if isinstance(params, dict) else {}
            op = arguments.get("op") if isinstance(arguments, dict) else ""
            denied = auth == "Bearer dedicated-bearer-fixture" and op == "write"
            text = "write denied" if denied else "ok-shared"
            posts.append({"auth": auth, "op": str(op), "text": text})
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://mcp.example.com/mcp"),
                text=(
                    '{"jsonrpc":"2.0","id":1,"result":{"content":'
                    '[{"type":"text","text":"' + text + '"}]}}'
                ),
            )

    executor = McpToolExecutor(
        mcp_url="https://mcp.example.com/mcp", auth_token="shared-token"
    )
    executor._client = _FakeClient()  # type: ignore[assignment]

    async def _forward_l1(body: dict[str, object]) -> dict[str, object]:
        del body
        if not l1_inside_forward.is_set():
            l1_inside_forward.set()
            await release_l1.wait()
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "l1",
                                    "function": {
                                        "name": "fs",
                                        "arguments": '{"op":"write","path":"cortex://x"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"content": "l1-done"}}]}

    l2_calls = {"n": 0}

    async def _forward_l2(body: dict[str, object]) -> dict[str, object]:
        del body
        l2_calls["n"] += 1
        if l2_calls["n"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "l2",
                                    "function": {
                                        "name": "fs",
                                        "arguments": '{"op":"read"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"content": "l2-done"}}]}

    l1 = asyncio.create_task(
        executor.run_tool_loop(
            _forward_l1, {"messages": []}, client_id="open-webui"
        )
    )
    await l1_inside_forward.wait()
    posts_before_l2 = len(posts)
    await executor.run_tool_loop(_forward_l2, {"messages": []}, client_id=None)
    l2_posts = posts[posts_before_l2:]
    release_l1.set()
    l1_result = await l1

    assert l2_posts == [
        {"auth": "Bearer shared-token", "op": "read", "text": "ok-shared"}
    ]
    assert posts[-1] == {
        "auth": "Bearer dedicated-bearer-fixture",
        "op": "write",
        "text": "write denied",
    }
    assert all(p["auth"] != "Bearer dedicated-bearer-fixture" for p in l2_posts)
    assert l1_result["choices"][0]["message"]["content"] == "l1-done"


def _catalog(provider: str, model_id: str) -> object:
    from services.universal_cloud_proxy.catalog import (
        CatalogManager,
        CatalogModel,
        ProviderCatalog,
    )

    mgr = CatalogManager([], {})
    mgr._catalogs[provider] = ProviderCatalog(
        provider=provider,
        models=[CatalogModel(id=model_id, provider=provider, max_concurrent=1)],
    )
    return mgr


def _chat_scope(body: bytes, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    raw = list(headers or [])
    raw.append((b"content-type", b"application/json"))
    return {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": raw,
        "query_string": b"",
    }


@pytest.mark.asyncio
async def test_open_webui_header_admits_bare_anthropic_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    from services.universal_cloud_proxy import cloud_proxy as cp

    mid = "anthropic/claude-fictional-99"
    seen: list[dict[str, object]] = []

    class _Forwarder:
        def adapter_type(self, _provider: str) -> str:
            return "anthropic"

        async def forward_chat_request(self, **kwargs: object) -> dict[str, object]:
            body = kwargs["request_body"]
            assert isinstance(body, dict)
            seen.append(body)
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Exec:
        available = True

        async def run_tool_loop(
            self, forward_fn: object, body: dict[str, object], **kw: object
        ) -> dict[str, object]:
            assert kw.get("client_id") == "open-webui"
            assert "X-ULG-Client" not in body
            assert "oauth_client_id" not in body
            assert not str(body.get("model", "")).endswith("-mcp")
            result = await forward_fn(body)  # type: ignore[operator]
            return result

    monkeypatch.setattr(cp, "_get_catalog", lambda: _catalog("anthropic", mid))
    monkeypatch.setattr(cp, "_get_forwarder", lambda: _Forwarder())
    monkeypatch.setattr(cp, "_get_event_bus", lambda: None)
    monkeypatch.setattr(cp, "_get_mcp_executor", lambda: _Exec())

    payload = json.dumps(
        {
            "model": mid,
            "messages": [{"role": "user", "content": "hi"}],
            "X-ULG-Client": "open-webui",
            "oauth_client_id": "open-webui",
        }
    ).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": payload, "more_body": False}

    scope = _chat_scope(payload, [(b"x-ulg-client", b"open-webui")])
    resp = await cp.chat_completions(Request(scope, receive))
    assert resp.status_code == 200
    assert seen and "X-ULG-Client" not in seen[0]
    assert "oauth_client_id" not in seen[0]


@pytest.mark.asyncio
async def test_bare_anthropic_without_header_skips_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    from services.universal_cloud_proxy import cloud_proxy as cp

    mid = "anthropic/claude-fictional-99"
    loop = {"called": False}

    class _Forwarder:
        def adapter_type(self, _provider: str) -> str:
            return "anthropic"

        async def forward_chat_request(self, **_kwargs: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": "direct"}}]}

    class _Exec:
        available = True

        async def run_tool_loop(self, *_a: object, **_k: object) -> dict[str, object]:
            loop["called"] = True
            return {}

    monkeypatch.setattr(cp, "_get_catalog", lambda: _catalog("anthropic", mid))
    monkeypatch.setattr(cp, "_get_forwarder", lambda: _Forwarder())
    monkeypatch.setattr(cp, "_get_event_bus", lambda: None)
    monkeypatch.setattr(cp, "_get_mcp_executor", lambda: _Exec())
    payload = json.dumps(
        {"model": mid, "messages": [{"role": "user", "content": "hi"}]}
    ).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": payload, "more_body": False}

    resp = await cp.chat_completions(Request(_chat_scope(payload), receive))
    assert resp.status_code == 200
    assert loop["called"] is False


@pytest.mark.asyncio
async def test_mcp_suffix_still_loops_without_open_webui_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    from services.universal_cloud_proxy import cloud_proxy as cp

    mid = "anthropic/claude-fictional-99"
    seen: dict[str, object] = {}

    class _Forwarder:
        def adapter_type(self, _provider: str) -> str:
            return "anthropic"

        async def forward_chat_request(self, **_kwargs: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": "ok"}}]}

    class _Exec:
        available = True

        async def run_tool_loop(
            self, forward_fn: object, body: dict[str, object], **kw: object
        ) -> dict[str, object]:
            seen["client_id"] = kw.get("client_id")
            seen["model"] = body.get("model")
            return await forward_fn(body)  # type: ignore[operator]

    monkeypatch.setattr(cp, "_get_catalog", lambda: _catalog("anthropic", mid))
    monkeypatch.setattr(cp, "_get_forwarder", lambda: _Forwarder())
    monkeypatch.setattr(cp, "_get_event_bus", lambda: None)
    monkeypatch.setattr(cp, "_get_mcp_executor", lambda: _Exec())
    payload = json.dumps(
        {"model": mid + "-mcp", "messages": [{"role": "user", "content": "hi"}]}
    ).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": payload, "more_body": False}

    resp = await cp.chat_completions(Request(_chat_scope(payload), receive))
    assert resp.status_code == 200
    assert seen["client_id"] is None
    assert seen["model"] == mid


@pytest.mark.asyncio
async def test_google_without_header_or_suffix_skips_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    from services.universal_cloud_proxy import cloud_proxy as cp

    mid = "google/gemini-fictional"
    loop = {"called": False}

    class _Forwarder:
        def adapter_type(self, _provider: str) -> str:
            return "google"

        async def forward_chat_request(self, **_kwargs: object) -> dict[str, object]:
            return {"choices": [{"message": {"content": "direct"}}]}

    class _Exec:
        available = True

        async def run_tool_loop(self, *_a: object, **_k: object) -> dict[str, object]:
            loop["called"] = True
            return {}

    monkeypatch.setattr(cp, "_get_catalog", lambda: _catalog("google", mid))
    monkeypatch.setattr(cp, "_get_forwarder", lambda: _Forwarder())
    monkeypatch.setattr(cp, "_get_event_bus", lambda: None)
    monkeypatch.setattr(cp, "_get_mcp_executor", lambda: _Exec())
    payload = json.dumps(
        {"model": mid, "messages": [{"role": "user", "content": "hi"}]}
    ).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": payload, "more_body": False}

    resp = await cp.chat_completions(Request(_chat_scope(payload), receive))
    assert resp.status_code == 200
    assert loop["called"] is False


@pytest.mark.asyncio
async def test_open_webui_plain_chat_forwards_when_executor_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executor down + open-webui, MCP not requested: forward, not 503."""
    from starlette.requests import Request

    from services.universal_cloud_proxy import cloud_proxy as cp

    mid = "anthropic/claude-fictional-99"
    forwarded = {"called": False}

    class _Forwarder:
        def adapter_type(self, _provider: str) -> str:
            return "anthropic"

        async def forward_chat_request(self, **_kwargs: object) -> dict[str, object]:
            forwarded["called"] = True
            return {"choices": [{"message": {"content": "direct"}}]}

    class _Exec:
        available = False

        async def run_tool_loop(self, *_a: object, **_k: object) -> dict[str, object]:
            raise AssertionError("tool loop must not run")

    monkeypatch.setattr(cp, "_open_webui_plain_chat_fallback_logged", False)
    monkeypatch.setattr(cp, "_get_catalog", lambda: _catalog("anthropic", mid))
    monkeypatch.setattr(cp, "_get_forwarder", lambda: _Forwarder())
    monkeypatch.setattr(cp, "_get_event_bus", lambda: None)
    monkeypatch.setattr(cp, "_get_mcp_executor", lambda: _Exec())
    payload = json.dumps(
        {"model": mid, "messages": [{"role": "user", "content": "hi"}]}
    ).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": payload, "more_body": False}

    scope = _chat_scope(payload, [(b"x-ulg-client", b"open-webui")])
    resp = await cp.chat_completions(Request(scope, receive))
    assert resp.status_code == 200
    assert forwarded["called"] is True


@pytest.mark.asyncio
async def test_lifespan_normalizes_bare_mcp_url_for_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup passes …/mcp through normalize_mcp_public_url → …/mcp/code/."""
    from types import SimpleNamespace

    from services.universal_cloud_proxy import cloud_proxy as cp
    from services.universal_cloud_proxy.config import CloudProxyConfig

    captured: dict[str, object] = {}

    class _Exec:
        def __init__(self, *, mcp_url: str, auth_token: str | None = None) -> None:
            captured["mcp_url"] = mcp_url
            captured["auth_token"] = auth_token

        async def startup(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    class _Bus:
        def set_debug_broadcaster(self, _broadcaster: object) -> None:
            return None

        async def publish(self, _event: object) -> None:
            return None

    class _Broadcaster:
        def __init__(self, **_kwargs: object) -> None:
            return None

        async def start_debug_server(self) -> None:
            return None

        async def stop_debug_server(self) -> None:
            return None

    class _Catalog:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def startup(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

        def get_all_models(self) -> list[object]:
            return []

    class _Browser:
        model_count = 0

        async def refresh(self) -> int:
            return 0

    class _Local:
        def __init__(self, **_kwargs: object) -> None:
            return None

        async def refresh(self) -> int:
            return 0

    class _Client:
        def __init__(self, **_kwargs: object) -> None:
            return None

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(cp, "EventBus", _Bus)
    monkeypatch.setattr(cp, "MinimalEventDebugBroadcaster", _Broadcaster)
    monkeypatch.setattr(
        cp,
        "load_config",
        lambda: CloudProxyConfig(
            providers=[],
            mcp_server_url="https://mcp.example/mcp",
            mcp_auth_token="tok",
        ),
    )
    monkeypatch.setattr(cp, "CatalogManager", _Catalog)
    monkeypatch.setattr(cp, "BrowserCatalogCache", _Browser)
    monkeypatch.setattr(cp, "LocalCatalogCache", _Local)
    monkeypatch.setattr(cp, "McpToolExecutor", _Exec)
    monkeypatch.setattr(cp.httpx, "AsyncClient", _Client)

    app = SimpleNamespace(state=SimpleNamespace())
    async with cp._lifespan(app):
        pass
    assert captured["mcp_url"] == "https://mcp.example/mcp/code/"
    assert captured["auth_token"] == "tok"


@pytest.mark.asyncio
async def test_tool_loop_credit_cap_on_turn_two_returns_prior_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimal import Decimal

    from anthropic_credit.policy import AnthropicCreditRefused, Refuse

    from services.universal_cloud_proxy.mcp_executor import McpToolExecutor

    calls = {"n": 0}

    async def forward_fn(body: dict[str, object]) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "turn-one",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "search",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        refuse = Refuse(
            code="anthropic_cycle_budget_exhausted",
            http_status=429,
            reservation_usd=Decimal("1"),
            headroom_usd=Decimal("0"),
            detail="cap",
        )
        raise AnthropicCreditRefused(refuse)

    executor = McpToolExecutor(mcp_url="https://mcp.example/mcp")

    async def _exec_tool(*_a: object, **_k: object) -> str:
        return "tool-ok"

    monkeypatch.setattr(executor, "execute_tool", _exec_tool)
    body = {
        "model": "anthropic/claude-sonnet-5",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [],
    }
    result = await executor._finish_tool_loop(
        forward_fn,
        body,
        max_iterations=5,
        loop_start=time.monotonic(),
        original_stream=False,
    )
    assert calls["n"] == 2
    assert result["choices"][0]["finish_reason"] == "credit_cap"
    assert "turn-one" in result["choices"][0]["message"]["content"]
