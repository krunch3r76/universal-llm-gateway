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
        "cursor_request",
        "operator_request",
        "fs",
        "rag",
        "retrieve",
        "tool_search",
        "dispatch",
        "fleet_liveness",
        "imprint",
        "delegate",
        "notify",
        "cse_session",
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
# imprint/delegate/notify are life-only (canonical domain_endpoints); trigger is
# overflow/relay, never surface_primary (d946 test overclaim; a505 promoted
# cursor_request into both primaries without updating this gate).
CODE_PRIMARY = (LIFE_PRIMARY - frozenset({"imprint", "delegate", "notify"})) | CODE_EXTRA


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


def test_operator_proxy_life_surface_legal_tools_matches_derive() -> None:
    """Gate hand-maintained LIFE_SURFACE_LEGAL_TOOLS against derive (arc 6655)."""
    from claude_bundles.operator_proxy_mission import LIFE_SURFACE_LEGAL_TOOLS
    from endpoint_surface import derive_surface_primary_tools

    assert LIFE_SURFACE_LEGAL_TOOLS == derive_surface_primary_tools("life")


def test_operator_proxy_forbidden_tools_matches_code_extra_derive() -> None:
    """Gate LIFE_SURFACE_FORBIDDEN_TOOLS against derive_code_extra_primary_tools."""
    from claude_bundles.operator_proxy_mission import LIFE_SURFACE_FORBIDDEN_TOOLS
    from endpoint_surface import derive_code_extra_primary_tools

    assert LIFE_SURFACE_FORBIDDEN_TOOLS == derive_code_extra_primary_tools()


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


def test_life_workspaces_write_guard_unset_life_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC1 — refusal preserved when LIFE_PROJECT_ROOT is unset."""
    from fs_roots import (
        life_workspaces_write_enabled,
        permission_refusal,
        permitted_ops,
    )

    monkeypatch.delenv("LIFE_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    assert not life_workspaces_write_enabled()
    assert "write" not in permitted_ops("life", "workspaces")
    refusal = permission_refusal("life", "workspaces", "write")
    assert refusal is not None
    assert "READ-ONLY" in refusal["error"]


def test_life_workspaces_write_guard_equal_roots_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC4 — LIFE_PROJECT_ROOT == PROJECT_ROOT must fail closed."""
    from fs_roots import life_workspaces_write_enabled, permission_refusal

    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(tmp_path))
    assert not life_workspaces_write_enabled()
    refusal = permission_refusal("life", "workspaces", "write")
    assert refusal is not None
    assert "READ-ONLY" in refusal["error"]


def test_life_fs_workspaces_write_lands_in_life_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    life_server: dict,
) -> None:
    """AC2 — life write resolves to LIFE_PROJECT_ROOT, not PROJECT_ROOT."""
    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    (shared / "universal-llm-gateway").mkdir(parents=True)
    (life_root / "universal-llm-gateway").mkdir(parents=True)
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))

    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="write",
        sandbox="workspaces",
        path="universal-llm-gateway/tmp/life-seat-probe.md",
        content="life-probe",
    )
    assert "error" not in result, result
    life_file = life_root / "universal-llm-gateway" / "tmp" / "life-seat-probe.md"
    shared_file = shared / "universal-llm-gateway" / "tmp" / "life-seat-probe.md"
    assert life_file.read_text() == "life-probe"
    assert not shared_file.exists()


def test_life_fs_workspaces_traversal_cannot_reach_shared_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    life_server: dict,
) -> None:
    """AC2 — .. traversal must not resolve into PROJECT_ROOT."""
    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    secret = shared / "secret.txt"
    secret.write_text("shared-secret")
    (life_root / "universal-llm-gateway").mkdir(parents=True)
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))

    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="write",
        sandbox="workspaces",
        path="universal-llm-gateway/../../secret.txt",
        content="pwn",
    )
    assert "error" in result
    assert secret.read_text() == "shared-secret"


def test_code_fs_workspaces_write_unchanged_with_life_root_configured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    code_server: dict,
) -> None:
    """AC3 — code surface keeps using PROJECT_ROOT."""
    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    (shared / "universal-llm-gateway").mkdir(parents=True)
    (life_root / "universal-llm-gateway").mkdir(parents=True)
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))

    fs_fn, _ = _fs_tool_fn(code_server)
    result = fs_fn(
        op="write",
        sandbox="workspaces",
        path="universal-llm-gateway/tmp/code-probe.md",
        content="code-probe",
    )
    assert "error" not in result, result
    shared_file = shared / "universal-llm-gateway" / "tmp" / "code-probe.md"
    life_file = life_root / "universal-llm-gateway" / "tmp" / "code-probe.md"
    assert shared_file.read_text() == "code-probe"
    assert not life_file.exists()


def test_fs_root_for_code_workspaces_unchanged_with_life_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC3 — fs_root_for('code','workspaces') ignores LIFE_PROJECT_ROOT."""
    from fs_roots import fs_root_for

    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))
    assert fs_root_for("code", "workspaces") == shared.resolve()
    assert fs_root_for("life", "workspaces") == life_root.resolve()


def test_life_fs_description_when_write_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC5 — derive_fs_sandbox_intro tracks write grant."""
    from fs_roots import derive_fs_sandbox_intro, life_workspaces_write_enabled

    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))
    assert life_workspaces_write_enabled()
    intro, _, _ = derive_fs_sandbox_intro("life")
    assert "life worktree" in intro
    assert "READ-ONLY" not in intro


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
    assert "workspaces_resolved_root" in result
    assert (
        "workspaces_read_at_head" in result or "workspaces_head_unknown" in result
    )


def test_life_workspaces_enabled_grant_preserves_md_ops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC2 — enabling write must not drop md_* from permitted_ops."""
    from fs_roots import LIFE_WORKSPACES_READ_OPS, permitted_ops

    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))

    granted = permitted_ops("life", "workspaces")
    for md_op in ("md_read", "md_list", "md_to_dict"):
        assert md_op in granted, (
            f"{md_op} dropped from life workspaces grant when write enabled; "
            f"grant={sorted(granted)}"
        )
    assert LIFE_WORKSPACES_READ_OPS <= granted


def test_life_workspaces_grant_includes_recent_commits() -> None:
    """Life catch-up query is a workspaces read; git_* stays overflow-banned."""
    from fs_roots import LIFE_WORKSPACES_READ_OPS, permitted_ops

    assert "recent_commits" in LIFE_WORKSPACES_READ_OPS
    assert "recent_commits" in permitted_ops("life", "workspaces")
    assert "recent_commits" in permitted_ops("code", "workspaces")
    assert "git_status" not in permitted_ops("life", "workspaces")
    assert "git_log" not in permitted_ops("life", "workspaces")


def test_life_workspaces_enabled_grant_excludes_delete_move(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC6 — explicit grant must not inherit delete/move from _WORKSPACES_OPS."""
    from fs_roots import permitted_ops

    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))

    granted = permitted_ops("life", "workspaces")
    assert "delete" not in granted
    assert "move" not in granted


def test_life_fs_workspaces_read_reports_root_and_head(life_server: dict) -> None:
    """AC3 — plain read carries structured root + HEAD visibility."""
    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="read",
        sandbox="workspaces",
        path=_workspaces_read_probe_path(),
    )
    assert "error" not in result, result
    assert "workspaces_resolved_root" in result
    assert (
        "workspaces_read_at_head" in result or "workspaces_head_unknown" in result
    )


def test_life_fs_workspaces_md_read_reports_root_and_head(life_server: dict) -> None:
    """AC3 — md_read carries structured root + HEAD visibility."""
    fs_fn, _ = _fs_tool_fn(life_server)
    result = fs_fn(
        op="md_read",
        sandbox="workspaces",
        path=_workspaces_read_probe_path(),
    )
    assert "error" not in result, result
    assert "workspaces_resolved_root" in result
    assert (
        "workspaces_read_at_head" in result or "workspaces_head_unknown" in result
    )


def test_life_read_and_md_read_share_resolved_root_read_only(
    life_server: dict,
) -> None:
    """AC4 — read and md_read resolve to the same tree when write is off."""
    fs_fn, _ = _fs_tool_fn(life_server)
    path = _workspaces_read_probe_path()
    read_result = fs_fn(op="read", sandbox="workspaces", path=path)
    md_result = fs_fn(op="md_read", sandbox="workspaces", path=path)
    assert "error" not in read_result, read_result
    assert "error" not in md_result, md_result
    assert (
        read_result["workspaces_resolved_root"]
        == md_result["workspaces_resolved_root"]
    )


def test_life_read_and_md_read_share_resolved_root_when_write_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    life_server: dict,
) -> None:
    """AC4 — read and md_read share fs_root_for bind path when write is on."""
    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    (shared / "universal-llm-gateway").mkdir(parents=True)
    life_repo = life_root / "universal-llm-gateway"
    life_repo.mkdir(parents=True)
    probe = life_repo / "tmp" / "life-resolution-probe.md"
    probe.parent.mkdir(parents=True)
    probe.write_text("# Probe\n\nlife-tree-only\n", encoding="utf-8")
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))

    fs_fn, _ = _fs_tool_fn(life_server)
    rel = "universal-llm-gateway/tmp/life-resolution-probe.md"
    read_result = fs_fn(op="read", sandbox="workspaces", path=rel)
    md_result = fs_fn(op="md_read", sandbox="workspaces", path=rel)
    assert "error" not in read_result, read_result
    assert "error" not in md_result, md_result
    assert read_result.get("content") == "# Probe\n\nlife-tree-only\n"
    assert "life-tree-only" in md_result.get("content", "")
    assert read_result["workspaces_resolved_root"] == str(life_root.resolve())
    assert (
        read_result["workspaces_resolved_root"]
        == md_result["workspaces_resolved_root"]
    )


def test_life_workspaces_refusal_state_aware_when_write_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC5 — refusal must not claim READ-ONLY when a life root is configured."""
    from fs_roots import permission_refusal

    shared = tmp_path / "shared"
    life_root = tmp_path / "life"
    shared.mkdir()
    life_root.mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(shared))
    monkeypatch.setenv("LIFE_PROJECT_ROOT", str(life_root))

    refusal = permission_refusal("life", "workspaces", "delete")
    assert refusal is not None
    assert "READ-ONLY" not in refusal["error"]
    assert "not in the life workspaces grant" in refusal["error"]


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
    assert "op_not_permitted" in result["error"]
    assert 'fs(op="read")' in result["error"]


def test_code_fs_cortex_md_list_refused_by_permissions(code_server: dict) -> None:
    fs_fn, _ = _fs_tool_fn(code_server)
    result = fs_fn(
        op="md_list",
        sandbox="cortex",
        path="notes/system/specs/cdp-operator-proxy-v0.md",
    )
    assert "error" in result
    assert "md_list" in result["error"]
    assert "sandbox='cortex'" in result["error"]
    assert "op_not_permitted" in result["error"]
    assert 'fs(op="read")' in result["error"]


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
    assert "send" in payload["live_ops"]
    assert "draft_new" in payload["live_ops"]
    assert payload.get("surface_policy") == "tier-R read + tier-D/O outbound"


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

