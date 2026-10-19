"""Headless cursor-sdk HOME prunes irrelevant user rules and non-code plugins."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_home import setup_cursor_dispatch_home
from services.git_integration_worker.cursor_seat_overlay import ECOSYSTEM_PLUGIN_RELPATH


def _fake_real_home(tmp_path: Path) -> Path:
    real = tmp_path / "operator"
    cursor = real / ".cursor"
    rules = cursor / "rules"
    rules.mkdir(parents=True)
    for name in (
        "php-laravel.mdc",
        "pwa-shell.mdc",
        "javascript-node.mdc",
        "python312.mdc",
        "quality-gates.mdc",
        "modularization.mdc",
        "system.mdc",
        "event-only-seat-wake_ulg.mdc",
        "core-code_ws.mdc",
    ):
        (rules / name).write_text(f"rule:{name}\n", encoding="utf-8")
    plugin_root = cursor / "plugins" / "local" / "ulg-ecosystem"
    (plugin_root / "rules").mkdir(parents=True)
    (plugin_root / "skills" / "reasoning-posture").mkdir(parents=True)
    (plugin_root / "skills" / "reasoning-posture" / "SKILL.md").write_text(
        "skill\n", encoding="utf-8"
    )
    (plugin_root / "rules" / "presence-discipline_ulg.mdc").write_text(
        "presence\n", encoding="utf-8"
    )
    (cursor / "plugins" / "plugin-gmail-gmail").mkdir(parents=True)
    (cursor / "plugins" / "plugin-gmail-gmail" / "CACHE").write_text("x", encoding="utf-8")
    (cursor / "plugins" / "plugin-outlook-outlook").mkdir(parents=True)
    (cursor / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    (real / ".gateway").mkdir(parents=True)
    (real / ".gateway" / "mcp.yaml").write_text("auth_token: t\n", encoding="utf-8")
    (real / ".config" / "cursor").mkdir(parents=True)
    (real / ".config" / "cursor" / "auth.json").write_text("{}", encoding="utf-8")
    return real


def _fake_overlay(tmp_path: Path) -> Path:
    root = tmp_path / "overlay"
    (root / "rules").mkdir(parents=True)
    (root / "skills" / "interagent-posture").mkdir(parents=True)
    (root / "rules" / "interagent-posture_ulg.mdc").write_text("x", encoding="utf-8")
    (root / "skills" / "interagent-posture" / "SKILL.md").write_text("x", encoding="utf-8")
    (root / "rules" / "restart-drain-discipline_ulg.mdc").write_text("x", encoding="utf-8")
    (root / "rules" / "skill-surface_ulg.mdc").write_text("x", encoding="utf-8")
    return root


@pytest.mark.offline
def test_setup_prunes_user_rules_and_plugin_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.setenv("CURSOR_SDK_SEAT_OVERLAY_ROOT", str(_fake_overlay(tmp_path)))
    real = _fake_real_home(tmp_path)
    (real / ".cursor" / "plugins" / "local" / "ulg-ecosystem" / "rules").mkdir(
        parents=True, exist_ok=True
    )
    (real / ".cursor" / "plugins" / "local" / "ulg-ecosystem" / "rules" / "operator-posture_ulg.mdc").write_text(
        "human\n", encoding="utf-8"
    )
    (real / ".cursor" / "plugins" / "local" / "ulg-ecosystem" / "skills" / "operator-posture").mkdir(
        parents=True
    )
    (real / ".cursor" / "plugins" / "local" / "ulg-ecosystem" / "rules" / "event-only-seat-wake_ulg.mdc").write_text(
        "liaison\n", encoding="utf-8"
    )
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d-prune", real_home=real, root=root, bare=False)
    rules_dir = home / ".cursor" / "rules"
    assert (rules_dir / "core-code_ws.mdc").is_file()
    assert not (rules_dir / "python312.mdc").exists()
    assert not (rules_dir / "php-laravel.mdc").exists()
    assert not (home / ".cursor" / "plugins" / "plugin-gmail-gmail").exists()
    plugin_root = home / ".cursor" / ECOSYSTEM_PLUGIN_RELPATH
    assert not (plugin_root / "rules" / "event-only-seat-wake_ulg.mdc").exists()


@pytest.mark.offline
def test_bare_home_still_omits_rules_and_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    real = _fake_real_home(tmp_path)
    root = tmp_path / "homes"
    home = setup_cursor_dispatch_home("d-bare", real_home=real, root=root, bare=True)
    assert not (home / ".cursor" / "rules").exists()
    assert not (home / ".cursor" / "plugins").exists()
