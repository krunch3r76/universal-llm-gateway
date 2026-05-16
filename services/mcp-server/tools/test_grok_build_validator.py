"""§5.11 validator tests (#3, #7–#10, #17, #22)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools._grok_build_test_support import (
    clear_validator_caches,
    install_grok_path,
    install_subprocess_run,
)
from tools._grok_build_validator import validate_dispatch


def test_not_a_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sidecar_root: Path
) -> None:
    cwd = str(tmp_path / "not-git")
    (tmp_path / "not-git").mkdir()
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=cwd, rev_parse_ok=False)

    vr = validate_dispatch("dispatch", cwd, "read_only", None, False, "json")

    assert vr.reason_code == "not_a_git_repo"


def test_session_conflict(admission: str, sidecar_root: Path) -> None:
    vr = validate_dispatch("dispatch", admission, "read_only", "sid", True, "json")
    assert vr.reason_code == "session_conflict"


def test_grok_not_in_path(monkeypatch: pytest.MonkeyPatch, admission: str) -> None:
    clear_validator_caches()
    monkeypatch.setattr("tools._grok_build_validator.shutil.which", lambda _: None)

    vr = validate_dispatch("dispatch", admission, "read_only", None, False, "json")

    assert vr.reason_code == "grok_not_in_path"


def test_missing_grok_auth(
    admission: str, monkeypatch: pytest.MonkeyPatch, sidecar_root: Path
) -> None:
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=admission, grok_models_rc=1)

    vr = validate_dispatch("dispatch", admission, "read_only", None, False, "json")

    assert vr.reason_code == "missing_grok_auth"


def test_sidecar_directory_creation(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sidecar = tmp_path / "fresh-sidecar"
    monkeypatch.setattr("tools._grok_build_validator._SIDECAR_DIR", sidecar)
    cwd = str(git_repo)
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=cwd)

    assert not sidecar.exists()
    vr1 = validate_dispatch("dispatch", cwd, "read_only", None, False, "json")
    assert vr1.ok and sidecar.is_dir()

    vr2 = validate_dispatch("dispatch", cwd, "read_only", None, False, "json")
    assert vr2.ok
