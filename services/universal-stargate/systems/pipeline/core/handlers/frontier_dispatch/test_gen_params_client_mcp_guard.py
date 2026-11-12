"""C2 guard reachability: ``build_frontier_request`` maps validate failures to 4xx."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from systems.pipeline.core.execution.errors import ClientSideMcpHttpRefusedError
from systems.pipeline.core.handlers.frontier_dispatch.admission_gate import (
    AdmissionResult,
)
from systems.pipeline.core.handlers.frontier_dispatch.gen_params import (
    build_frontier_request,
)


def _tool(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object", "properties": {}}},
    }


def _minimal_admission(*, tools: list[dict[str, Any]]) -> AdmissionResult:
    return AdmissionResult(
        opts={"generation_parameters": {"max_tokens": 1024}},
        agent=None,
        model="openai/gpt-5.4",
        model_entity_id="model:test",
        provider="openai",
        publish=lambda _e: None,
        mcp_enabled=True,
        server_tools_enabled=False,
        remote_mcp=False,
        max_turns=8,
        user_prompt="hello",
        boot_profile="light",
        tools=tools,
        system="",
        hydration_meta={},
    )


def test_build_frontier_request_refuses_mcp_server_tools_without_loop_flag() -> None:
    """Production path: mixed in-process + MCP tools raises before wire build."""
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

    admission = _minimal_admission(tools=[_tool("cortex"), _tool("rag")])
    with pytest.raises(ClientSideMcpHttpRefusedError) as exc_info:
        build_frontier_request(handler, step, context, admission)

    body = exc_info.value.to_dict()
    assert body["code"] == "client_side_mcp_http_refused"
    assert "rag" in body["offending_tools"]
