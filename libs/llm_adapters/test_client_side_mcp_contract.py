"""Client-side MCP tool-loop contract (C2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_adapters.anthropic import AnthropicAdapter
from llm_adapters.client_side_mcp import (
    client_side_mcp_tool_loop_allowed,
    validate_client_side_mcp_tool_loop,
)
from llm_adapters.core import FrontierRequest
from llm_adapters.google import GoogleAdapter
from llm_adapters.responses import ResponsesAPIAdapter


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object", "properties": {}}},
    }


def test_client_side_loop_allowed_for_in_process_tools_only() -> None:
    tools = [_tool("cortex"), _tool("agent_bus")]
    assert client_side_mcp_tool_loop_allowed(tools=tools, remote_mcp=False) is True


def test_client_side_loop_disallowed_with_mcp_server_tool_names() -> None:
    tools = [_tool("cortex"), _tool("rag")]
    assert client_side_mcp_tool_loop_allowed(tools=tools, remote_mcp=False) is False


def test_validate_raises_when_loop_would_dial_mcp() -> None:
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="openai/gpt-5.4",
        tools=[_tool("fs")],
        mcp_tool_loop=True,
        remote_mcp=False,
    )
    with pytest.raises(ValueError, match="remote_mcp"):
        validate_client_side_mcp_tool_loop(req)


def test_validate_allows_remote_mcp_path_unchanged() -> None:
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="openai/gpt-5.4",
        tools=[{"type": "mcp", "server_label": "vortex", "server_url": "https://x"}],
        mcp_tool_loop=False,
        remote_mcp=True,
    )
    validate_client_side_mcp_tool_loop(req)


def _adapters_for_guard_tests() -> list[tuple[str, object]]:
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


@pytest.mark.parametrize("label,adapter", _adapters_for_guard_tests())
def test_adapter_build_frontier_request_refuses_mcp_server_tool_loop(
    label: str,
    adapter: AnthropicAdapter | GoogleAdapter | ResponsesAPIAdapter,
) -> None:
    """Each native adapter path must refuse before any outbound provider call."""
    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="openai/gpt-5.4",
        tools=[_tool("brave_search")],
        mcp_tool_loop=True,
        remote_mcp=False,
        max_tokens=256,
    )
    with pytest.raises(ValueError, match="remote_mcp"):
        adapter.build_frontier_request(req)


@pytest.mark.offline
def test_client_side_mcp_module_import_graph_hygiene() -> None:
    """Import closure must not pull agent_seat, services, or cursor_bridge."""
    import ast

    root = Path(__file__).resolve().parents[2]
    path = root / "libs/llm_adapters/client_side_mcp.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "agent_seat" not in imported
    assert "services" not in imported
    assert "cursor_bridge" not in imported


@pytest.mark.offline
def test_client_side_mcp_consumers_declared_and_package_import_verified() -> None:
    from implement_admission.consumer_import_verify import (
        clear_verify_caches,
        verify_consumer_import,
    )
    from implement_admission.injector_map import (
        nominations_for_lib_path,
        tuple_declared_in_source,
    )

    clear_verify_caches()
    module_path = "libs/llm_adapters/client_side_mcp.py"
    pkg_path = "libs/llm_adapters/__init__.py"
    text = Path(__file__).resolve().parents[2].joinpath(*module_path.split("/")).read_text(
        encoding="utf-8"
    )
    declared = tuple_declared_in_source(text, "CONSUMERS")
    assert declared == ("cloud_proxy", "gateway", "mcp", "stargate")
    slugs = {slug for slug, _src in nominations_for_lib_path(module_path)}
    assert slugs == set(declared)
    for slug in declared:
        assert verify_consumer_import(slug, pkg_path) == "verified"
