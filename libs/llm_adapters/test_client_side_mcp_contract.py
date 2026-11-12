"""Client-side MCP tool-loop contract (C2)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from llm_adapters.client_side_mcp import client_loop_offenders


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object", "properties": {}}},
    }


def test_client_loop_offenders_empty_for_in_process_tools_only() -> None:
    tools = [_tool("cortex"), _tool("agent_bus")]
    assert client_loop_offenders(tools) == []


def test_client_loop_offenders_lists_non_in_process_function_names() -> None:
    tools = [_tool("cortex"), _tool("rag")]
    assert client_loop_offenders(tools) == ["rag"]


def test_client_loop_offenders_labels_bare_type_mcp() -> None:
    tools = [
        _tool("cortex"),
        {"type": "mcp", "server_label": "vortex", "server_url": "https://x"},
    ]
    assert client_loop_offenders(tools) == ["<type:mcp>"]


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
def test_client_side_mcp_consumers_file_grain_verify() -> None:
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
    text = (
        Path(__file__)
        .resolve()
        .parents[2]
        .joinpath(*module_path.split("/"))
        .read_text(encoding="utf-8")
    )
    declared = tuple_declared_in_source(text, "CONSUMERS")
    assert declared == ("cloud_proxy", "mcp", "stargate")
    slugs = {slug for slug, _src in nominations_for_lib_path(module_path)}
    assert slugs == set(declared)
    for slug in declared:
        status = verify_consumer_import(slug, module_path)
        assert status == "verified", f"{slug}: {status}"


@pytest.mark.offline
def test_llm_adapters_import_no_cycle_with_client_side_mcp() -> None:
    """core and each frontier adapter must import after client_side_mcp loads."""
    for mod in (
        "llm_adapters.core",
        "llm_adapters.anthropic.adapter",
        "llm_adapters.google",
        "llm_adapters.responses.adapter",
    ):
        sys.modules.pop(mod, None)
    importlib.import_module("llm_adapters.client_side_mcp")
    importlib.import_module("llm_adapters.core")
    importlib.import_module("llm_adapters.anthropic.adapter")
    importlib.import_module("llm_adapters.google")
    importlib.import_module("llm_adapters.responses.adapter")
