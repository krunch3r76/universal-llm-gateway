"""GIW spawn PATH must put the venv bin first even when it is already present."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.model_manager.ui.controller.service_ctl.git_integration_worker_service import (
    _path_with_venv_first,
    _runtime_env,
)


@pytest.mark.offline
def test_path_with_venv_first_wins_over_local_bin() -> None:
    venv = "/home/io/.venvs/universal/bin"
    inherited = f"/home/io/.local/bin:{venv}:/usr/bin:/bin"
    result = _path_with_venv_first(inherited, venv)
    parts = result.split(":")
    assert parts[0] == venv
    assert parts.count(venv) == 1
    assert parts.index(venv) < parts.index("/home/io/.local/bin")


@pytest.mark.offline
def test_path_with_venv_first_prepends_when_absent() -> None:
    venv = "/home/io/.venvs/universal/bin"
    inherited = "/home/io/.local/bin:/usr/bin:/bin"
    result = _path_with_venv_first(inherited, venv)
    assert result.split(":")[0] == venv
    assert "/home/io/.local/bin" in result.split(":")


@pytest.mark.offline
def test_path_with_venv_first_idempotent_when_already_first() -> None:
    venv = "/home/io/.venvs/universal/bin"
    inherited = f"{venv}:/usr/bin:/bin"
    assert _path_with_venv_first(inherited, venv) == inherited


@pytest.mark.offline
def test_runtime_env_always_sets_path_with_venv_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    venv_bin = home / ".venvs" / "universal" / "bin"
    venv_bin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(
        "PATH", f"/home/io/.local/bin:{venv_bin}:/usr/bin:/bin"
    )
    env = _runtime_env()
    assert "PATH" in env
    parts = env["PATH"].split(":")
    assert parts[0] == str(venv_bin)
    assert parts.count(str(venv_bin)) == 1
    assert parts.index(str(venv_bin)) < parts.index("/home/io/.local/bin")
    assert env["VIRTUAL_ENV"] == str(home / ".venvs" / "universal")
    assert "CURSOR_SDK_VENV_PATH" not in env
