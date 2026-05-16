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


def test_cwd_missing(monkeypatch: pytest.MonkeyPatch, sidecar_root: Path) -> None:
    install_grok_path(monkeypatch)

    vr = validate_dispatch(
        "dispatch", "/nonexistent/path/does/not/exist", "read_only", None, False, "json"
    )

    assert vr.reason_code == "cwd_missing"


def test_git_unreachable_rev_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sidecar_root: Path
) -> None:
    """rev-parse raising OSError / TimeoutExpired routes to git_unreachable,
    distinct from a clean CalledProcessError exit which routes to not_a_git_repo.
    """
    import subprocess as _sp

    cwd = str(tmp_path / "exists")
    (tmp_path / "exists").mkdir()
    install_grok_path(monkeypatch)

    def fake_run(cmd: list[str], **kwargs: object) -> _sp.CompletedProcess[str]:
        if cmd[0:2] == ["git", "-C"] and cmd[3] == "rev-parse":
            raise OSError("git binary missing")
        raise AssertionError(f"unexpected subprocess.run: {cmd!r}")

    monkeypatch.setattr(_sp, "run", fake_run)
    vr = validate_dispatch("dispatch", cwd, "read_only", None, False, "json")

    assert vr.reason_code == "git_unreachable"
    assert "git invocation failed" in vr.reason


def test_git_unreachable_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sidecar_root: Path
) -> None:
    """status --porcelain raising TimeoutExpired routes to git_unreachable."""
    import subprocess as _sp

    cwd = str(tmp_path / "exists2")
    (tmp_path / "exists2").mkdir()
    install_grok_path(monkeypatch)

    def fake_run(cmd: list[str], **kwargs: object) -> _sp.CompletedProcess[str]:
        if cmd[0:2] == ["git", "-C"] and cmd[3] == "rev-parse":
            return _sp.CompletedProcess(cmd, 0, stdout=".git\n", stderr="")
        if cmd[0:2] == ["git", "-C"] and cmd[3:5] == ["status", "--porcelain"]:
            raise _sp.TimeoutExpired(cmd, 10)
        raise AssertionError(f"unexpected subprocess.run: {cmd!r}")

    monkeypatch.setattr(_sp, "run", fake_run)
    vr = validate_dispatch("dispatch", cwd, "read_only", None, False, "json")

    assert vr.reason_code == "git_unreachable"
    assert "git status failed" in vr.reason


def test_bad_output_format(admission: str, sidecar_root: Path) -> None:
    vr = validate_dispatch("dispatch", admission, "read_only", None, False, "yaml")
    assert vr.reason_code == "bad_output_format"


def test_grok_models_oserror_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """_grok_models_ok returns False when grok binary raises OSError, not just
    on non-zero exit. Covers the (OSError, TimeoutExpired) except branch."""
    import subprocess as _sp

    from tools._grok_build_test_support import clear_validator_caches
    from tools._grok_build_validator import _grok_models_ok

    clear_validator_caches()

    def fake_run(cmd: list[str], **kwargs: object) -> _sp.CompletedProcess[str]:
        raise OSError("grok binary missing")

    monkeypatch.setattr(_sp, "run", fake_run)
    assert _grok_models_ok() is False
    clear_validator_caches()
