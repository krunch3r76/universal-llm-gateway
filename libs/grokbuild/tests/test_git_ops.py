"""Push and PR primitive tests for grokbuild."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from grokbuild.git_ops import pr_create_op, push_op


def _current_branch(cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.asyncio
async def test_push_success_sets_real_upstream(git_repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "remote", "add", "origin", str(remote)],
        check=True,
        capture_output=True,
    )
    branch = _current_branch(git_repo)

    out = await push_op(cwd=str(git_repo), branch=branch)

    assert out["status"] == "completed", out
    assert out["exit_code"] == 0
    assert out["metadata"]["remote"] == "origin"
    assert out["metadata"]["branch"] == branch
    assert out["metadata"]["upstream"] == f"origin/{branch}"
    assert out["metadata"]["upstream_set"] is True


@pytest.mark.asyncio
async def test_push_failure_does_not_synthesize_upstream(git_repo: Path) -> None:
    branch = _current_branch(git_repo)

    out = await push_op(cwd=str(git_repo), branch=branch, remote="missing")

    assert out["status"] == "failed"
    assert out["exit_code"] != 0
    assert out["metadata"]["upstream"] == ""
    assert out["metadata"]["upstream_set"] is False
    upstream = subprocess.run(
        [
            "git",
            "-C",
            str(git_repo),
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{u}",
        ],
        capture_output=True,
        text=True,
    )
    assert upstream.returncode != 0


@pytest.mark.asyncio
async def test_pr_create_rejects_when_gh_missing(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("grokbuild.git_ops.shutil.which", lambda _: None)

    out = await pr_create_op(cwd=str(git_repo), pr_title="Ship it")

    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "gh_not_in_path"


@pytest.mark.asyncio
async def test_pr_create_invokes_gh_with_fields(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("grokbuild.git_ops.shutil.which", lambda name: f"/bin/{name}")
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    class Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"https://github.com/acme/repo/pull/1\n", b""

    async def fake_exec(*cmd: str, **kwargs: Any) -> Proc:
        calls.append((cmd, kwargs))
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    out = await pr_create_op(
        cwd=str(git_repo),
        pr_title="Ship it",
        pr_body="Body",
        pr_base="master",
        pr_head="feature",
        draft=True,
    )

    assert out["status"] == "completed"
    assert out["metadata"]["pr_url"] == "https://github.com/acme/repo/pull/1"
    cmd, kwargs = calls[0]
    assert cmd == (
        "gh",
        "pr",
        "create",
        "--title",
        "Ship it",
        "--body",
        "Body",
        "--base",
        "master",
        "--head",
        "feature",
        "--draft",
    )
    assert kwargs["cwd"] == str(git_repo)
