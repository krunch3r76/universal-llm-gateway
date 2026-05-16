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


def test_edit_working_tree_dirty_rejection(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, sidecar_root: Path
) -> None:
    """Edit-mode dispatches still reject on a dirty working tree —
    edit needs a clean baseline to produce a meaningful diff."""
    cwd = str(git_repo)
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=cwd, status_pre=" M tracked.txt\n")

    vr = validate_dispatch("dispatch", cwd, "edit", None, False, "json")

    assert vr.ok is False
    assert vr.reason_code == "working_tree_dirty"


def test_read_only_dirty_tree_admitted(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, sidecar_root: Path
) -> None:
    """Read-only dispatches admit any tree state; dirty_admission=True
    flags the verdict as audit-indeterminate for the handler."""
    cwd = str(git_repo)
    install_grok_path(monkeypatch)
    install_subprocess_run(monkeypatch, cwd=cwd, status_pre=" M tracked.txt\n")

    vr = validate_dispatch("dispatch", cwd, "read_only", None, False, "json")

    assert vr.ok is True
    assert vr.dirty_admission is True
    assert vr.git_status_pre == " M tracked.txt\n"


def test_read_only_clean_tree_no_dirty_admission(
    admission: str, sidecar_root: Path
) -> None:
    """Clean-tree read_only admission leaves dirty_admission=False."""
    vr = validate_dispatch("dispatch", admission, "read_only", None, False, "json")

    assert vr.ok is True
    assert vr.dirty_admission is False


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


def test_grok_models_caches_only_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """_grok_models_ok caches only True returns; a transient False return is
    NOT cached, so a subsequent successful call re-runs the subprocess and
    returns True without requiring an MCP restart.

    Regression for the cold-start lru_cache poisoning pattern: with
    ``lru_cache(maxsize=1)`` the first call's False would have been cached
    indefinitely, blocking every dispatch with ``missing_grok_auth`` until
    MCP restarted. See docs/agent-guides/grok-build-dispatch.md §3.4 / §7.2.
    """
    import subprocess as _sp

    from tools._grok_build_test_support import clear_validator_caches
    from tools._grok_build_validator import _grok_models_ok

    clear_validator_caches()

    call_count = {"n": 0}

    def fake_run(cmd: list[str], **kwargs: object) -> _sp.CompletedProcess[str]:
        call_count["n"] += 1
        # First call simulates the cold-start transient (non-zero exit).
        # Subsequent calls succeed.
        rc = 1 if call_count["n"] == 1 else 0
        return _sp.CompletedProcess(cmd, rc, stdout="" if rc else "ok\n", stderr="")

    monkeypatch.setattr(_sp, "run", fake_run)

    # First call: transient False. Must NOT be cached.
    assert _grok_models_ok() is False
    assert call_count["n"] == 1
    # Second call: re-runs subprocess and gets a clean True.
    assert _grok_models_ok() is True
    assert call_count["n"] == 2
    # Third call: uses the cached True. Subprocess MUST NOT be re-invoked.
    assert _grok_models_ok() is True
    assert call_count["n"] == 2

    clear_validator_caches()
