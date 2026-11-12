"""C2 guard: ``build_frontier_request`` refuses offenders on the client loop."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_seat.tools import in_process_client_tool_definitions
from llm_adapters.anthropic import AnthropicAdapter
from llm_adapters.google import GoogleAdapter
from llm_adapters.responses import ResponsesAPIAdapter

from systems.pipeline.core.execution.errors import ClientSideMcpHttpRefusedError
from systems.pipeline.core.handlers.frontier_dispatch.admission_gate import (
    AdmissionResult,
)
from systems.pipeline.core.handlers.frontier_dispatch.gen_params import (
    build_frontier_request,
)
from systems.pipeline.core.handlers.frontier_dispatch.tools import (
    resolve_dispatch_tool_set,
)


def _tool(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object", "properties": {}}},
    }


def _minimal_admission(
    *,
    tools: list[dict[str, Any]],
    remote_mcp: bool = False,
) -> AdmissionResult:
    return AdmissionResult(
        opts={"generation_parameters": {"max_tokens": 1024}},
        agent=None,
        model="openai/gpt-5.4",
        model_entity_id="model:test",
        provider="openai",
        publish=lambda _e: None,
        mcp_enabled=True,
        server_tools_enabled=False,
        remote_mcp=remote_mcp,
        max_turns=8,
        user_prompt="hello",
        boot_profile="light",
        tools=tools,
        system="",
        hydration_meta={},
    )


def _minimal_build_context() -> tuple[Any, Any, Any]:
    handler = SimpleNamespace(
        _build_generation_params=lambda _step, resolved_config: ({}, {})
    )
    step = SimpleNamespace(
        generation_parameters={},
        handler_inputs={},
        get_domain_field=lambda _k, default=None: default,
    )
    context = SimpleNamespace(
        options={},
        execution_id="exec-test-c2",
        pipeline=SimpleNamespace(id="pipe-test"),
    )
    return handler, step, context


def test_build_frontier_request_refuses_mcp_server_tools_on_client_loop() -> None:
    """Production path: mixed in-process + MCP tools raises before wire build."""
    handler, step, context = _minimal_build_context()
    admission = _minimal_admission(tools=[_tool("cortex"), _tool("rag")])
    with pytest.raises(ClientSideMcpHttpRefusedError) as exc_info:
        build_frontier_request(handler, step, context, admission)

    body = exc_info.value.to_dict()
    assert body["code"] == "client_side_mcp_http_refused"
    assert body["offending_tools"] == ["rag"]


def test_build_frontier_request_refuses_bare_type_mcp_on_client_loop() -> None:
    handler, step, context = _minimal_build_context()
    admission = _minimal_admission(
        tools=[
            _tool("cortex"),
            {"type": "mcp", "server_label": "vortex", "server_url": "https://x"},
        ],
    )
    with pytest.raises(ClientSideMcpHttpRefusedError) as exc_info:
        build_frontier_request(handler, step, context, admission)
    assert exc_info.value.to_dict()["offending_tools"] == ["<type:mcp>"]


def test_build_frontier_request_remote_mcp_empty_tools_no_raise() -> None:
    handler, step, context = _minimal_build_context()
    admission = _minimal_admission(tools=[], remote_mcp=True)
    bundle = build_frontier_request(handler, step, context, admission)
    assert bundle.req.mcp_tool_loop is False
    assert bundle.req.remote_mcp is True


@pytest.mark.asyncio
async def test_build_frontier_request_refuses_after_resolver_extra_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _catalog_with_rag() -> list[dict[str, Any]]:
        return [_tool("cortex"), _tool("rag")]

    monkeypatch.setattr(
        "agent_seat.tools.in_process_client_tool_definitions",
        _catalog_with_rag,
    )
    tools, _, _ = await resolve_dispatch_tool_set(
        mcp_enabled=True,
        remote_mcp=False,
        agent=None,
        model="openai/gpt-5.4",
        provider="openai",
        endpoint_request_id=None,
        system_prompt="sys",
        publish=lambda _e: None,
        execution_id="exec-resolver-c2",
    )
    handler, step, context = _minimal_build_context()
    admission = _minimal_admission(tools=tools)
    with pytest.raises(ClientSideMcpHttpRefusedError) as exc_info:
        build_frontier_request(handler, step, context, admission)
    assert "rag" in exc_info.value.to_dict()["offending_tools"]


def _adapters_for_pass_tests() -> list[tuple[str, object]]:
    return [
        ("anthropic", AnthropicAdapter(api_key="test-key")),
        ("google", GoogleAdapter(api_key="test-key")),
        (
            "responses",
            ResponsesAPIAdapter(
                api_key="test-key",
                base_url="https://api.example.com/v1",
                vendor="openai",
            ),
        ),
    ]


@pytest.mark.parametrize("label,adapter", _adapters_for_pass_tests())
def test_build_frontier_request_in_process_tools_adapter_wire(
    label: str,
    adapter: AnthropicAdapter | GoogleAdapter | ResponsesAPIAdapter,
) -> None:
    """In-process catalog sets mcp_tool_loop and each adapter builds a body."""
    handler, step, context = _minimal_build_context()
    tools = in_process_client_tool_definitions()
    admission = _minimal_admission(tools=tools)
    bundle = build_frontier_request(handler, step, context, admission)
    assert bundle.req.mcp_tool_loop is True
    adapter.build_frontier_request(bundle.req)
