"""Lane-B stale-base refresh on reused admit (lane-b-reused-lane-stale-base)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from universal_event_bus import Event

from services.git_integration_worker import cursor_sdk_lane_refresh as lane_refresh_mod
from services.git_integration_worker.app import create_app
from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_branch_archive import lookup_archive_tag
from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
    get_disposition,
)
from services.git_integration_worker.cursor_sdk_lane_refresh import StaleLaneError
from services.git_integration_worker.cursor_sdk_lane_regime import set_lane_b_regime
from services.git_integration_worker.cursor_sdk_worktree import (
    lane_branch_name,
    lane_worktree_dir,
    mint_dispatch_worktree,
    resolve_admit_binding,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    ensure_worktree_schema,
    lookup_lane_worktree,
    unregister_lane_worktree,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)
    yield
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


@pytest.fixture
def worktree_root(tmp_path: Path) -> Path:
    root = tmp_path / "worktrees"
    root.mkdir()
    return root


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t-refresh",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-1",
        "execution_id": "exec-disp-1",
        "message": "hello",
        "lane": "B",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _binding(
    source_repo: Path,
    worktree_root: Path,
    req: CursorDispatchRequest,
):
    return resolve_admit_binding(
        req=req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )


def _master_tip(repo: Path) -> str:
    return _git("rev-parse", "refs/heads/master", cwd=repo)


def _lane_tip(wt: Path) -> str:
    return _git("rev-parse", "HEAD", cwd=wt)


def _wire_event_capture(monkeypatch: pytest.MonkeyPatch, store: list[Event]) -> None:
    from services.git_integration_worker import cursor_sdk_events as ev_mod

    def _cap(event: Event) -> None:
        if event.signal == "sdk.lane_b.base_refreshed":
            store.append(event)

    monkeypatch.setattr(ev_mod, "_emit", _cap)


def _capture_lane_b_events(monkeypatch: pytest.MonkeyPatch) -> list[Event]:
    captured: list[Event] = []
    _wire_event_capture(monkeypatch, captured)
    return captured


def _registry_snapshot(source_repo: Path, thread_id: str) -> dict[str, Any]:
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT minted_at, salvage_refusal_count, quarantined_at, branch_point "
            "FROM cursor_sdk_lane_worktrees WHERE source_repo=? AND thread_id=?",
            (str(source_repo.resolve()), thread_id),
        ).fetchone()
    assert row is not None
    return dict(row)


def _commit_on_lane(wt: Path, name: str) -> str:
    (wt / name).write_text(f"{name}\n", encoding="utf-8")
    _git("add", name, cwd=wt)
    _git("commit", "-m", name, cwd=wt)
    return _lane_tip(wt)


def _advance_master(repo: Path, *, n: int, prefix: str = "master") -> None:
    _git("checkout", "master", cwd=repo)
    for i in range(n):
        (repo / f"{prefix}-{i}.md").write_text(f"{i}\n", encoding="utf-8")
        _git("add", f"{prefix}-{i}.md", cwd=repo)
        _git("commit", "-m", f"{prefix}-{i}", cwd=repo)


def _mint_lane(
    source_repo: Path,
    worktree_root: Path,
    thread_id: str = "t-refresh",
    dispatch_id: str = "disp-mint",
) -> Path:
    return mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )


def _landed_behind_patch_equivalent(
    source_repo: Path,
    worktree_root: Path,
    thread_id: str = "t-refresh",
    *,
    file_prefix: str | None = None,
) -> tuple[Path, str]:
    prefix = file_prefix or thread_id.replace("/", "-")
    wt = _mint_lane(source_repo, worktree_root, thread_id=thread_id)
    tips: list[str] = []
    for i in range(3):
        tips.append(_commit_on_lane(wt, f"{prefix}-{i}.md"))
    old_tip = _lane_tip(wt)
    _git("checkout", "master", cwd=source_repo)
    for i in range(3):
        name = f"{prefix}-{i}.md"
        (source_repo / name).write_text(f"{name}\n", encoding="utf-8")
        _git("add", name, cwd=source_repo)
        _git("commit", "-m", f"land-{name}", cwd=source_repo)
    _advance_master(source_repo, n=4, prefix=f"{prefix}-master")
    _ = tips
    return wt, old_tip


def test_reuse_refreshes_patch_equivalent_lane(
    source_repo: Path, worktree_root: Path
) -> None:
    thread_id = "t-patch"
    wt, old_tip = _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    binding = _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="disp-2", execution_id="exec-2"),
    )
    assert binding.binding_kind == "reused"
    assert _lane_tip(binding.workspace) == _master_tip(source_repo)
    branch = lane_branch_name(thread_id)
    archived = lookup_archive_tag(repo=source_repo, branch_name=branch)
    assert archived is not None
    assert archived[1] == old_tip


def test_reuse_fast_forwards_ancestor_lane(
    source_repo: Path, worktree_root: Path
) -> None:
    thread_id = "t-anc"
    _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="m1")
    _advance_master(source_repo, n=3)
    binding = _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="m2", execution_id="exec-m2"),
    )
    assert _lane_tip(binding.workspace) == _master_tip(source_repo)
    branch = lane_branch_name(thread_id)
    assert lookup_archive_tag(repo=source_repo, branch_name=branch) is None


def test_reuse_fast_forwards_ff_merged_lane_commit(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-ff-merged"
    wt = _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="ffm1")
    _commit_on_lane(wt, "ff-merged.md")
    branch = lane_branch_name(thread_id)
    _git("checkout", "master", cwd=source_repo)
    _git("merge", "--ff-only", branch, cwd=source_repo)
    _advance_master(source_repo, n=1, prefix="ff-after")
    old_tip = _lane_tip(wt)
    master = _master_tip(source_repo)
    assert old_tip != master
    dispatch_id = "ffm2"
    events = _capture_lane_b_events(monkeypatch)
    binding = _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id=dispatch_id, execution_id="exec-ffm2"),
    )
    assert binding.binding_kind == "reused"
    assert _lane_tip(binding.workspace) == master
    assert lookup_archive_tag(repo=source_repo, branch_name=branch) is None
    row = lookup_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    assert row is not None
    assert row.branch_point == master
    assert len(events) == 1
    ev = events[0]
    assert ev.signal == "sdk.lane_b.base_refreshed"
    assert ev.scope == "node"
    assert ev.payload["outcome"] == "fast_forwarded"
    assert ev.payload["old_tip"] == old_tip
    assert ev.payload["new_tip"] == master
    assert ev.payload["dispatch_id"] == dispatch_id
    assert ev.payload["thread_id"] == thread_id
    assert ev.payload["behind_by"] >= 1


def test_caller_supplied_worktree_path_skips_refresh(
    source_repo: Path, worktree_root: Path
) -> None:
    thread_id = "t-caller-path"
    wt, stale_tip = _landed_behind_patch_equivalent(
        source_repo, worktree_root, thread_id, file_prefix="caller"
    )
    unregister_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    expected = lane_worktree_dir(worktree_root, thread_id, source_repo=source_repo)
    caller_wt = worktree_root / "caller-supplied-external"
    if expected.is_dir():
        expected.rename(caller_wt)
    else:
        caller_wt = wt
    branch = lane_branch_name(thread_id)
    binding = resolve_admit_binding(
        req=_req(
            thread_id=thread_id,
            dispatch_id="caller-adopt",
            execution_id="exec-caller",
            worktree_path=str(caller_wt),
        ),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert binding.binding_kind == "adopted"
    assert _lane_tip(binding.workspace) == stale_tip
    assert _lane_tip(binding.workspace) != _master_tip(source_repo)
    assert lookup_archive_tag(repo=source_repo, branch_name=branch) is None


def test_rev_list_failure_refuses(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-revlist"
    wt, old_tip = _landed_behind_patch_equivalent(
        source_repo, worktree_root, thread_id, file_prefix="revlist"
    )
    dispatch_id = "rev2"

    def _wrapped(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("rev-list", "--count"):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="fail")
        return subprocess.run(
            ["git", "-C", str(cwd.resolve()), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    monkeypatch.setattr(lane_refresh_mod, "_git_runner", _wrapped, raising=False)
    events = _capture_lane_b_events(monkeypatch)
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(thread_id=thread_id, dispatch_id=dispatch_id, execution_id="exec-rev2"),
        )
    assert _lane_tip(wt) == old_tip
    assert len(events) == 1
    assert events[0].signal == "sdk.lane_b.base_refreshed"
    assert events[0].payload["outcome"] == "refused"
    assert events[0].payload["new_tip"] == old_tip


def test_refresh_archive_failure_refuses(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-arch"
    wt, old_tip = _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    before = _lane_tip(wt)
    dispatch_id = "arch"
    events = _capture_lane_b_events(monkeypatch)

    def _no_archive(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_lane_refresh.archive_branch",
        _no_archive,
    )
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(
                thread_id=thread_id,
                dispatch_id=dispatch_id,
                execution_id="exec-arch",
            ),
        )
    assert _lane_tip(wt) == before
    assert len(events) == 1
    ev = events[0]
    assert ev.signal == "sdk.lane_b.base_refreshed"
    assert ev.payload["outcome"] == "refused"
    assert ev.payload["old_tip"] == old_tip
    assert ev.payload["new_tip"] == old_tip
    assert ev.payload["dispatch_id"] == dispatch_id
    assert ev.payload["thread_id"] == thread_id


def test_refresh_updates_branch_point_only(
    source_repo: Path, worktree_root: Path
) -> None:
    thread_id = "t-bp"
    branch = lane_branch_name(thread_id)
    _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    before = _registry_snapshot(source_repo, thread_id)
    _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="bp-2", execution_id="exec-bp-2"),
    )
    after = _registry_snapshot(source_repo, thread_id)
    row = lookup_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    assert row is not None
    assert (
        int(_git("rev-list", "--count", f"{row.branch_point}..HEAD", cwd=row.worktree_path))
        == 0
    )
    for key in ("minted_at", "salvage_refusal_count", "quarantined_at"):
        assert after[key] == before[key]
    assert get_disposition(branch_name=branch) is None


def test_unlanded_lane_left_untouched(
    source_repo: Path, worktree_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = "t-unlanded"
    wt = _mint_lane(source_repo, worktree_root, thread_id=thread_id)
    _commit_on_lane(wt, "only-lane.md")
    before = _lane_tip(wt)
    _advance_master(source_repo, n=2, prefix="unlanded")
    events = _capture_lane_b_events(monkeypatch)
    binding = _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="u2", execution_id="exec-u2"),
    )
    assert binding.binding_kind == "reused"
    assert _lane_tip(wt) == before
    assert events and events[-1].payload["outcome"] == "left_unlanded"
    assert events[-1].payload["behind_by"] >= 1


def test_tracked_dirty_landed_lane_refused(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-dirty"
    wt, old_tip = _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    path = wt / "t-dirty-0.md"
    dirty_bytes = b"dirty\n"
    path.write_bytes(dirty_bytes)
    events = _capture_lane_b_events(monkeypatch)
    dispatch_id = "d2"
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(thread_id=thread_id, dispatch_id=dispatch_id, execution_id="exec-d2"),
        )
    assert path.read_bytes() == dirty_bytes
    assert len(events) == 1
    ev = events[0]
    assert ev.signal == "sdk.lane_b.base_refreshed"
    assert ev.payload["outcome"] == "refused"
    assert ev.payload["old_tip"] == old_tip
    assert ev.payload["new_tip"] == old_tip
    assert ev.payload["thread_id"] == thread_id
    assert ev.payload["dispatch_id"] == dispatch_id
    assert ev.payload["behind_by"] >= 1


def test_untracked_collision_refused(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-untr"
    wt, old_tip = _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    collision = wt / "t-untr-master-3.md"
    payload = b"collision-bytes\n"
    collision.write_bytes(payload)
    dispatch_id = "ut2"
    events = _capture_lane_b_events(monkeypatch)
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(
                thread_id=thread_id,
                dispatch_id=dispatch_id,
                execution_id="exec-ut2",
            ),
        )
    assert collision.read_bytes() == payload
    assert len(events) == 1
    ev = events[0]
    assert ev.signal == "sdk.lane_b.base_refreshed"
    assert ev.payload["outcome"] == "refused"
    assert ev.payload["old_tip"] == old_tip
    assert ev.payload["new_tip"] == old_tip
    assert ev.payload["dispatch_id"] == dispatch_id
    assert ev.payload["thread_id"] == thread_id


def test_git_status_failure_is_not_clean(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-status"
    wt, _ = _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    before = _lane_tip(wt)
    def _wrapped(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:1] == ("status",):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")
        return subprocess.run(
            ["git", "-C", str(cwd.resolve()), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    monkeypatch.setattr(lane_refresh_mod, "_git_runner", _wrapped)
    events = _capture_lane_b_events(monkeypatch)
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(thread_id=thread_id, dispatch_id="st2", execution_id="exec-st2"),
        )
    assert _lane_tip(wt) == before
    assert events and events[-1].payload["outcome"] == "refused"


_LIVE_STATUSES = ("admitted", "running", "queued", "parked_waiting")


@pytest.mark.parametrize("status", _LIVE_STATUSES)
def test_live_sibling_skips_refresh(
    source_repo: Path,
    worktree_root: Path,
    status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-sib"
    wt, _ = _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    before = _lane_tip(wt)
    ledger = CursorDispatchLedger.instance()
    sibling = _req(
        thread_id=thread_id,
        dispatch_id="sibling-live",
        execution_id="exec-sibling",
    )
    ledger.admit(
        req=sibling,
        fingerprint="fp-sibling",
        execution_id="exec-sibling",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="sibling-live",
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status=? WHERE dispatch_id=?",
            (status, "sibling-live"),
        )
    events = _capture_lane_b_events(monkeypatch)
    _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="sib2", execution_id="exec-sib2"),
    )
    assert _lane_tip(wt) == before
    assert events and events[-1].payload["outcome"] == "skipped_live_writer"


def test_own_dispatch_row_does_not_block(
    source_repo: Path, worktree_root: Path
) -> None:
    thread_id = "t-own"
    wt, _ = _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    ledger = CursorDispatchLedger.instance()
    own = _req(
        thread_id=thread_id,
        dispatch_id="own-row",
        execution_id="exec-own",
    )
    ledger.admit(
        req=own,
        fingerprint="fp-own",
        execution_id="exec-own",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="own-row",
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    binding = _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="own-row", execution_id="exec-own"),
    )
    assert _lane_tip(binding.workspace) == _master_tip(source_repo)


def test_mint_attach_and_adopt_refresh(
    source_repo: Path, worktree_root: Path
) -> None:
    thread_id = "t-attach"
    wt = _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="a1")
    _advance_master(source_repo, n=2, prefix="attach")
    import shutil

    shutil.rmtree(wt)
    _git("worktree", "prune", cwd=source_repo)
    unregister_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    minted = _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="a2", execution_id="exec-a2"),
    )
    assert minted.binding_kind == "minted"
    assert _lane_tip(minted.workspace) == _master_tip(source_repo)

    thread_id2 = "t-adopt"
    orphan = _mint_lane(source_repo, worktree_root, thread_id=thread_id2, dispatch_id="ad1")
    _advance_master(source_repo, n=1, prefix="adopt")
    unregister_lane_worktree(thread_id=thread_id2, source_repo=source_repo)
    adopted = _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id2, dispatch_id="ad2", execution_id="exec-ad2"),
    )
    assert adopted.binding_kind == "adopted"
    assert adopted.workspace.resolve() == orphan.resolve()
    assert _lane_tip(adopted.workspace) == _master_tip(source_repo)


def test_quarantined_lane_refused(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-quar"
    wt, _ = _landed_behind_patch_equivalent(source_repo, worktree_root, thread_id)
    before = _lane_tip(wt)
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees SET quarantined_at=?, salvage_refusal_count=3 "
            "WHERE source_repo=? AND thread_id=?",
            ("2020-01-01T00:00:00+00:00", str(source_repo.resolve()), thread_id),
        )
    snap_before = _registry_snapshot(source_repo, thread_id)
    events = _capture_lane_b_events(monkeypatch)
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(thread_id=thread_id, dispatch_id="q2", execution_id="exec-q2"),
        )
    assert _lane_tip(wt) == before
    assert events and events[-1].payload["outcome"] == "refused"
    snap_after = _registry_snapshot(source_repo, thread_id)
    assert snap_after["quarantined_at"] == snap_before["quarantined_at"]
    assert snap_after["salvage_refusal_count"] == snap_before["salvage_refusal_count"]


def test_stale_lane_route_mapping(
    tmp_path: Path,
    source_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir()
    dispatch_ws = tmp_path / "dispatch_ws"
    dispatch_ws.mkdir()
    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=source_repo,
        worktree_root=wt_root,
        dispatch_workspace=dispatch_ws,
        green_gate_cmd=["true"],
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(cfg.source_repo))
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(cfg.worktree_root))
    monkeypatch.setenv(
        "GIT_INTEGRATION_DISPATCH_WORKSPACE", str(cfg.dispatch_workspace)
    )
    monkeypatch.setattr(route_mod, "_CONFIG", cfg)
    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"setting_sources": ["projectSettings"]},
    )

    async def _noop_acquire(**kwargs: object) -> str:
        return str(kwargs.get("dispatch_id") or "slot")

    monkeypatch.setattr(route_mod, "acquire_sdk_dispatch_slot", _noop_acquire)

    thread_id = "t-route"
    wt, _ = _landed_behind_patch_equivalent(source_repo, wt_root, thread_id)
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees SET quarantined_at=? "
            "WHERE source_repo=? AND thread_id=?",
            ("2020-01-01T00:00:00+00:00", str(source_repo.resolve()), thread_id),
        )
    client = TestClient(create_app())

    @patch(
        "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
        return_value=MagicMock(done=lambda: False),
    )
    def _post(_mock: MagicMock) -> None:
        body = {
            "thread_id": thread_id,
            "model": "cursor/composer-2.5",
            "dispatch_id": "route-stale",
            "execution_id": "exec-route-stale",
            "handoff_contract": "implement",
            "lane": "B",
            "message": "---\ncontract: implement\nsource_ref: todo:x\nfiles_expected:\n- a.py\n---\n",
        }
        resp = client.post("/api/v1/cursor/dispatch", json=body)
        assert resp.status_code == 409
        payload = resp.json()
        assert payload["code"] == "CURSOR_STALE_LANE"
        assert payload.get("retryable") is False
        assert payload.get("data", {}).get("validation_stage") == "stale_lane"

    _post()

    monkeypatch.setattr(
        route_mod,
        "resolve_admit_binding",
        lambda **_k: (_ for _ in ()).throw(
            route_mod.WorktreeMintError("mint broke", retryable=False)
        ),
    )

    @patch(
        "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
        return_value=MagicMock(done=lambda: False),
    )
    def _mint_fail(_mock: MagicMock) -> None:
        body = {
            "thread_id": "other",
            "model": "cursor/composer-2.5",
            "dispatch_id": "route-mint",
            "execution_id": "exec-route-mint",
            "handoff_contract": "implement",
            "lane": "B",
            "message": "---\ncontract: implement\nsource_ref: todo:y\nfiles_expected:\n- b.py\n---\n",
        }
        resp = client.post("/api/v1/cursor/dispatch", json=body)
        assert resp.status_code == 503
        assert resp.json()["code"] == "CURSOR_WORKTREE_MINT_FAILED"

    _mint_fail()
    _ = wt


def _assert_refresh_event(
    events: list[Event],
    *,
    outcome: str,
    old_tip: str,
    new_tip: str,
    dispatch_id: str,
    thread_id: str,
    behind_by: int,
) -> None:
    assert len(events) == 1
    ev = events[0]
    assert ev.signal == "sdk.lane_b.base_refreshed"
    assert ev.scope == "node"
    assert ev.payload["outcome"] == outcome
    assert ev.payload["old_tip"] == old_tip
    assert ev.payload["new_tip"] == new_tip
    assert ev.payload["dispatch_id"] == dispatch_id
    assert ev.payload["thread_id"] == thread_id
    assert ev.payload["behind_by"] == behind_by


def test_refresh_event_outcomes(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_ff = "t-ev-ff"
    _mint_lane(source_repo, worktree_root, thread_id=thread_ff, dispatch_id="ev1")
    _advance_master(source_repo, n=1, prefix="ev-ff")
    wt_ff = lookup_lane_worktree(thread_id=thread_ff, source_repo=source_repo)
    assert wt_ff is not None
    old_ff = _lane_tip(wt_ff.worktree_path)
    master = _master_tip(source_repo)
    events_ff: list[Event] = []
    _wire_event_capture(monkeypatch, events_ff)
    dispatch_ff = "ev1b"
    _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_ff, dispatch_id=dispatch_ff, execution_id="exec-ev1b"),
    )
    _assert_refresh_event(
        events_ff,
        outcome="fast_forwarded",
        old_tip=old_ff,
        new_tip=master,
        dispatch_id=dispatch_ff,
        thread_id=thread_ff,
        behind_by=1,
    )

    thread_reset = "t-ev-reset"
    wt_reset, old_reset = _landed_behind_patch_equivalent(
        source_repo, worktree_root, thread_reset, file_prefix="evreset"
    )
    master_reset = _master_tip(source_repo)
    events_reset: list[Event] = []
    _wire_event_capture(monkeypatch, events_reset)
    dispatch_reset = "ev2"
    behind_reset = int(
        _git(
            "rev-list",
            "--count",
            f"{lane_branch_name(thread_reset)}..master",
            cwd=source_repo,
        )
    )
    _binding(
        source_repo,
        worktree_root,
        _req(
            thread_id=thread_reset,
            dispatch_id=dispatch_reset,
            execution_id="exec-ev2",
        ),
    )
    _assert_refresh_event(
        events_reset,
        outcome="reset_after_archive",
        old_tip=old_reset,
        new_tip=master_reset,
        dispatch_id=dispatch_reset,
        thread_id=thread_reset,
        behind_by=behind_reset,
    )
    assert _lane_tip(wt_reset) == master_reset

    thread_skip = "t-ev-skip"
    wt3, old_skip = _landed_behind_patch_equivalent(
        source_repo, worktree_root, thread_skip, file_prefix="evskip"
    )
    behind_skip = int(
        _git(
            "rev-list",
            "--count",
            f"{lane_branch_name(thread_skip)}..master",
            cwd=source_repo,
        )
    )
    ledger = CursorDispatchLedger.instance()
    ledger.admit(
        req=_req(thread_id=thread_skip, dispatch_id="ev3s", execution_id="exec-ev3s"),
        fingerprint="fp",
        execution_id="exec-ev3s",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="ev3s",
            thread_id=thread_skip,
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt3.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    events_skip: list[Event] = []
    _wire_event_capture(monkeypatch, events_skip)
    dispatch_skip = "ev3"
    _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_skip, dispatch_id=dispatch_skip, execution_id="exec-ev3"),
    )
    _assert_refresh_event(
        events_skip,
        outcome="skipped_live_writer",
        old_tip=old_skip,
        new_tip=old_skip,
        dispatch_id=dispatch_skip,
        thread_id=thread_skip,
        behind_by=behind_skip,
    )

    thread_left = "t-ev-left"
    wt4 = _mint_lane(source_repo, worktree_root, thread_id=thread_left, dispatch_id="ev4")
    old_left = _commit_on_lane(wt4, "unlanded.md")
    _advance_master(source_repo, n=1, prefix="ev-left")
    behind_left = int(
        _git(
            "rev-list",
            "--count",
            f"{lane_branch_name(thread_left)}..master",
            cwd=source_repo,
        )
    )
    events_left: list[Event] = []
    _wire_event_capture(monkeypatch, events_left)
    dispatch_left = "ev4b"
    _binding(
        source_repo,
        worktree_root,
        _req(
            thread_id=thread_left,
            dispatch_id=dispatch_left,
            execution_id="exec-ev4b",
        ),
    )
    _assert_refresh_event(
        events_left,
        outcome="left_unlanded",
        old_tip=old_left,
        new_tip=old_left,
        dispatch_id=dispatch_left,
        thread_id=thread_left,
        behind_by=behind_left,
    )

    thread_ref = "t-ev-ref"
    wt5, old_ref = _landed_behind_patch_equivalent(
        source_repo, worktree_root, thread_ref, file_prefix="evref"
    )
    behind_ref = int(
        _git(
            "rev-list",
            "--count",
            f"{lane_branch_name(thread_ref)}..master",
            cwd=source_repo,
        )
    )
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees SET quarantined_at=? "
            "WHERE source_repo=? AND thread_id=?",
            ("2020-01-01T00:00:00+00:00", str(source_repo.resolve()), thread_ref),
        )
    events_ref: list[Event] = []
    _wire_event_capture(monkeypatch, events_ref)
    dispatch_ref = "ev5"
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(
                thread_id=thread_ref,
                dispatch_id=dispatch_ref,
                execution_id="exec-ev5",
            ),
        )
    _assert_refresh_event(
        events_ref,
        outcome="refused",
        old_tip=old_ref,
        new_tip=old_ref,
        dispatch_id=dispatch_ref,
        thread_id=thread_ref,
        behind_by=behind_ref,
    )
    assert _lane_tip(wt5) == old_ref


def test_git_invocations_use_pinned_master_sha(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-pin-sha"
    _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="pin1")
    _advance_master(source_repo, n=2, prefix="pin-m")
    master = _master_tip(source_repo)
    invocations: list[tuple[str, ...]] = []

    def _wrapped(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        invocations.append(args)
        return subprocess.run(
            ["git", "-C", str(cwd.resolve()), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    monkeypatch.setattr(lane_refresh_mod, "_git_runner", _wrapped, raising=False)
    events = _capture_lane_b_events(monkeypatch)
    _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="pin2", execution_id="exec-pin2"),
    )
    assert len(events) == 1
    assert events[0].payload["new_tip"] == master
    row = lookup_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    assert row is not None
    assert row.branch_point == master
    assert _lane_tip(row.worktree_path) == master
    for args in invocations:
        if args[:2] == ("rev-list", "--count"):
            assert args[2].endswith(master)
            assert "..master" not in args[2]
        elif args[:3] == ("merge-base", "--is-ancestor"):
            assert args[3] == master
        elif args[:2] == ("merge", "--ff-only"):
            assert args[2] == master
        elif args[:1] == ("cherry",):
            assert args[1] == master


def test_stale_branch_point_repaired_when_behind_zero(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-stale-bp"
    _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="sbp1")
    _advance_master(source_repo, n=1, prefix="sbp-m")
    _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="sbp2", execution_id="exec-sbp2"),
    )
    master = _master_tip(source_repo)
    seed_sha = _git("rev-list", "--max-parents=0", "HEAD", cwd=source_repo)
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees SET branch_point=? "
            "WHERE source_repo=? AND thread_id=?",
            (seed_sha, str(source_repo.resolve()), thread_id),
        )
    row = lookup_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    assert row is not None
    assert row.branch_point == seed_sha
    assert _lane_tip(row.worktree_path) == master
    events = _capture_lane_b_events(monkeypatch)
    _binding(
        source_repo,
        worktree_root,
        _req(thread_id=thread_id, dispatch_id="sbp3", execution_id="exec-sbp3"),
    )
    assert len(events) == 0
    row = lookup_lane_worktree(thread_id=thread_id, source_repo=source_repo)
    assert row is not None
    assert row.branch_point == master


def test_git_timeout_emits_refused_once(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-timeout"
    _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="to1")
    _advance_master(source_repo, n=1, prefix="to-m")

    def _wrapped(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("merge", "--ff-only"):
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=60.0)
        return subprocess.run(
            ["git", "-C", str(cwd.resolve()), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    monkeypatch.setattr(lane_refresh_mod, "_git_runner", _wrapped, raising=False)
    events = _capture_lane_b_events(monkeypatch)
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(thread_id=thread_id, dispatch_id="to2", execution_id="exec-to2"),
        )
    assert len(events) == 1
    assert events[0].signal == "sdk.lane_b.base_refreshed"
    assert events[0].payload["outcome"] == "refused"


def test_archive_branch_exception_emits_refused_once(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-arch-exc"
    _landed_behind_patch_equivalent(
        source_repo, worktree_root, thread_id, file_prefix="archex"
    )
    events = _capture_lane_b_events(monkeypatch)

    def _raise(**_kwargs: object) -> str:
        raise RuntimeError("archive exploded")

    monkeypatch.setattr(lane_refresh_mod, "archive_branch", _raise)
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(thread_id=thread_id, dispatch_id="ax2", execution_id="exec-ax2"),
        )
    assert len(events) == 1
    assert events[0].payload["outcome"] == "refused"


def test_update_branch_point_exception_emits_refused_once(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-ubp-exc"
    _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="ubp1")
    _advance_master(source_repo, n=1, prefix="ubp-m")
    events = _capture_lane_b_events(monkeypatch)
    calls = {"n": 0}

    def _flaky(**kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("registry write failed")
        from services.git_integration_worker.cursor_sdk_worktree_registry import (
            update_lane_branch_point as real,
        )

        real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(lane_refresh_mod, "update_lane_branch_point", _flaky)
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(thread_id=thread_id, dispatch_id="ubp2", execution_id="exec-ubp2"),
        )
    assert len(events) == 1
    assert events[0].payload["outcome"] == "refused"


def test_release_mutex_failure_after_refusal_emits_once(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-rel-refuse"
    wt, old_tip = _landed_behind_patch_equivalent(
        source_repo, worktree_root, thread_id, file_prefix="relref"
    )
    dispatch_id = "rel-r2"
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees SET quarantined_at=? "
            "WHERE source_repo=? AND thread_id=?",
            ("2020-01-01T00:00:00+00:00", str(source_repo.resolve()), thread_id),
        )
    events = _capture_lane_b_events(monkeypatch)

    def _release_fail(**_kwargs: object) -> None:
        raise RuntimeError("sqlite release failed")

    monkeypatch.setattr(lane_refresh_mod, "release_mint_mutex", _release_fail)
    with pytest.raises(StaleLaneError):
        _binding(
            source_repo,
            worktree_root,
            _req(
                thread_id=thread_id,
                dispatch_id=dispatch_id,
                execution_id="exec-rel-r2",
            ),
        )
    assert _lane_tip(wt) == old_tip
    assert len(events) == 1
    ev = events[0]
    assert ev.signal == "sdk.lane_b.base_refreshed"
    assert ev.scope == "node"
    assert ev.payload["outcome"] == "refused"


def test_release_mutex_failure_after_fast_forward_emits_once(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-rel-ff"
    _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="relff1")
    _advance_master(source_repo, n=2, prefix="rel-ff")
    master = _master_tip(source_repo)
    events = _capture_lane_b_events(monkeypatch)
    dispatch_id = "relff2"

    def _release_fail(**_kwargs: object) -> None:
        raise RuntimeError("sqlite release failed")

    monkeypatch.setattr(lane_refresh_mod, "release_mint_mutex", _release_fail)
    binding = _binding(
        source_repo,
        worktree_root,
        _req(
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            execution_id="exec-relff2",
        ),
    )
    assert binding.binding_kind == "reused"
    assert _lane_tip(binding.workspace) == master
    assert len(events) == 1
    ev = events[0]
    assert ev.signal == "sdk.lane_b.base_refreshed"
    assert ev.scope == "node"
    assert ev.payload["outcome"] == "fast_forwarded"
    assert ev.payload["new_tip"] == master
    refused = [e for e in events if e.payload.get("outcome") == "refused"]
    assert len(refused) == 0


def test_release_mutex_failure_after_skipped_live_writer(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "t-rel-skip"
    wt, old_tip = _landed_behind_patch_equivalent(
        source_repo, worktree_root, thread_id, file_prefix="relskip"
    )
    ledger = CursorDispatchLedger.instance()
    sibling = _req(
        thread_id=thread_id,
        dispatch_id="rel-sib",
        execution_id="exec-rel-sib",
    )
    ledger.admit(
        req=sibling,
        fingerprint="fp-rel-sib",
        execution_id="exec-rel-sib",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="rel-sib",
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    events = _capture_lane_b_events(monkeypatch)
    dispatch_id = "rel-skip2"

    def _release_fail(**_kwargs: object) -> None:
        raise RuntimeError("sqlite release failed")

    monkeypatch.setattr(lane_refresh_mod, "release_mint_mutex", _release_fail)
    _binding(
        source_repo,
        worktree_root,
        _req(
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            execution_id="exec-rel-skip2",
        ),
    )
    assert _lane_tip(wt) == old_tip
    assert len(events) == 1
    ev = events[0]
    assert ev.signal == "sdk.lane_b.base_refreshed"
    assert ev.scope == "node"
    assert ev.payload["outcome"] == "skipped_live_writer"
    assert ev.payload["old_tip"] == old_tip
    refused = [e for e in events if e.payload.get("outcome") == "refused"]
    assert len(refused) == 0


def test_success_path_emit_failure_logged_not_refused(
    source_repo: Path,
    worktree_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from services.git_integration_worker import cursor_sdk_events as ev_mod

    thread_id = "t-emit-fail"
    _mint_lane(source_repo, worktree_root, thread_id=thread_id, dispatch_id="emf1")
    _advance_master(source_repo, n=1, prefix="emf-m")
    master = _master_tip(source_repo)
    events = _capture_lane_b_events(monkeypatch)

    def _emit_boom(event: Event) -> None:
        if event.signal == "sdk.lane_b.base_refreshed":
            raise RuntimeError("publish down")

    monkeypatch.setattr(ev_mod, "_emit", _emit_boom)
    with caplog.at_level("ERROR"):
        binding = _binding(
            source_repo,
            worktree_root,
            _req(thread_id=thread_id, dispatch_id="emf2", execution_id="exec-emf2"),
        )
    assert binding.binding_kind == "reused"
    assert _lane_tip(binding.workspace) == master
    assert len(events) == 0
    assert not any(
        "outcome=refused" in rec.getMessage() for rec in caplog.records
    )
    assert any(
        "base_refreshed emit failed" in rec.getMessage() for rec in caplog.records
    ), caplog.text
