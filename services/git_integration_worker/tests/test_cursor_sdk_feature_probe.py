"""Unit tests for cursor-sdk RunGitInfo feature probe (closeout-correctness)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_sdk_feature_probe import (
    CLOUD_SEND_PATH_LABEL,
    LOCAL_BRIDGE_PATH_LABEL,
    SDK_GIT_PROBE_ABSENT,
    clear_probe_cache,
    git_probe_degraded_reasons,
    probe_run_git_info,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


@pytest.fixture(autouse=True)
def _reset_probe_cache() -> None:
    clear_probe_cache()


def test_probe_fail_closed_without_git_shape() -> None:
    probe = probe_run_git_info(path_label=LOCAL_BRIDGE_PATH_LABEL, result=None)
    assert probe.git_available is False
    assert probe.sample_branch is None
    assert probe.path_label == LOCAL_BRIDGE_PATH_LABEL


def test_probe_git_available_from_result_git() -> None:
    branch = type("Branch", (), {"repo_url": "r", "branch": "main", "pr_url": None})()
    result = type("Result", (), {"git": type("Git", (), {"branches": (branch,)})()})()
    probe = probe_run_git_info(path_label=LOCAL_BRIDGE_PATH_LABEL, result=result)
    assert probe.git_available is True
    assert probe.sample_branch == "main"


def test_probe_caches_by_path_label() -> None:
    branch = type("Branch", (), {"repo_url": "r", "branch": "dev", "pr_url": None})()
    result = type("Result", (), {"git": type("Git", (), {"branches": (branch,)})()})()
    first = probe_run_git_info(path_label=CLOUD_SEND_PATH_LABEL, result=result)
    second = probe_run_git_info(path_label=CLOUD_SEND_PATH_LABEL, result=None)
    assert first is second
    assert second.git_available is True


def test_probe_client_factory_introspection() -> None:
    branch = type("Branch", (), {"repo_url": "r", "branch": "probe", "pr_url": None})()

    def _factory() -> object:
        return type("Result", (), {"git": type("Git", (), {"branches": (branch,)})()})()

    probe = probe_run_git_info(
        path_label=LOCAL_BRIDGE_PATH_LABEL,
        result=None,
        client_factory=_factory,
    )
    assert probe.git_available is True
    assert probe.sample_branch == "probe"


def test_git_probe_degraded_reasons_mismatch_when_available(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "feature/probe"],
        check=True,
        capture_output=True,
    )
    branch = type("Branch", (), {"repo_url": "r", "branch": "main", "pr_url": None})()
    result = type("Result", (), {"git": type("Git", (), {"branches": (branch,)})()})()
    probe = probe_run_git_info(path_label=LOCAL_BRIDGE_PATH_LABEL, result=result)
    reasons = git_probe_degraded_reasons(
        probe=probe,
        sdk_git={"branch": "main", "repo_url": "r", "pr_url": None},
        source_repo=tmp_path,
    )
    assert reasons == ("sdk_fs_mismatch",)


def test_git_probe_degraded_reasons_suppresses_mismatch_when_absent(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    probe = probe_run_git_info(path_label=LOCAL_BRIDGE_PATH_LABEL, result=None)
    reasons = git_probe_degraded_reasons(
        probe=probe,
        sdk_git={"branch": "main", "repo_url": "r", "pr_url": None},
        source_repo=tmp_path,
    )
    assert reasons == (SDK_GIT_PROBE_ABSENT,)
