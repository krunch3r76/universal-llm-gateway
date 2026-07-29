"""Unit tests for deploy_identity code_version resolution and caching behavior."""

from __future__ import annotations

from unittest.mock import patch

from deploy_identity.code_version import (
    reset_code_version_cache_for_tests,
    resolve_code_version,
)


def test_env_override_wins(monkeypatch):
    """ULG_CODE_VERSION env var overrides git rev-parse for deterministic probes."""
    reset_code_version_cache_for_tests()
    monkeypatch.setenv("ULG_CODE_VERSION", "env-sha-override")
    assert resolve_code_version() == "env-sha-override"


def test_cache_once_per_process(monkeypatch):
    """resolve_code_version caches the first successful resolution for the process."""
    reset_code_version_cache_for_tests()
    monkeypatch.delenv("ULG_CODE_VERSION", raising=False)
    with patch(
        "deploy_identity.code_version.subprocess.run",
        return_value=type("R", (), {"stdout": "githead123\n"})(),
    ), patch(
        "deploy_identity.code_version.get_workspace_root",
        return_value=type("P", (), {"__str__": lambda self: "/repo"})(),
    ):
        first = resolve_code_version()
        second = resolve_code_version()
    assert first == "githead123"
    assert second == first


def test_unknown_when_git_and_env_fail(monkeypatch):
    """Return unknown when neither env override nor git resolution succeeds."""
    reset_code_version_cache_for_tests()
    monkeypatch.delenv("ULG_CODE_VERSION", raising=False)
    with patch(
        "deploy_identity.code_version.get_workspace_root",
        side_effect=RuntimeError("no root"),
    ):
        assert resolve_code_version() == "unknown"


def test_stamp_line_two_wins_over_git(tmp_path, monkeypatch):
    """Deploy stamp line 2 supplies SHA when git is unavailable in-container."""
    reset_code_version_cache_for_tests()
    monkeypatch.delenv("ULG_CODE_VERSION", raising=False)
    stamp = tmp_path / ".source_sync_stamp"
    stamp.write_text(
        "2026-07-29T23:41:00Z\nfeedfacefeedfacefeedfacefeedfacefeedface\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("deploy_identity.code_version._stamp_path", lambda: stamp)
    with patch(
        "deploy_identity.code_version.get_workspace_root",
        side_effect=RuntimeError("no git in container"),
    ):
        assert resolve_code_version() == "feedfacefeedfacefeedfacefeedfacefeedface"
