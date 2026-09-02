"""Tests for cursor_sdk_context — IDE-parity agent options."""

from __future__ import annotations

from pathlib import Path

import pytest
from cursor_sdk.types import ModelSelection

from services.git_integration_worker.config import _DIFF_SCOPED_GATE_SCRIPT, load_config
from services.git_integration_worker.cursor_sdk_context import (
    CursorSdkParityError,
    build_agent_options,
    build_local_agent_options,
    build_mcp_servers,
    resolve_fastmcp_remote_cmd,
    resolve_mcp_token,
    validate_dispatch_context,
)


def _stub_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "universal-llm-gateway"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "mcp-fastmcp-remote-bridge.py").write_text("# stub\n", encoding="utf-8")
    return repo


@pytest.fixture(autouse=True)
def _fastmcp_remote_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_context.shutil.which",
        lambda cmd: "/usr/bin/fastmcp-remote" if cmd == "fastmcp-remote" else None,
    )


def test_local_agent_options_cwd_is_dispatch_workspace(tmp_path: Path) -> None:
    """cwd must equal dispatch_workspace, NOT source_repo."""
    dispatch_ws = tmp_path / "dispatch"
    dispatch_ws.mkdir()
    opts = build_local_agent_options(dispatch_ws)
    assert opts.cwd == str(dispatch_ws.resolve())


def test_local_agent_options_load_all_setting_sources(tmp_path: Path) -> None:
    dispatch_ws = tmp_path / "dispatch"
    dispatch_ws.mkdir()
    opts = build_local_agent_options(dispatch_ws)
    assert opts.cwd == str(dispatch_ws.resolve())
    assert list(opts.setting_sources or ()) == ["all"]


def test_local_agent_options_dirs_when_source_repo_differs(tmp_path: Path) -> None:
    """Multi-root dispatches expose git identity via local.dirs, not a cwd list."""
    dispatch_ws = tmp_path / "projects-root"
    dispatch_ws.mkdir()
    workspace_root = tmp_path / "universal-llm-gateway"
    workspace_root.mkdir()
    opts = build_local_agent_options(dispatch_ws, workspace_root=workspace_root)
    assert opts.cwd == str(dispatch_ws.resolve())
    assert list(opts.dirs or ()) == [str(workspace_root.resolve())]


def test_local_agent_options_omits_dirs_when_paths_match(tmp_path: Path) -> None:
    dispatch_ws = tmp_path / "repo"
    dispatch_ws.mkdir()
    opts = build_local_agent_options(dispatch_ws, workspace_root=dispatch_ws)
    assert opts.cwd == str(dispatch_ws.resolve())
    assert opts.dirs is None


def test_local_agent_options_lane_b_omits_dirs_when_write_tree_matches_cwd(
    tmp_path: Path,
) -> None:
    """Lane-B: write_tree == dispatch_workspace ⇒ no extra dirs entry."""
    worktree = tmp_path / "lane-worktree"
    worktree.mkdir()
    opts = build_local_agent_options(worktree, workspace_root=worktree)
    assert opts.cwd == str(worktree.resolve())
    assert opts.dirs is None


def test_local_agent_options_satellite_workspace_root(tmp_path: Path) -> None:
    """Lane-A satellite: cwd=projects-root, workspace_root=satellite ⇒ dirs=(satellite,)."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    satellite = tmp_path / "satellite-repo"
    satellite.mkdir()
    opts = build_local_agent_options(projects_root, workspace_root=satellite)
    assert opts.cwd == str(projects_root.resolve())
    assert list(opts.dirs or ()) == [str(satellite.resolve())]


def test_local_agent_options_hub_lane_a_workspace_root(tmp_path: Path) -> None:
    """Hub Lane-A: cwd=projects-root, workspace_root=hub ⇒ dirs=(hub,)."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    hub = tmp_path / "universal-llm-gateway"
    hub.mkdir()
    opts = build_local_agent_options(projects_root, workspace_root=hub)
    assert opts.cwd == str(projects_root.resolve())
    assert list(opts.dirs or ()) == [str(hub.resolve())]


def test_mcp_bridge_anchors_to_source_repo(tmp_path: Path) -> None:
    """MCP stdio bridge path stays anchored to source_repo even when dispatch_workspace differs."""
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    bridge_dir = source_repo / "scripts"
    bridge_dir.mkdir(parents=True)
    bridge = bridge_dir / "mcp-fastmcp-remote-bridge.py"
    bridge.touch()
    servers = build_mcp_servers(source_repo)
    assert "user-vortex" in servers
    assert str(bridge.resolve()) in servers["user-vortex"].args


def test_green_gate_cmd_independent_of_dispatch_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GIT_INTEGRATION_DISPATCH_WORKSPACE must not appear in or influence green_gate_cmd."""
    fake_dispatch_ws = "/mnt/torus/projects/some-unrelated-path"
    monkeypatch.setenv("GIT_INTEGRATION_DISPATCH_WORKSPACE", fake_dispatch_ws)
    cfg = load_config()
    gate_script = " ".join(cfg.green_gate_cmd)
    assert "refs/heads/master...HEAD" in gate_script
    assert fake_dispatch_ws not in gate_script
    assert _DIFF_SCOPED_GATE_SCRIPT in gate_script


def test_mcp_servers_use_stdio_bridge(tmp_path: Path) -> None:
    repo = _stub_repo(tmp_path)
    bridge = repo / "scripts" / "mcp-fastmcp-remote-bridge.py"

    servers = build_mcp_servers(repo, real_home=tmp_path / "home")
    assert "user-vortex" in servers
    assert "vortex-code" in servers
    assert "vortex-life" not in servers
    cfg = servers["user-vortex"]
    assert cfg.args == [str(bridge.resolve())]
    assert servers["vortex-code"].args == cfg.args
    assert servers["vortex-code"].env == cfg.env


def test_mcp_servers_missing_bridge_raises(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    repo.mkdir()
    with pytest.raises(CursorSdkParityError, match="vortex MCP bridge missing"):
        build_mcp_servers(repo)


def test_resolve_fastmcp_remote_falls_back_to_sys_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:26890 — PATH may omit the venv bin while the package lives there."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    remote = venv_bin / "fastmcp-remote"
    remote.write_text("#!/bin/sh\n", encoding="utf-8")
    remote.chmod(0o755)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_context.shutil.which",
        lambda _cmd: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_context.sys.prefix",
        str(tmp_path / "venv"),
    )
    assert resolve_fastmcp_remote_cmd() == str(remote)


def test_resolve_fastmcp_remote_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_context.shutil.which",
        lambda _cmd: None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_context.sys.prefix",
        "/tmp/does-not-exist-venv",
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_context.sys.executable",
        "/tmp/does-not-exist-venv/bin/python",
    )
    with pytest.raises(CursorSdkParityError, match="fastmcp-remote"):
        resolve_fastmcp_remote_cmd()


def test_mcp_token_env_mirrors_auth_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _stub_repo(tmp_path)
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok-from-auth")
    monkeypatch.setenv("PROBE_PARENT_ENV", "parent-survives")

    env = build_mcp_servers(repo)["user-vortex"].env or {}
    assert env.get("MCP_TOKEN") == "tok-from-auth"
    assert env.get("PROBE_PARENT_ENV") == "parent-survives"


def test_mcp_token_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    gateway = home / ".gateway"
    gateway.mkdir(parents=True)
    (gateway / "mcp.yaml").write_text("auth_token: yaml-token\n", encoding="utf-8")
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)

    token, source = resolve_mcp_token(real_home=home)
    assert token == "yaml-token"
    assert source == "yaml:auth_token"


def test_validate_dispatch_context_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    home = tmp_path / "home"
    cursor_rules = home / ".cursor" / "rules"
    cursor_rules.mkdir(parents=True)
    xdg = home / ".config" / "cursor"
    xdg.mkdir(parents=True)
    (xdg / "auth.json").write_text("{}", encoding="utf-8")
    gateway = home / ".gateway"
    gateway.mkdir(parents=True)
    (gateway / "mcp.yaml").write_text("auth_token: yaml-token\n", encoding="utf-8")

    repo = _stub_repo(tmp_path)
    report = validate_dispatch_context(repo, real_home=home)
    assert report["mcp_token_source"] == "yaml:auth_token"
    assert report["cursor_auth_source"].startswith("file:")
    assert report["user_rules_dir_present"] is True
    assert report["observed_home_kind"] == "operator"


def test_validate_dispatch_context_missing_mcp_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    home = tmp_path / "home"
    xdg = home / ".config" / "cursor"
    xdg.mkdir(parents=True)
    (xdg / "auth.json").write_text("{}", encoding="utf-8")
    repo = _stub_repo(tmp_path)

    with pytest.raises(CursorSdkParityError, match="observed_home_kind=operator"):
        validate_dispatch_context(repo, real_home=home)


def test_validate_dispatch_context_missing_cursor_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    home = tmp_path / "home"
    gateway = home / ".gateway"
    gateway.mkdir(parents=True)
    (gateway / "mcp.yaml").write_text("auth_token: yaml-token\n", encoding="utf-8")
    repo = _stub_repo(tmp_path)

    with pytest.raises(CursorSdkParityError, match="Cursor credential"):
        validate_dispatch_context(repo, real_home=home)


def test_build_agent_options_wires_model_and_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "tok")
    repo = _stub_repo(tmp_path)
    dispatch_ws = tmp_path / "dispatch"
    dispatch_ws.mkdir()
    model = ModelSelection(id="composer-2.5")

    opts = build_agent_options(repo, dispatch_ws, model)
    assert opts.model == model
    assert opts.mode == "agent"
    assert opts.local is not None
    assert opts.mcp_servers is not None
    assert "user-vortex" in opts.mcp_servers


def test_mcp_servers_set_contract_env_for_implement(tmp_path: Path) -> None:
    repo = _stub_repo(tmp_path)
    env = build_mcp_servers(repo, handoff_contract="implement")["user-vortex"].env or {}
    assert env.get("ULG_MCP_CONTRACT") == "implement"


def test_mcp_servers_set_contract_env_for_pure_mechanical(tmp_path: Path) -> None:
    repo = _stub_repo(tmp_path)
    env = (
        build_mcp_servers(repo, handoff_contract="pure-mechanical")["user-vortex"].env
        or {}
    )
    assert env.get("ULG_MCP_CONTRACT") == "pure-mechanical"


def test_mcp_servers_omit_contract_env_for_light_bounded(tmp_path: Path) -> None:
    repo = _stub_repo(tmp_path)
    env = build_mcp_servers(repo, handoff_contract="light-bounded")["user-vortex"].env or {}
    assert "ULG_MCP_CONTRACT" not in env


def test_mcp_servers_omit_contract_env_when_unset(tmp_path: Path) -> None:
    repo = _stub_repo(tmp_path)
    env = build_mcp_servers(repo)["user-vortex"].env or {}
    assert "ULG_MCP_CONTRACT" not in env
