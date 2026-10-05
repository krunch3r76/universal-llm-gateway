"""Spawn-time Lane-B remint — friction 37813 missing cwd before bridge launch."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_sdk_bridge_launch import launch_sdk_bridge
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_dispatch_context import (
    SdkDispatchContext,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    WorktreeMintError,
    mint_dispatch_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_remint import (
    ensure_spawn_workspace,
    remint_lane_worktree,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _cfg(hub: Path, worktree_root: Path, dispatch_ws: Path) -> WorkerConfig:
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=hub,
        worktree_root=worktree_root,
        dispatch_workspace=dispatch_ws,
        green_gate_cmd=["true"],
    )


def _lane_b_ctx(
    hub: Path, write_tree: Path, *, dispatch_id: str, thread_id: str, cfg: WorkerConfig
) -> SdkDispatchContext:
    return SdkDispatchContext(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        handoff_contract="implement",
        hub=hub,
        dispatch_workspace=write_tree,
        capture_binding=CaptureBinding.lane_b(cfg, write_tree),
    )


def _launch_kwargs(tmp_path: Path) -> dict[str, object]:
    home = tmp_path / "dispatch-home"
    home.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    venv = tmp_path / "repo-venv"
    (venv / "bin").mkdir(parents=True)
    return {
        "bridge_state": state,
        "dispatch_home": home,
        "repo_venv": venv,
        "real_home": tmp_path / "operator-home",
        "local": None,
        "client_timeout": httpx.Timeout(5.0),
    }


def test_ensure_spawn_workspace_skips_lane_a(tmp_path: Path) -> None:
    """Lane A must not remint when dispatch_workspace is absent."""
    hub = tmp_path / "hub"
    hub.mkdir()
    missing = tmp_path / "no-such-shared"
    ctx = SdkDispatchContext(
        dispatch_id="a1",
        thread_id="ta",
        handoff_contract="consult",
        hub=hub,
        dispatch_workspace=missing,
        capture_binding=CaptureBinding(
            lane="A",
            write_tree=hub.resolve(),
            receipt_tree=hub.resolve(),
            mount_root=hub.resolve(),
            repo_roots=(hub.resolve(),),
        ),
    )
    out = ensure_spawn_workspace(ctx)
    assert out is ctx
    assert not missing.exists()


def test_ensure_spawn_workspace_noop_when_lane_b_present(tmp_path: Path) -> None:
    """Present isolation tree must not remint."""
    hub = tmp_path / "hub"
    hub.mkdir()
    wt = tmp_path / "lane-t1"
    wt.mkdir()
    cfg = _cfg(hub, tmp_path / "wtroot", tmp_path / "dws")
    ctx = _lane_b_ctx(hub, wt, dispatch_id="d1", thread_id="t1", cfg=cfg)
    out = ensure_spawn_workspace(ctx)
    assert out is ctx


def test_ensure_spawn_workspace_remints_missing_lane_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing Lane-B dir at spawn remints before the caller proceeds."""
    hub = tmp_path / "hub"
    hub.mkdir()
    missing = tmp_path / "lane-missing"
    restored = tmp_path / "lane-restored"
    cfg = _cfg(hub, tmp_path / "wtroot", tmp_path / "dws")
    ctx = _lane_b_ctx(hub, missing, dispatch_id="d2", thread_id="t2", cfg=cfg)
    calls: list[str] = []

    def _remint(**kwargs: object) -> Path:
        calls.append(str(kwargs["thread_id"]))
        restored.mkdir()
        return restored

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_remint.remint_lane_worktree",
        _remint,
    )
    out = ensure_spawn_workspace(ctx)
    assert calls == ["t2"]
    assert out.dispatch_workspace == restored
    assert out.capture_binding.write_tree == restored.resolve()
    assert restored.is_dir()


def test_ensure_spawn_workspace_raises_when_remint_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remint that does not restore a directory must not reach Node spawn."""
    hub = tmp_path / "hub"
    hub.mkdir()
    missing = tmp_path / "lane-gone"
    cfg = _cfg(hub, tmp_path / "wtroot", tmp_path / "dws")
    ctx = _lane_b_ctx(hub, missing, dispatch_id="d3", thread_id="t3", cfg=cfg)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_remint.remint_lane_worktree",
        lambda **_k: tmp_path / "still-missing",
    )
    with pytest.raises(WorktreeMintError, match="missing at bridge spawn"):
        ensure_spawn_workspace(ctx)


def test_launch_sdk_bridge_remints_before_client_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Client.launch_bridge`` must see a directory, not a vanished lane path."""
    import services.git_integration_worker.cursor_sdk_bridge_launch as bridge_launch

    hub = tmp_path / "hub"
    hub.mkdir()
    missing = tmp_path / "lane-spawn-missing"
    restored = tmp_path / "lane-spawn-restored"
    cfg = _cfg(hub, tmp_path / "wtroot", tmp_path / "dws")
    ctx = _lane_b_ctx(hub, missing, dispatch_id="d4", thread_id="t4", cfg=cfg)
    captured: dict[str, str] = {}

    def _remint(**_kwargs: object) -> Path:
        restored.mkdir()
        return restored

    def _launch_bridge(*, command, workspace, **_kwargs):  # noqa: ARG001
        captured["workspace"] = workspace
        return object()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_remint.remint_lane_worktree",
        _remint,
    )
    monkeypatch.setattr(bridge_launch.Client, "launch_bridge", _launch_bridge)
    launch_sdk_bridge(ctx, **_launch_kwargs(tmp_path))
    assert captured["workspace"] == str(restored)
    assert restored.is_dir()


def test_launch_sdk_bridge_does_not_spawn_when_remint_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed remint must raise before ``Client.launch_bridge``."""
    import services.git_integration_worker.cursor_sdk_bridge_launch as bridge_launch

    hub = tmp_path / "hub"
    hub.mkdir()
    missing = tmp_path / "lane-fail"
    cfg = _cfg(hub, tmp_path / "wtroot", tmp_path / "dws")
    ctx = _lane_b_ctx(hub, missing, dispatch_id="d5", thread_id="t5", cfg=cfg)
    launched = {"n": 0}

    def _launch_bridge(**_kwargs):
        launched["n"] += 1
        return object()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_remint.remint_lane_worktree",
        lambda **_k: (_ for _ in ()).throw(
            WorktreeMintError("git lock", retryable=True)
        ),
    )
    monkeypatch.setattr(bridge_launch.Client, "launch_bridge", _launch_bridge)
    with pytest.raises(WorktreeMintError, match="git lock"):
        launch_sdk_bridge(ctx, **_launch_kwargs(tmp_path))
    assert launched["n"] == 0


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


def test_remint_after_hand_deleted_directory(source_repo: Path, tmp_path: Path) -> None:
    """Hand-deleted lane dir (git metadata stale) remints by attaching the branch."""
    worktree_root = tmp_path / "worktrees"
    thread_id = "t-hand-delete"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="first",
        thread_id=thread_id,
    )
    kept = wt / "kept.txt"
    kept.write_text("visible\n", encoding="utf-8")
    _git("add", "kept.txt", cwd=wt)
    _git("commit", "-m", "keep", cwd=wt)
    shutil.rmtree(wt)
    assert not wt.exists()
    reminted = remint_lane_worktree(
        source_repo=source_repo,
        dispatch_id="second",
        thread_id=thread_id,
        worktree_root=worktree_root,
    )
    assert reminted.is_dir()
    assert (reminted / "kept.txt").read_text(encoding="utf-8") == "visible\n"
