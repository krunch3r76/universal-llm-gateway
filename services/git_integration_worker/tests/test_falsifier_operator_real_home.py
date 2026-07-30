"""Falsifier — operator_real_home ignores contaminated process HOME.

Regression for CURSOR_VENV_CONFIG (agent-bus:6468): GIW process HOME leaked to a
per-dispatch overlay; resolve_repo_venv then looked for .venvs under that path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_home import (
    CursorVenvConfigError,
    is_dispatch_home_path,
    operator_real_home,
    resolve_repo_venv,
    validate_repo_venv,
)


def test_falsifier_operator_real_home_ignores_dispatch_home_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    leaked = dispatch_root / "auto-deadbeef-home"
    leaked.mkdir(parents=True)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    # Re-bind module root used by is_dispatch_home_path / operator_real_home.
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.setenv("HOME", str(leaked))

    pinned = operator_real_home()
    assert not is_dispatch_home_path(pinned, root=dispatch_root)
    assert pinned == Path(home_mod._passwd_home()).resolve() or pinned == home_mod._passwd_home()

    venv = resolve_repo_venv()
    assert leaked not in venv.parents and venv != leaked / ".venvs" / "universal"
    assert not str(venv).startswith(str(leaked))


def test_falsifier_resolve_repo_venv_under_passwd_survives_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live operator venv must validate when HOME is a fake dispatch home."""
    import services.git_integration_worker.cursor_home as home_mod

    passwd = home_mod._passwd_home()
    fake = passwd / ".local" / "share" / "git-integration-worker" / "cursor-dispatch-homes" / "auto-fake-home"
    monkeypatch.setenv("HOME", str(fake))
    # Ensure is_dispatch_home_path sees the real default root.
    venv = resolve_repo_venv()
    assert venv == passwd / ".venvs" / "universal"
    validate_repo_venv(venv)  # raises CursorVenvConfigError if wrong
