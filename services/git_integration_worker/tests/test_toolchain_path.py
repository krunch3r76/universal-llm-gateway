"""GIW toolchain PATH correction — venv first even when spawn PATH is shadowed."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.toolchain_path import (
    apply_toolchain_path,
    path_with_venv_first,
)


@pytest.mark.offline
def test_path_with_venv_first_wins_over_local_bin() -> None:
    venv = "/home/io/.venvs/universal/bin"
    inherited = f"/home/io/.local/bin:{venv}:/usr/bin:/bin"
    result = path_with_venv_first(inherited, venv)
    parts = result.split(":")
    assert parts[0] == venv
    assert parts.count(venv) == 1
    assert parts.index(venv) < parts.index("/home/io/.local/bin")


@pytest.mark.offline
def test_apply_toolchain_path_rewrites_shadowed_spawn_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    venv_bin = home / ".venvs" / "universal" / "bin"
    venv_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    spawn = f"/home/io/.local/bin:{venv_bin}:/usr/bin:/bin"
    env = {"PATH": spawn}
    report = apply_toolchain_path(env)
    assert report.corrected is True
    assert report.spawn_first == "/home/io/.local/bin"
    assert report.effective_first == str(venv_bin)
    assert env["PATH"].split(":")[0] == str(venv_bin)
    assert env["PATH"].split(":").count(str(venv_bin)) == 1


@pytest.mark.offline
def test_apply_toolchain_path_idempotent_when_already_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    venv_bin = home / ".venvs" / "universal" / "bin"
    venv_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    spawn = f"{venv_bin}:/usr/bin:/bin"
    env = {"PATH": spawn}
    report = apply_toolchain_path(env)
    assert report.corrected is False
    assert env["PATH"] == spawn
