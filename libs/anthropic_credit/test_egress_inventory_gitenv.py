"""Git subprocess env scrub and fail-closed read guards for egress_inventory."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from libs.anthropic_credit import egress_inventory
from libs.anthropic_credit.egress_inventory import (
    _git_subprocess_env,
    _git_tracked_scan_files,
    scan,
)
from libs.anthropic_credit.test_egress_inventory_guards import (
    _commit_all,
    _fixture_allowlist,
    _git_init,
    _write_fake_stargate,
)

pytestmark = pytest.mark.offline


def _scan_only_repo(parent: Path, name: str, rel_file: str) -> Path:
    repo = parent / name
    repo.mkdir(parents=True)
    _git_init(repo)
    (repo / rel_file).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel_file).write_text(f"# {name}\n", encoding="utf-8")
    _commit_all(repo)
    return repo


def _git_ls_files_text(repo_root: Path, *, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "libs"],
        capture_output=True,
        check=False,
        env=env if env is not None else os.environ.copy(),
    )
    return proc.stdout.decode("utf-8", errors="replace")


@pytest.fixture
def pair_repos(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "pair"
    repo_a = _scan_only_repo(base, "a", "libs/a_only.py")
    repo_b = _scan_only_repo(base, "b", "libs/b_only.py")
    return repo_a, repo_b


@pytest.mark.parametrize(
    "env_patch",
    [
        lambda b: {"GIT_DIR": str(b / ".git")},
        lambda b: {"GIT_WORK_TREE": str(b)},
        lambda b: {"GIT_INDEX_FILE": str(b / ".git" / "index")},
    ],
    ids=["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"],
)
def test_git_tracked_scan_ignores_inherited_repo_vars(
    pair_repos: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    env_patch,
) -> None:
    repo_a, repo_b = pair_repos
    for key, value in env_patch(repo_b).items():
        monkeypatch.setenv(key, value)

    polluted = _git_ls_files_text(repo_a)
    if "GIT_WORK_TREE" not in env_patch(repo_b):
        assert "b_only.py" in polluted
        assert "a_only.py" not in polluted

    rels = _git_tracked_scan_files(repo_a)
    assert rels == ["libs/a_only.py"]


def test_git_subprocess_env_drops_git_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", "/tmp/not-a-real-git-dir")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "x")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "y")
    env = _git_subprocess_env()
    assert "GIT_DIR" not in env
    assert "GIT_CONFIG_KEY_0" not in env
    assert "GIT_CONFIG_VALUE_0" not in env


def test_unreadable_tracked_file_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(egress_inventory, "_SERVED_ROOT_ALLOWLIST", {})
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _write_fake_stargate(repo)
    (repo / "services/web/dist").mkdir(parents=True)
    rel = "services/x/unreadable.py"
    target = repo / rel
    target.parent.mkdir(parents=True)
    target.write_text("clean\n", encoding="utf-8")
    _commit_all(repo)

    original = Path.read_text

    def _read_text(self: Path, *args, **kwargs):
        if self == target.resolve() or self == target:
            raise PermissionError("simulated deny")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    report = scan(repo)
    assert not report.ok
    assert any(
        "unreadable.py" in e and "PermissionError" in e for e in report.scan_errors
    )


def test_unreadable_served_file_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        egress_inventory, "_SERVED_ROOT_ALLOWLIST", _fixture_allowlist()
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _write_fake_stargate(repo)
    served = repo / "services/web/dist/served_only.js"
    served.parent.mkdir(parents=True, exist_ok=True)
    served.write_text("console.log(1)\n", encoding="utf-8")
    _commit_all(repo)

    original = Path.read_text

    def _read_text(self: Path, *args, **kwargs):
        if self == served.resolve() or self == served:
            raise PermissionError("simulated deny")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    report = scan(repo)
    assert not report.ok
    assert any(
        "served_only.js" in e and "PermissionError" in e for e in report.scan_errors
    )


def test_non_utf8_tracked_path_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(egress_inventory, "_SERVED_ROOT_ALLOWLIST", {})
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    _write_fake_stargate(repo)
    (repo / "services/web/dist").mkdir(parents=True)
    (repo / "libs" / "ok.py").parent.mkdir(parents=True)
    (repo / "libs" / "ok.py").write_text("pass\n", encoding="utf-8")
    _commit_all(repo)

    real_run = subprocess.run

    def _fake_run(args, **kwargs):
        if args and args[0] == "git" and "ls-files" in args and "-z" in args:
            return subprocess.CompletedProcess(
                args, 0, b"libs/\xffbad.py\x00libs/ok.py\x00", b""
            )
        return real_run(args, **kwargs)

    monkeypatch.setattr(egress_inventory.subprocess, "run", _fake_run)
    report = scan(repo)
    assert not report.ok
    assert any("non-utf8 tracked path" in e for e in report.derivation_errors)


def test_scrub_required_for_correct_repo(
    pair_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without env scrub, _git_tracked_scan_files would mirror polluted git -C."""
    repo_a, repo_b = pair_repos
    monkeypatch.setenv("GIT_DIR", str(repo_b / ".git"))

    def _passthrough_env() -> dict[str, str]:
        return dict(os.environ)

    monkeypatch.setattr(egress_inventory, "_git_subprocess_env", _passthrough_env)
    rels = _git_tracked_scan_files(repo_a)
    assert rels == []
