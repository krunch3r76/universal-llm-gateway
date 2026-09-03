"""Dual-key Cursor API routing — Other Models pool via AgentOptions.api_key."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cursor_sdk.types import ModelSelection

from services.git_integration_worker.cursor_sdk_bridge_launch import (
    build_bridge_command,
)
from services.git_integration_worker.cursor_sdk_context import (
    CURSOR_API_KEY_OTHER_MODELS_ENV,
    CursorSdkParityError,
    build_agent_options,
    resolve_cursor_api_key,
    validate_dispatch_context,
)
from services.git_integration_worker.cursor_sdk_resume import start_or_resume_agent


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


def test_resolve_cursor_api_key_primary_env() -> None:
    env = {
        "CURSOR_API_KEY": "primary-key",
        CURSOR_API_KEY_OTHER_MODELS_ENV: "other-key",
    }
    res = resolve_cursor_api_key("cursor/composer-2.5", env=env)
    assert res.provenance == "env:CURSOR_API_KEY"
    assert res.api_key is None


def test_resolve_cursor_api_key_grok_primary_pool() -> None:
    env = {
        "CURSOR_API_KEY": "primary-key",
        CURSOR_API_KEY_OTHER_MODELS_ENV: "secondary-key",
    }
    res = resolve_cursor_api_key("cursor/grok-4.6", env=env)
    assert res.provenance == "env:CURSOR_API_KEY"
    assert res.api_key is None


def test_resolve_cursor_api_key_primary_auth_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    xdg = home / ".config" / "cursor"
    xdg.mkdir(parents=True)
    auth = xdg / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    env: dict[str, str] = {}
    res = resolve_cursor_api_key("composer-2.5", real_home=home, env=env)
    assert res.provenance == f"file:{auth}"
    assert res.api_key is None


def test_resolve_cursor_api_key_other_models_secondary(tmp_path: Path) -> None:
    env = {CURSOR_API_KEY_OTHER_MODELS_ENV: "secondary-key"}
    res = resolve_cursor_api_key("cursor/claude-opus-5", env=env)
    assert res.provenance == f"env:{CURSOR_API_KEY_OTHER_MODELS_ENV}"
    assert res.api_key == "secondary-key"


def test_resolve_cursor_api_key_other_models_missing_secondary() -> None:
    env = {"CURSOR_API_KEY": "primary-key"}
    with pytest.raises(CursorSdkParityError, match=CURSOR_API_KEY_OTHER_MODELS_ENV):
        resolve_cursor_api_key("claude-sonnet-5", env=env)


def test_resolve_cursor_api_key_missing_secondary_names_secrets_env() -> None:
    env: dict[str, str] = {}
    with pytest.raises(CursorSdkParityError, match="secrets.env"):
        resolve_cursor_api_key("gpt-5.6-terra", env=env)


def test_build_bridge_command_never_injects_cursor_api_key(tmp_path: Path) -> None:
    home = tmp_path / "dispatch-home"
    home.mkdir()
    command = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=home,
        repo_venv=None,
        real_home=None,
        dispatch_id="disp-om",
    )
    assert not any(a.startswith("CURSOR_API_KEY=") for a in command)


def test_validate_dispatch_context_other_models_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "tok")
    monkeypatch.delenv(CURSOR_API_KEY_OTHER_MODELS_ENV, raising=False)
    monkeypatch.setenv("CURSOR_API_KEY", "primary")
    repo = _stub_repo(tmp_path)

    with pytest.raises(CursorSdkParityError, match=CURSOR_API_KEY_OTHER_MODELS_ENV):
        validate_dispatch_context(
            repo, resolved_model="cursor/claude-opus-5", real_home=tmp_path / "home"
        )


def test_validate_dispatch_context_other_models_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "tok")
    monkeypatch.setenv(CURSOR_API_KEY_OTHER_MODELS_ENV, "secondary")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    repo = _stub_repo(tmp_path)

    report = validate_dispatch_context(
        repo, resolved_model="gpt-5.6-terra", real_home=tmp_path / "home"
    )
    assert report["cursor_auth_source"] == f"env:{CURSOR_API_KEY_OTHER_MODELS_ENV}"


def test_build_agent_options_wires_api_key_for_other_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "tok")
    repo = _stub_repo(tmp_path)
    dispatch_ws = tmp_path / "dispatch"
    dispatch_ws.mkdir()
    model = ModelSelection(id="claude-opus-5")

    opts = build_agent_options(
        repo,
        dispatch_ws,
        model,
        workspace_root=repo,
        api_key="secondary-key",
    )
    assert opts.api_key == "secondary-key"


def test_build_agent_options_primary_pool_omits_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "tok")
    repo = _stub_repo(tmp_path)
    dispatch_ws = tmp_path / "dispatch"
    dispatch_ws.mkdir()
    model = ModelSelection(id="composer-2.5")

    opts = build_agent_options(
        repo,
        dispatch_ws,
        model,
        workspace_root=repo,
        api_key=None,
    )
    assert opts.api_key is None


def test_start_or_resume_agent_create_carries_api_key() -> None:
    client = MagicMock()
    agent = MagicMock()
    run = MagicMock()
    client.create_agent.return_value = agent
    agent.send.return_value = run
    options = MagicMock(api_key="secondary-key")

    got_agent, got_run = start_or_resume_agent(
        client=client,
        agent_options=options,
        prompt="hello",
        resume_ctx=None,
    )

    client.create_agent.assert_called_once_with(options)
    assert got_agent is agent
    assert got_run is run


def test_start_or_resume_agent_resume_carries_api_key() -> None:
    from services.git_integration_worker.cursor_sdk_resume import ResumeRunContext

    client = MagicMock()
    agent = MagicMock()
    run = MagicMock()
    client.resume_agent.return_value = agent
    agent.send.return_value = run
    options = MagicMock(api_key="secondary-key")
    resume_ctx = ResumeRunContext(
        resume_of="parent-disp",
        state_root="/tmp/store",
        sdk_agent_id="agent-parent",
    )

    start_or_resume_agent(
        client=client,
        agent_options=options,
        prompt="continue",
        resume_ctx=resume_ctx,
    )

    client.resume_agent.assert_called_once_with("agent-parent", options)


def test_build_bridge_command_does_not_mutate_os_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "primary")
    before = dict(os.environ)
    build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=tmp_path,
        repo_venv=None,
        real_home=None,
        dispatch_id=None,
    )
    assert dict(os.environ) == before
