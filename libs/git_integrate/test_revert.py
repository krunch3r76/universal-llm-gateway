"""revert_op runs in-process and treats conflict as indeterminate."""

import subprocess
from typing import Any

import pytest

from git_integrate import git_cas, revert as revert_mod
from git_integrate.revert import revert_op
from git_integrate.schema import CasResult


def _completed(code: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=code, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def _patch_gate_and_cas(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _gate(*_args: Any, **_kwargs: Any):
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    async def _current_sha(*_args: Any, **_kwargs: Any) -> str:
        return "master-before"

    async def _advance(*_args: Any, **_kwargs: Any) -> CasResult:
        return CasResult(non_ff=False, new_sha="abc123")

    monkeypatch.setattr(revert_mod, "_run_command", _gate)
    monkeypatch.setattr(revert_mod.git_cas, "current_sha", _current_sha)
    monkeypatch.setattr(revert_mod.git_cas, "advance_master_cas", _advance)


@pytest.mark.asyncio
async def test_revert_does_not_use_worker_http(_patch_gate_and_cas: None) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "rev-parse" in argv:
            return _completed(0, stdout="abc123\n")
        return _completed(0, stdout="")

    result = await revert_op(
        source_repo="/tmp/repo",
        merge_sha="deadbeef",
        acquire_lease=lambda _key, _holder: True,
        release_lease=lambda _key, _holder: True,
        git_runner=runner,
        green_gate_cmd=["true"],
    )
    assert result["worker_http"] is False
    assert result["status"] == "landed"
    assert result["signal"] == "git.land.revert_landed"
    assert result["revert_sha"] == "abc123"
    assert calls[0][0:4] == ["git", "-C", "/tmp/repo", "revert"]
    assert "-m1" in calls[0]
    assert "http" not in " ".join(calls[0])


@pytest.mark.asyncio
async def test_revert_conflict_is_indeterminate_and_single_call(
    _patch_gate_and_cas: None,
) -> None:
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
        green_gate_cmd=["true"],
    )
    assert result["status"] == "indeterminate"
    assert result["reason"] == "revert_conflict"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_lease_unavailable_does_not_run_git() -> None:
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
