"""S1a-2 land lease — Amendment A1 (master lease, dirty master, concurrent land)."""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from git_integrate.git_cas import diff_sha256
from git_integrate.land import land_op
from git_integrate.schema import RC_DIRTY_MASTER
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_land_lease import (
    DirtyMasterRefused,
    checked_out_master_dirty,
    dirty_master_envelope,
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
    try:
        async with master_land_guard(
            source_repo=str(source_repo),
            holder_op_id=holder,
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
    except DirtyMasterRefused as exc:
        return dirty_master_envelope(exc=exc)


@pytest.fixture
def event_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    def _record(signal: str, **payload: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("git_integrate.events.record", _record)
    return log


@pytest.mark.asyncio
async def test_s1a_land_refuses_dirty_master(
    source_repo: Path,
    tmp_path: Path,
) -> None:
    """AC8: land refuses merge into a dirty checked-out master."""
    _git("checkout", "master", cwd=source_repo)
    (source_repo / "operator-wip.py").write_text("# wip\n")

    dirty, reason = checked_out_master_dirty(str(source_repo))
    assert dirty is True
    assert "dirty" in reason.lower()

    wt = _arc_worktree(source_repo, tmp_path, "clean-arc", "feature.py")
    out = await _land_with_master_lease(
        source_repo=source_repo,
        arc="clean-arc",
        worktree=wt,
        holder_suffix="dirty-master",
    )
    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_DIRTY_MASTER


@pytest.mark.asyncio
async def test_s1a_concurrent_land_loser_remerge_regate(
    source_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """AC7: concurrent lands serialize; loser re-merges master tip and re-runs gate."""
    wt_a = _arc_worktree(source_repo, tmp_path, "land-a", "a.py")
    wt_b = _arc_worktree(source_repo, tmp_path, "land-b", "b.py")

    gate_runs = 0
    original_run = __import__(
        "git_integrate.ops_common", fromlist=["_run_command"]
    )._run_command

    async def _count_gate(cmd: list[str], **kwargs: Any) -> Any:
        nonlocal gate_runs
        if cmd == _passing_gate():
            gate_runs += 1
        return await original_run(cmd, **kwargs)

    monkeypatch.setattr("git_integrate.ops_common._run_command", _count_gate)
    monkeypatch.setattr("git_integrate.events.record", lambda signal, **payload: event_log.append((signal, payload)))

    results = await asyncio.gather(
        _land_with_master_lease(
            source_repo=source_repo,
            arc="land-a",
            worktree=wt_a,
            holder_suffix="a",
        ),
        _land_with_master_lease(
            source_repo=source_repo,
            arc="land-b",
            worktree=wt_b,
            holder_suffix="b",
        ),
    )

    assert all(r["status"] == "completed" for r in results), results
    assert gate_runs >= 2

    master_tree = subprocess.run(
        ["git", "-C", str(source_repo), "ls-tree", "-r", "--name-only", "master"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "a.py" in master_tree
    assert "b.py" in master_tree
    # Loser re-merged updated master before gating (merge commit on arc B).
    merge_count = subprocess.run(
        ["git", "-C", str(wt_b), "rev-list", "--count", "--merges", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert int(merge_count) >= 1

    lease_key = master_land_lease_key(source_repo)
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM cursor_sdk_land_leases WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
    assert row is None


@pytest.mark.asyncio
async def test_s1a_f_a1_concurrent_land_serializes_not_stale_green(
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
