"""Tests for cursor_home — credential copy, isolation, fail-closed gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_home import (
    CursorHomeConfigError,
    CursorVenvConfigError,
    dispatch_git_identity,
    dispatch_home_path,
    resolve_repo_venv,
    setup_cursor_dispatch_home,
    validate_repo_venv,
)


def _fake_real_home(
    tmp_path: Path,
    *,
    with_identity: bool = True,
    with_credential: bool = True,
) -> Path:
    real = tmp_path / "real-home"
    if with_identity:
        cursor = real / ".cursor"
        cursor.mkdir(parents=True)
        (cursor / "cli-config.json").write_text(
            json.dumps({"authInfo": {"email": "op@example.com"}}),
            encoding="utf-8",
        )
    if with_credential:
        xdg = real / ".config" / "cursor"
        xdg.mkdir(parents=True)
        (xdg / "auth.json").write_text(
            json.dumps({"token": "real-credential"}),
            encoding="utf-8",
        )
    return real


def test_dispatch_home_path_shape(tmp_path: Path) -> None:
    root = tmp_path / "homes"
    assert dispatch_home_path("abc", root=root) == root / "abc-home"


def test_dispatch_git_identity_names_dispatch() -> None:
    name, email = dispatch_git_identity("auto-deadbeef")
    assert name == "cursor-sdk/auto-deadbeef"
    assert email == "auto-deadbeef@dispatch.git-integration-worker"


def test_dispatch_git_identity_names_lane_when_thread_given() -> None:
    name, email = dispatch_git_identity("auto-deadbeef", thread_id="7223")
    assert name == "cursor-sdk/lane-7223"
    assert email == "auto-deadbeef@dispatch.git-integration-worker"


def test_setup_seeds_gitconfig_in_dispatch_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    real = _fake_real_home(tmp_path)
    plugin_skill = (
        real
        / ".cursor"
        / "plugins"
        / "local"
        / "ulg-ecosystem"
        / "skills"
        / "residual-imprint"
    )
    plugin_skill.mkdir(parents=True)
    (plugin_skill / "SKILL.md").write_text("# residual-imprint", encoding="utf-8")
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("auto-smoke", real_home=real, root=root)
    gitconfig = home / ".gitconfig"
    assert gitconfig.exists()
    assert not gitconfig.is_symlink()
    text = gitconfig.read_text(encoding="utf-8")
    assert "cursor-sdk/auto-smoke" in text
    assert "auto-smoke@dispatch.git-integration-worker" in text


def test_user_rules_copy_not_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    real = tmp_path / "real-home"
    rules = real / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "operator.mdc").write_text("rule", encoding="utf-8")
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    copied = home / ".cursor" / "rules" / "operator.mdc"
    assert copied.read_text(encoding="utf-8") == "rule"
    assert not copied.is_symlink()


def test_mcp_json_copy_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    real = tmp_path / "real-home"
    cursor = real / ".cursor"
    cursor.mkdir(parents=True)
    (cursor / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    plugin_skill = (
        real
        / ".cursor"
        / "plugins"
        / "local"
        / "ulg-ecosystem"
        / "skills"
        / "residual-imprint"
    )
    plugin_skill.mkdir(parents=True)
    (plugin_skill / "SKILL.md").write_text("# residual-imprint", encoding="utf-8")
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    assert (home / ".cursor" / "mcp.json").exists()


def test_mcp_yaml_copy_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    real = tmp_path / "real-home"
    gateway = real / ".gateway"
    gateway.mkdir(parents=True)
    (gateway / "mcp.yaml").write_text("auth_token: test-token\n", encoding="utf-8")
    plugin_skill = (
        real
        / ".cursor"
        / "plugins"
        / "local"
        / "ulg-ecosystem"
        / "skills"
        / "residual-imprint"
    )
    plugin_skill.mkdir(parents=True)
    (plugin_skill / "SKILL.md").write_text("# residual-imprint", encoding="utf-8")
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    copied = home / ".gateway" / "mcp.yaml"
    assert copied.read_text(encoding="utf-8") == "auth_token: test-token\n"
    assert not copied.is_symlink()


def test_plugins_copy_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    real = tmp_path / "real-home"
    plugin_skill = (
        real
        / ".cursor"
        / "plugins"
        / "local"
        / "ulg-ecosystem"
        / "skills"
        / "residual-imprint"
    )
    plugin_skill.mkdir(parents=True)
    (plugin_skill / "SKILL.md").write_text("# residual-imprint", encoding="utf-8")
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    copied = (
        home
        / ".cursor"
        / "plugins"
        / "local"
        / "ulg-ecosystem"
        / "skills"
        / "residual-imprint"
        / "SKILL.md"
    )
    assert copied.read_text(encoding="utf-8") == "# residual-imprint"
    assert not copied.is_symlink()


def test_identity_copy_not_symlink(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    identity = home / ".cursor" / "cli-config.json"
    assert identity.exists()
    assert not identity.is_symlink()


def test_credential_copy_present(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    cred = home / ".config" / "cursor" / "auth.json"
    assert cred.exists()
    assert not cred.is_symlink()


def test_credential_isolation(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    dispatch_cred = home / ".config" / "cursor" / "auth.json"
    real_cred = real / ".config" / "cursor" / "auth.json"
    before = real_cred.read_bytes()
    dispatch_cred.write_text(json.dumps({"token": "mutated"}), encoding="utf-8")
    assert real_cred.read_bytes() == before


def test_homes_mutually_disjoint(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    root = tmp_path / "homes"
    h1 = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    h2 = setup_cursor_dispatch_home("d2", real_home=real, root=root)
    assert h1 != h2
    assert h1.resolve() != h2.resolve()
    assert h1.resolve() != real.resolve()


def test_fail_closed_no_cred_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = _fake_real_home(tmp_path, with_credential=False)
    root = tmp_path / "homes"
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(CursorHomeConfigError):
        setup_cursor_dispatch_home("d1", real_home=real, root=root)


def test_api_key_satisfies_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = _fake_real_home(tmp_path, with_credential=False)
    root = tmp_path / "homes"
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    assert not (home / ".config" / "cursor" / "auth.json").exists()


def test_identity_absent_non_fatal(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path, with_identity=False)
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    assert (home / ".config" / "cursor" / "auth.json").exists()
    assert not (home / ".cursor" / "cli-config.json").exists()


def test_idempotent_re_setup(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    root = tmp_path / "homes"
    setup_cursor_dispatch_home("d1", real_home=real, root=root)
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    assert (home / ".cursor" / "cli-config.json").exists()
    assert (home / ".config" / "cursor" / "auth.json").exists()


def test_container_home_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = _fake_real_home(tmp_path)
    root = tmp_path / "homes"
    monkeypatch.delenv("HOME", raising=False)
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    assert home.name == "d1-home"
    assert (home / ".config" / "cursor" / "auth.json").exists()


def _fake_venv(tmp_path: Path, *, with_executables: bool = True) -> Path:
    venv = tmp_path / "venv"
    if with_executables:
        bindir = venv / "bin"
        bindir.mkdir(parents=True)
        for exe in ("python", "pytest", "ruff"):
            (bindir / exe).touch()
    return venv


def test_resolve_repo_venv_default_from_real_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "operator"
    monkeypatch.delenv("CURSOR_SDK_VENV_PATH", raising=False)
    assert resolve_repo_venv(real_home=fake_home) == fake_home / ".venvs" / "universal"


def test_resolve_repo_venv_rejects_non_universal_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "custom-venv"
    monkeypatch.setenv("CURSOR_SDK_VENV_PATH", str(override))
    with pytest.raises(CursorVenvConfigError, match="universal venv"):
        resolve_repo_venv()


def test_validate_repo_venv_ok(tmp_path: Path) -> None:
    venv = _fake_venv(tmp_path)
    assert validate_repo_venv(venv) is None


def test_validate_repo_venv_missing_dir(tmp_path: Path) -> None:
    venv = tmp_path / "missing"
    with pytest.raises(CursorVenvConfigError, match="venv dir"):
        validate_repo_venv(venv)


def test_validate_repo_venv_missing_executable(tmp_path: Path) -> None:
    venv = _fake_venv(tmp_path)
    (venv / "bin" / "pytest").unlink()
    with pytest.raises(CursorVenvConfigError, match="bin/pytest"):
        validate_repo_venv(venv)


def _seed_cursor_agent_shim(home: Path) -> Path:
    versions = home / ".local/share/cursor-agent/versions/0.0-test"
    versions.mkdir(parents=True)
    binary = versions / "cursor-agent"
    binary.write_text("#!/bin/sh\necho cursor-agent\n", encoding="utf-8")
    binary.chmod(0o755)
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "agent").symlink_to(binary)
    return local_bin


def test_is_cursor_agent_shim_accepts_cursor_binary(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_home import is_cursor_agent_shim

    home = tmp_path / "home"
    local_bin = _seed_cursor_agent_shim(home)
    assert is_cursor_agent_shim(local_bin / "agent") is True


def test_is_cursor_agent_shim_rejects_grok_binary(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_home import is_cursor_agent_shim

    home = tmp_path / "home"
    grok_bin = home / ".grok" / "downloads" / "grok-linux-x86_64"
    grok_bin.parent.mkdir(parents=True)
    grok_bin.write_text("#!/bin/sh\necho grok\n", encoding="utf-8")
    grok_bin.chmod(0o755)
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "agent").symlink_to(grok_bin)
    assert is_cursor_agent_shim(local_bin / "agent") is False


def test_build_dispatch_path_prepend_includes_verified_local_bin(
    tmp_path: Path,
) -> None:
    import os

    from services.git_integration_worker.cursor_home import build_dispatch_path_prepend

    home = tmp_path / "home"
    _seed_cursor_agent_shim(home)
    venv = _fake_venv(tmp_path)
    prepend = build_dispatch_path_prepend(venv, real_home=home)
    parts = prepend.split(os.pathsep)
    assert parts[0] == str(venv / "bin")
    assert parts[1] == str(home / ".local" / "bin")


def test_build_dispatch_path_prepend_skips_grok_overwritten_shim(
    tmp_path: Path,
) -> None:
    from services.git_integration_worker.cursor_home import (
        build_dispatch_path_prepend,
        is_cursor_agent_shim,
    )

    home = tmp_path / "home"
    grok_bin = home / ".grok" / "downloads" / "grok-linux-x86_64"
    grok_bin.parent.mkdir(parents=True)
    grok_bin.write_text("#!/bin/sh\necho grok\n", encoding="utf-8")
    grok_bin.chmod(0o755)
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "agent").symlink_to(grok_bin)
    assert is_cursor_agent_shim(local_bin / "agent") is False
    venv = _fake_venv(tmp_path)
    prepend = build_dispatch_path_prepend(venv, real_home=home)
    assert prepend == str(venv / "bin")
