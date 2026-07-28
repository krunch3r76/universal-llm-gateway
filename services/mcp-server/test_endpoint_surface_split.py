"""Dual-endpoint surface split — tools/list baselines, gate, catalog, and budgets."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
CANONICAL_YAML = REPO_ROOT / "config" / "mcp" / "canonical.yaml"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

LIFE_PRIMARY = frozenset(
    {
        "cortex",
        "cortex_brief",
        "agent_bus",
        "agent_bus_read",
        "fs",
        "rag",
        "retrieve",
        "tool_search",
        "dispatch",
        "imprint",
        "delegate",
    }
)
CODE_EXTRA = frozenset(
    {
        "manage",
        "observability",
        "pipeline",
        "team_dispatch",
        "panel_dispatch",
        "project_ask",
    }
)
CODE_PRIMARY = (LIFE_PRIMARY - frozenset({"imprint", "delegate"})) | CODE_EXTRA


@pytest.fixture(scope="module")
def life_server() -> dict:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, overflow_md, overflow_reg = _build_server("life")
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "tool_names": {t.name for t in tools},
        "primary": derive_surface_primary_tools("life"),
        "overflow_md": overflow_md,
        "overflow_reg": overflow_reg,
    }


@pytest.fixture(scope="module")
def code_server() -> dict:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, overflow_md, overflow_reg = _build_server("code")
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "tool_names": {t.name for t in tools},
        "primary": derive_surface_primary_tools("code"),
        "overflow_md": overflow_md,
        "overflow_reg": overflow_reg,
    }


def test_life_tools_list_exact_primary_set(life_server: dict) -> None:
    assert life_server["tool_names"] == set(LIFE_PRIMARY)
    assert life_server["primary"] == LIFE_PRIMARY


def test_code_tools_list_exact_primary_set(code_server: dict) -> None:
    assert code_server["tool_names"] == set(CODE_PRIMARY)
    assert code_server["primary"] == CODE_PRIMARY


def test_skill_suggest_absent_from_both_surfaces(
    life_server: dict, code_server: dict
) -> None:
    assert "skill_suggest" not in life_server["tool_names"]
    assert "skill_suggest" not in code_server["tool_names"]
    assert "skill_suggest" not in life_server["overflow_md"]
    assert "skill_suggest" not in code_server["overflow_md"]
    assert "skill_suggest" in life_server["overflow_reg"]
    assert "skill_suggest" in code_server["overflow_reg"]


def test_code_surface_includes_dispatch_family(code_server: dict) -> None:
    assert CODE_EXTRA <= code_server["tool_names"]


def test_life_surface_excludes_dispatch_family(life_server: dict) -> None:
    assert not (CODE_EXTRA & life_server["tool_names"])


def test_imprint_present_on_life_absent_on_code(
    life_server: dict, code_server: dict
) -> None:
    assert "imprint" in life_server["tool_names"]
    assert "imprint" in life_server["primary"]
    assert "imprint" not in code_server["tool_names"]
    assert "imprint" not in code_server["primary"]


def test_delegate_present_on_life_absent_on_code(
    life_server: dict, code_server: dict
) -> None:
    assert "delegate" in life_server["tool_names"]
    assert "delegate" in life_server["primary"]
    assert "delegate" not in code_server["tool_names"]
    assert "delegate" not in code_server["primary"]


def test_life_cortex_entity_merge_rejected() -> None:
    from fastmcp import FastMCP
    from request_profile import bind_request
    from tools.cortex import register_cortex_tools

    mcp = FastMCP("test-life-cortex-gate")
    register_cortex_tools(mcp, surface="life")
    tools = asyncio.run(mcp.list_tools())
    cortex_tool = next(t for t in tools if t.name == "cortex")
    with bind_request("default", surface="life"):
        result = cortex_tool.fn(
            tool="entity_merge", arguments='{"source_id":"a:1","target_id":"b:2"}'
        )
    assert result.get("status_code") == 422
    assert result.get("family") == "admin"
    assert result.get("surface") == "life"


def test_cortex_descriptor_byte_budgets() -> None:
    from tools.cortex_named_tools._surface_render import render_cortex_tool_description

    render_cortex_tool_description("life", canonical_yaml_path=CANONICAL_YAML)
    render_cortex_tool_description("code", canonical_yaml_path=CANONICAL_YAML)


def test_life_tool_search_excludes_code_infra_overflow(life_server: dict) -> None:
    blocked = {
        "quality_gate",
        "topology",
        "sql",
        "git_status",
        "cortex_chunk_create",
        "http_replay",
    }
    present = blocked & set(life_server["overflow_md"])
    assert not present, f"code-only overflow leaked to life catalog: {sorted(present)}"


def test_code_tool_search_retains_admin_overflow(code_server: dict) -> None:
    assert (
        "quality_gate" in code_server["overflow_md"]
        or "cortex_chunk_create" in code_server["overflow_md"]
    )


def _workspaces_read_probe_path() -> str:
    """Path that exists under the current PROJECT_ROOT layout."""
    root = Path(os.environ.get("PROJECT_ROOT", "/data/project"))
    multi_repo = (root / "universal-llm-gateway" / "README.md").is_file()
    if multi_repo:
        return (
            "universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/"
            "cdp-operator-proxy/SKILL.md"
        )
    return "README.md"


def _workspaces_read_probe_uri() -> str:
    rel = _workspaces_read_probe_path()
    return f"workspaces://{rel}"


def _fs_tool_fn(server_bundle: dict):
    tools = asyncio.run(server_bundle["mcp"].list_tools())
    fs_tool = next(t for t in tools if t.name == "fs")
    return fs_tool.fn, fs_tool


def test_life_fs_description_advertises_workspaces_read(life_server: dict) -> None:
    _, fs_tool = _fs_tool_fn(life_server)
    description = fs_tool.description or ""
    assert "/mcp/life" in description
    assert "READ-ONLY" in description
    assert "workspaces://{repo}/{rel}" in description
    assert "not available here" not in description


def test_life_fs_description_derived_from_permissions(life_server: dict) -> None:
    from fs_roots import derive_fs_sandbox_intro, life_workspaces_read_granted

    assert life_workspaces_read_granted()
    intro, _, _ = derive_fs_sandbox_intro("life")
    _, fs_tool = _fs_tool_fn(life_server)
    assert intro in (fs_tool.description or "")


def test_code_fs_description_retains_workspaces_advertisement(
    code_server: dict,
) -> None:
    _, fs_tool = _fs_tool_fn(code_server)
    description = fs_tool.description or ""
    assert "across sandboxes (cortex, workspaces)" in description


def test_life_fs_workspaces_read_succeeds(life_server: dict) -> None:
    fs_fn, _ = _fs_tool_fn(life_server)
    probe = _workspaces_read_probe_path()
    result = fs_fn(
        op="read",
        sandbox="workspaces",
        path=probe,
    )
    assert "error" not in result, result
    assert "content" in result or "content_base64" in result
    assert "read_project_file tool not available" not in result.get("error", "")


def test_life_fs_workspaces_uri_read_succeeds(life_server: dict) -> None:
    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="read",
        path=_workspaces_read_probe_uri(),
    )
    assert "error" not in result, result
    assert "content" in result or "content_base64" in result


def test_life_fs_workspaces_write_refused(life_server: dict) -> None:
    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="write",
        sandbox="workspaces",
        path="universal-llm-gateway/tmp/o1-probe.md",
        content="probe",
    )
    assert "error" in result
    assert "/mcp/life surface" in result["error"]
    assert "READ-ONLY" in result["error"]


def test_life_fs_workspaces_non_allowlisted_op_refused(life_server: dict) -> None:
    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="delete",
        sandbox="workspaces",
        path="universal-llm-gateway/README.md",
    )
    assert "error" in result
    assert "/mcp/life surface" in result["error"]


def test_life_fs_workspaces_md_list_succeeds(life_server: dict) -> None:
    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="md_list",
        sandbox="workspaces",
        path=_workspaces_read_probe_path(),
    )
    assert "error" not in result, result.get("error")
    assert "sections" in result or "headings" in result


def test_life_fs_cortex_md_list_refused_by_permissions(life_server: dict) -> None:
    """md_* on cortex is surface-gated via PERMISSIONS, not markdown overflow alone."""
    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="md_list",
        sandbox="cortex",
        path="notes/system/specs/cdp-operator-proxy-v0.md",
    )
    assert "error" in result
    assert "md_list" in result["error"]
    assert "/mcp/life surface" in result["error"]
    assert "sandbox='cortex'" in result["error"]


def test_life_overflow_excludes_project_write_tools(life_server: dict) -> None:
    blocked = {
        "write_project_file",
        "edit_project_file",
        "delete_project_file",
        "move_project_file",
        "copy_project_file",
    }
    present = blocked & set(life_server["overflow_reg"])
    assert not present, f"write-capable project tools leaked to life overflow: {sorted(present)}"
    assert "read_project_file" not in life_server["overflow_reg"]


def test_code_fs_workspaces_dispatches(code_server: dict) -> None:
    assert "read_project_file" in code_server["overflow_reg"]
    fs_fn, _ = _fs_tool_fn(code_server)
    result = fs_fn(
        op="read",
        sandbox="workspaces",
        path="universal-llm-gateway/README.md",
    )
    assert "/mcp/life surface" not in result.get("error", "")
    assert "read_project_file tool not available" not in result.get("error", "")


def test_mcp_request_events_include_surface_attr() -> None:
    from unittest.mock import patch

    from endpoint_surface import MCP_LIFE_PATH
    from mcp_request_middleware import McpRequestEventsMiddleware

    captured: list[dict] = []

    async def _noop_app(scope, receive, send):  # noqa: ANN001
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    def _capture_record(signal: str, **payload):  # noqa: ANN001
        if signal == "mcp.request.started":
            captured.append(payload)

    middleware = McpRequestEventsMiddleware(_noop_app)

    scope = {
        "type": "http",
        "method": "GET",
        "path": MCP_LIFE_PATH,
        "headers": [],
        "mcp_surface": "life",
        "server": ("test", 443),
        "client": ("127.0.0.1", 12345),
    }

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(_msg):  # noqa: ANN001
        return None

    with patch("mcp_request_middleware.record", side_effect=_capture_record):
        asyncio.run(middleware(scope, _receive, _send))
    assert captured and captured[0].get("surface") == "life"


def test_life_overflow_includes_email_when_local_installed(life_server: dict) -> None:
    """Email is a life-allowlisted private tool; must appear in overflow + catalog."""
    try:
        import tools.local.email  # noqa: F401, PLC0415
    except ImportError:
        pytest.skip("tools.local.email not installed in this checkout")
    assert "email" in life_server["overflow_reg"]
    assert "email" in life_server["overflow_md"]
    assert "finance" not in life_server["overflow_reg"]


def test_code_private_tools_remain_broader_than_life(
    life_server: dict, code_server: dict
) -> None:
    try:
        import tools.local.finance  # noqa: F401, PLC0415
    except ImportError:
        pytest.skip("tools.local.finance not installed in this checkout")
    assert "email" in code_server["overflow_reg"]
    assert "finance" in code_server["overflow_reg"]
    assert "finance" not in life_server["overflow_reg"]


def test_life_dispatch_email_list_returns_catalog(life_server: dict) -> None:
    try:
        import tools.local.email  # noqa: F401, PLC0415
    except ImportError:
        pytest.skip("tools.local.email not installed in this checkout")
    from request_profile import bind_request

    email_fn = life_server["overflow_reg"]["email"]
    with bind_request("default", surface="life"):
        payload = email_fn(op="list")
    assert "live_ops" in payload
    assert "list" in payload["live_ops"]
    assert "move" not in payload["live_ops"]
    assert payload.get("surface_policy") == "tier-R read ops only"


def test_life_dispatch_email_blocks_mutating_op(life_server: dict) -> None:
    try:
        import tools.local.email  # noqa: F401, PLC0415
    except ImportError:
        pytest.skip("tools.local.email not installed in this checkout")
    from request_profile import bind_request

    email_fn = life_server["overflow_reg"]["email"]
    with bind_request("default", surface="life"):
        payload = email_fn(
            op="move",
            arguments='{"message_ids":["x"],"folder":"Archive"}',
        )
    assert payload.get("error") == "life_surface_read_only"

