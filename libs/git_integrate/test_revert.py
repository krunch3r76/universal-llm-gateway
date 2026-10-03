"""revert_op runs in-process and treats conflict as indeterminate."""

import subprocess

import pytest

from git_integrate.revert import revert_op


def _completed(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=code, stdout=stdout, stderr=stderr
    )


@pytest.mark.asyncio
async def test_revert_does_not_use_worker_http():
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _completed(0, stdout="abc123\n")

    result = await revert_op(
        source_repo="/tmp/repo",
        merge_sha="deadbeef",
        acquire_lease=lambda _key, _holder: True,
        release_lease=lambda _key, _holder: True,
        git_runner=runner,
    )
    assert result["worker_http"] is False
    assert result["status"] == "landed"
    assert result["signal"] == "git.land.revert_landed"
    assert calls[0][0:4] == ["git", "-C", "/tmp/repo", "revert"]
    assert "-m1" in calls[0]
    assert "http" not in " ".join(calls[0])


@pytest.mark.asyncio
async def test_revert_conflict_is_indeterminate_and_single_call():
    calls = {"n": 0}

    def runner(_argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls["n"] += 1
        return _completed(1, stderr="CONFLICT")

    result = await revert_op(
        source_repo="/tmp/repo",
        merge_sha="deadbeef",
        acquire_lease=lambda _key, _holder: True,
        release_lease=lambda _key, _holder: True,
        git_runner=runner,
    )
    assert result["status"] == "indeterminate"
    assert result["reason"] == "revert_conflict"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_lease_unavailable_does_not_run_git():
    def runner(_argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError("git must not run")

    result = await revert_op(
        source_repo="/tmp/repo",
        merge_sha="deadbeef",
        acquire_lease=lambda _key, _holder: False,
        release_lease=lambda _key, _holder: True,
        git_runner=runner,
    )
    assert result["status"] == "indeterminate"
    assert result["worker_http"] is False
