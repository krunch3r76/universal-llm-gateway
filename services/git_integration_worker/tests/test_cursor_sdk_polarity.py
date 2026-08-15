"""Polarity proof gates for changed_paths attribution (6341 L1)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_closeout import (
    capture_wt_baseline_with_hashes,
    changed_paths,
)
from services.git_integration_worker.cursor_sdk_git_head import (
    git_diff_paths_between,
    resolve_git_head,
)

pytestmark = pytest.mark.offline


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test"],
        check=True,
        capture_output=True,
    )


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_clean_exit_untracked_not_reported_deleted(tmp_path: Path) -> None:
    """Repro agent-bus:6347 — parallel commit clears ?? baseline; not a deletion."""
    _init_git_repo(tmp_path)
    (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    assert baseline["codes"]["f.py"] == "??"

    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "f.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "x"],
        check=True,
        capture_output=True,
    )
    assert (tmp_path / "f.py").is_file()

    change_set, deviations = changed_paths(tmp_path, baseline)
    assert change_set.deleted == ()
    assert "f.py" not in change_set.deleted
    assert "capture:polarity_unproved:f.py" in deviations
    assert not any(":deleted:" in deviation for deviation in deviations)


def test_proved_deletion_when_file_removed(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "gone.py").write_text("tracked\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "gone.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "gone.py").write_text("dirty\n", encoding="utf-8")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    (tmp_path / "gone.py").unlink()
    change_set, _deviations = changed_paths(tmp_path, baseline)
    assert change_set.deleted == ("gone.py",)


def test_proved_created_untracked_new_file(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    (tmp_path / "new.py").write_text("fresh\n", encoding="utf-8")
    change_set, _deviations = changed_paths(tmp_path, baseline)
    assert change_set.created == ("new.py",)


def test_proved_modified_hash_change(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    before = "before\n"
    after = "after\n"
    (tmp_path / "edit.py").write_text(before, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "edit.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "edit.py").write_text(after, encoding="utf-8")
    baseline = {
        "codes": {"edit.py": " M"},
        "hashes": {"edit.py": _sha256_hex(before)},
    }
    change_set, _deviations = changed_paths(tmp_path, baseline)
    assert change_set.modified == ("edit.py",)


def test_admit_head_present_after_baseline_capture(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.py").write_text("x\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "tracked.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    assert baseline["admit_head"] == resolve_git_head(tmp_path)


def test_admit_head_none_on_fresh_repo_without_commits(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    assert baseline["admit_head"] is None


def test_clean_at_admit_deletion_proves_via_admit_head(tmp_path: Path) -> None:
    """Clean tracked file absent from codes/hashes; conjunct 3 falls back to cat-file."""
    _init_git_repo(tmp_path)
    (tmp_path / "clean.py").write_text("tracked\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "clean.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    assert "clean.py" not in baseline["codes"]
    assert "clean.py" not in baseline["hashes"]
    assert baseline["admit_head"] is not None
    (tmp_path / "clean.py").unlink()
    change_set, _deviations = changed_paths(tmp_path, baseline)
    assert change_set.deleted == ("clean.py",)


def test_clean_at_admit_deletion_not_proved_without_admit_head(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "clean.py").write_text("tracked\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "clean.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    baseline = {
        "codes": {},
        "hashes": {},
        "outside_repo": [],
        "admit_head": None,
    }
    (tmp_path / "clean.py").unlink()
    change_set, deviations = changed_paths(tmp_path, baseline)
    assert change_set.deleted == ()
    assert "capture:polarity_unproved:clean.py" in deviations
    assert not any(":deleted:" in deviation for deviation in deviations)


def test_git_diff_paths_between_mid_window_commit(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "a.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    admit_head = resolve_git_head(tmp_path)
    (tmp_path / "b.py").write_text("b\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "b.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "add b"],
        check=True,
        capture_output=True,
    )
    closeout_head = resolve_git_head(tmp_path)
    diff_paths = git_diff_paths_between(
        tmp_path,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    assert "b.py" in diff_paths


def test_git_diff_paths_between_empty_when_sha_missing(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    assert git_diff_paths_between(tmp_path, admit_head=None, closeout_head="abc") == frozenset()
    assert git_diff_paths_between(tmp_path, admit_head="abc", closeout_head=None) == frozenset()
