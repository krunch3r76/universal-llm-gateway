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
    resolve_mcp_token,
    validate_dispatch_context,
)


def _stub_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "universal-llm-gateway"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "mcp-stdio-proxy.py").write_text("# stub\n", encoding="utf-8")
    return repo


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


def test_mcp_proxy_anchors_to_source_repo(tmp_path: Path) -> None:
    """MCP stdio proxy path stays anchored to source_repo even when dispatch_workspace differs."""
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    proxy_dir = source_repo / "scripts"
    proxy_dir.mkdir(parents=True)
    proxy = proxy_dir / "mcp-stdio-proxy.py"
    proxy.touch()
    servers = build_mcp_servers(source_repo)
    assert "user-vortex" in servers
    assert str(proxy.resolve()) in servers["user-vortex"].args


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


def test_mcp_servers_use_stdio_proxy(tmp_path: Path) -> None:
    repo = _stub_repo(tmp_path)
    proxy = repo / "scripts" / "mcp-stdio-proxy.py"

    servers = build_mcp_servers(repo, real_home=tmp_path / "home")
    assert "user-vortex" in servers
    cfg = servers["user-vortex"]
    assert cfg.args == [str(proxy.resolve())]


def test_mcp_servers_missing_proxy_raises(tmp_path: Path) -> None:
    repo = tmp_path / "universal-llm-gateway"
    repo.mkdir()
    with pytest.raises(FileNotFoundError, match="mcp-stdio-proxy"):
        build_mcp_servers(repo)


def test_mcp_token_env_mirrors_auth_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _stub_repo(tmp_path)
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok-from-auth")

    env = build_mcp_servers(repo)["user-vortex"].env or {}
    assert env.get("MCP_TOKEN") == "tok-from-auth"


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

    with pytest.raises(CursorSdkParityError, match="MCP token"):
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
