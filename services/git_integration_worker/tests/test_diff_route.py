"""``/diff`` contract via ``_diff_sync`` (C6, thread 1147)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from git_integrate.schema import RC_WORKTREE_MISSING

from services.git_integration_worker.routes.integrate import _diff_sync


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _arc_worktree(tmp_path: Path, source_repo: Path) -> Path:
    wt = tmp_path / "worktrees" / "route-arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/route-arc", str(wt), "master", cwd=source_repo)
    _git("config", "user.email", "t@e.com", cwd=wt)
    _git("config", "user.name", "T", cwd=wt)
    (wt / "route.py").write_text("route = 1\n")
    _git("add", "route.py", cwd=wt)
    _git("commit", "-m", "route change", cwd=wt)
    return wt


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@e.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    (repo / "base.py").write_text("base\n")
    _git("add", "base.py", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    _git("checkout", "-b", "_integration_parked", cwd=repo)
    return repo


def test_diff_sync_default_includes_body_and_diffstat(
    tmp_path: Path, source_repo: Path
) -> None:
    wt = _arc_worktree(tmp_path, source_repo)
    resp = _diff_sync(str(wt), "", include_full_diff=True)
    assert resp.status == "ok"
    assert resp.full_diff_included is True
    assert resp.diff
    assert resp.diffstat is not None
    assert resp.diffstat.files_changed >= 1
    assert resp.diff_sha256
    assert resp.branch == "arc/route-arc"


def test_diff_sync_compact_omits_body_preserves_fingerprint(
    tmp_path: Path, source_repo: Path
) -> None:
    wt = _arc_worktree(tmp_path, source_repo)
    full = _diff_sync(str(wt), "", include_full_diff=True)
    compact = _diff_sync(str(wt), "", include_full_diff=False)
    assert compact.diff == ""
    assert compact.full_diff_included is False
    assert compact.diff_sha256 == full.diff_sha256
    assert compact.diffstat == full.diffstat


def test_diff_sync_path_filter_scopes_body_only(
    tmp_path: Path, source_repo: Path
) -> None:
    wt = _arc_worktree(tmp_path, source_repo)
    (wt / "other.txt").write_text("extra\n")
    _git("add", "other.txt", cwd=wt)
    _git("commit", "-m", "second file", cwd=wt)
    filtered = _diff_sync(str(wt), "route.py", include_full_diff=True)
    unfiltered = _diff_sync(str(wt), "", include_full_diff=True)
    assert filtered.diff_sha256 == unfiltered.diff_sha256
    assert filtered.diffstat == unfiltered.diffstat
    assert "route.py" in filtered.diff
    assert "other.txt" not in filtered.diff


def test_diff_sync_missing_worktree_rejected() -> None:
    resp = _diff_sync("/no/such/worktree", "", include_full_diff=True)
    assert resp.status == "rejected"
    assert resp.reason_code == RC_WORKTREE_MISSING


@pytest.mark.asyncio
async def test_diff_endpoint_default_includes_full_body(
    tmp_path: Path, source_repo: Path
) -> None:
    """Guard D: Query default must include full diff (S2 additive-first)."""
    from httpx import ASGITransport, AsyncClient

    from services.git_integration_worker.app import create_app

    wt = _arc_worktree(tmp_path, source_repo)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/git/diff",
            params={"worktree_path": str(wt)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["full_diff_included"] is True
    assert body["diff"]
    assert body["diff_sha256"]
    assert body["diffstat"] is not None
