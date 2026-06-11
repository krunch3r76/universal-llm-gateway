"""Tests for cursor_home — credential copy, isolation, fail-closed gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_home import (
    CursorHomeConfigError,
    dispatch_home_path,
    setup_cursor_dispatch_home,
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
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d1", real_home=real, root=root)
    assert (home / ".cursor" / "mcp.json").exists()


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
