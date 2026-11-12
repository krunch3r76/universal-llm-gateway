"""S0 r3 witnesses — alias strictness, project guard, schema, lane thread."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fs_impl import fs_impl

from tools._hashing import sha256_hex_of_file
from tools.filesystem._expected_read_sha256_boundary import (
    _key_matches_target,
    coerce_expected_read_sha256_at_fs_boundary,
)
from tools.filesystem.test_expected_read_sha256_boundary import (
    _fs_common,
    _fs_kwargs,
    _md_registry,
    _patch_ws_read,
)

pytestmark = pytest.mark.offline


def test_workspaces_uri_wrong_repo_rejected() -> None:
    path = "universal-llm-gateway/notes/doc.md"
    key = "workspaces://other-repo/notes/doc.md"
    assert not _key_matches_target(key, path=path, sandbox="workspaces")


def test_bare_key_no_blind_first_segment_strip() -> None:
    path = "universal-llm-gateway/services/mcp-server/x.py"
    assert not _key_matches_target("mcp-server/x.py", path=path, sandbox="workspaces")
    assert not _key_matches_target(
        "agent-guides/README.md",
        path="universal-llm-gateway/docs/agent-guides/README.md",
        sandbox="workspaces",
    )


def test_key_miss_includes_accepted_for_target() -> None:
    _, err = coerce_expected_read_sha256_at_fs_boundary(
        op="read",
        expected={"missing.md": "a" * 64},
        path="notes/doc.md",
        sandbox="cortex",
    )
    assert err is not None
    assert err.get("code") == "invalid_parameter"
    accepted = err["data"].get("accepted_for_target")
    assert accepted
    assert "notes/doc.md" in accepted


def test_map_value_none_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    rel = "notes/doc.md"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("# T\n\nx\n", encoding="utf-8")
    md_fn = _md_registry(root, monkeypatch)
    out = fs_impl(**_fs_kwargs(md_fn, op="md_read", expected_read_sha256={rel: None}))
    assert out.get("code") == "invalid_parameter"
    assert rel in out["data"]["keys"]


def test_read_project_file_map_rejected_validation() -> None:
    import jsonschema
    from fastmcp import FastMCP

    from tools.project import register_project_tools

    mcp = FastMCP("read_project_validation")
    register_project_tools(mcp)
    tool = asyncio.run(mcp.get_tool("read_project_file"))
    params = tool.parameters
    props = params["properties"] if isinstance(params, dict) else params.properties
    arg_schema = {
        "type": "object",
        "properties": {
            k: dict(v) if not isinstance(v, dict) else v for k, v in props.items()
        },
        "required": ["path"],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(arg_schema).validate(
            {
                "path": "notes/doc.md",
                "expected_read_sha256": {"notes/doc.md": "a" * 64},
            }
        )


def test_read_project_file_map_via_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = tmp_path / "projects"
    rel = "universal-llm-gateway/notes/doc.md"
    (projects / rel).parent.mkdir(parents=True)
    (projects / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(projects / rel)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: projects)
    monkeypatch.setattr("tools.project._PROJECT_ROOT", projects)
    _patch_ws_read(monkeypatch, projects)
    out = fs_impl(
        **_fs_common(
            op="read",
            sandbox="workspaces",
            path=rel,
            section="Title",
            expected_read_sha256={rel: live},
        )
    )
    assert "AttributeError" not in str(out)
    assert "body" in out.get("content", "")


def test_workspaces_thread_bare_key_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = tmp_path / "shared"
    lane = tmp_path / "lane-wt"
    rel = "universal-llm-gateway/notes/doc.md"
    (lane / rel).parent.mkdir(parents=True)
    (lane / rel).write_text("# Title\n\nlane-body\n", encoding="utf-8")
    live = sha256_hex_of_file(lane / rel)
    shared.mkdir()
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: shared)
    monkeypatch.setattr("tools.project._PROJECT_ROOT", lane)

    def _lane_root(_tid: str) -> Path:
        return lane

    monkeypatch.setattr("lane_branch_root.root_for_thread", _lane_root)
    monkeypatch.setattr(
        "lane_branch_root.branch_for_thread", lambda _t: "cursor-sdk/lane-23300"
    )
    _patch_ws_read(monkeypatch, lane)
    out = fs_impl(
        **_fs_common(
            op="read",
            sandbox="workspaces",
            path=rel,
            thread="23300",
            section="Title",
            expected_read_sha256={"notes/doc.md": live},
        )
    )
    assert out.get("code") != "invalid_parameter", out
    assert "lane-body" in out.get("content", "")
