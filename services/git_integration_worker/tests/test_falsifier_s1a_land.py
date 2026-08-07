"""Bound-invariant falsifiers — S1a land (F-A1)."""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from git_integrate.git_cas import diff_sha256
from git_integrate.land import land_op
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_land_lease import (
    master_land_guard,
    master_land_lease_key,
    release_land_lease,
    try_acquire_land_lease,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _passing_gate() -> list[str]:
    return ["true"]


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("base\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    _git("checkout", "-b", "_integration_parked", cwd=repo)
    return repo


def _arc_worktree(source_repo: Path, tmp_path: Path, arc: str, filename: str) -> Path:
    wt = tmp_path / "worktrees" / arc
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", f"arc/{arc}", str(wt), "master", cwd=source_repo)
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)
    (wt / filename).write_text(f"# {filename}\n")
    _git("add", filename, cwd=wt)
    _git("commit", "-m", f"add {filename}", cwd=wt)
    return wt


async def _land_with_master_lease(
    *,
    source_repo: Path,
    arc: str,
    worktree: Path,
    holder_suffix: str,
) -> dict[str, Any]:
    holder = f"land-{holder_suffix}-{uuid.uuid4().hex[:8]}"
    async with master_land_guard(
        source_repo=str(source_repo),
        holder_op_id=holder,
        worktree_path=str(worktree),
    ):
        return await land_op(
            arc=arc,
            phase="s1a",
            worktree_path=str(worktree),
            approval="approved",
            expected_diff_sha256=diff_sha256(str(worktree)),
            source_repo=str(source_repo),
            green_gate_cmd=_passing_gate(),
            remove_worktree=False,
        )


@pytest.mark.asyncio
async def test_falsifier_f_a1_concurrent_land_serializes_not_stale_green(
    source_repo: Path,
    tmp_path: Path,
) -> None:
    """F-A1: two concurrent land attempts serialize — no stale-green double land."""
    wt_a = _arc_worktree(source_repo, tmp_path, "fa1-a", "fa1_a.py")
    wt_b = _arc_worktree(source_repo, tmp_path, "fa1-b", "fa1_b.py")

    holder_a = f"fa1-a-{uuid.uuid4().hex[:8]}"
    holder_b = f"fa1-b-{uuid.uuid4().hex[:8]}"
    lease_key = master_land_lease_key(source_repo)

    async def _first_land() -> bool:
        await asyncio.to_thread(
            try_acquire_land_lease, lease_key=lease_key, holder_op_id=holder_a
        )
        await asyncio.sleep(0.05)
        return await asyncio.to_thread(
            try_acquire_land_lease, lease_key=lease_key, holder_op_id=holder_b
        )

    blocked = await _first_land()
    assert blocked is False
    await asyncio.to_thread(
        release_land_lease, lease_key=lease_key, holder_op_id=holder_a
    )

    out_a, out_b = await asyncio.gather(
        _land_with_master_lease(
            source_repo=source_repo,
            arc="fa1-a",
            worktree=wt_a,
            holder_suffix="seq-a",
        ),
        _land_with_master_lease(
            source_repo=source_repo,
            arc="fa1-b",
            worktree=wt_b,
            holder_suffix="seq-b",
        ),
    )
    assert out_a["status"] == "completed"
    assert out_b["status"] == "completed"
    assert out_a["master_sha"] != out_b["master_sha"]

    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM cursor_sdk_land_leases WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
    assert row is None
