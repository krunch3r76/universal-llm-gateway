"""Capture binding seam — Lane-A shared checkout vs Lane-B per-dispatch worktree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_sdk_manifest import (
    registered_repo_roots,
    resolve_mount_root,
)
from services.git_integration_worker.cursor_sdk_worktree import is_managed_worktree


def _lane_a_repo_roots(cfg: WorkerConfig, mount: Path) -> tuple[Path, ...]:
    """Registered repos plus git worktrees minted under ``worktree_root``."""
    roots = [repo.resolve() for repo in registered_repo_roots(mount)]
    wt_root = cfg.worktree_root.resolve()
    if wt_root.is_dir():
        try:
            for child in sorted(wt_root.iterdir()):
                if child.is_dir() and (child / ".git").exists():
                    resolved = child.resolve()
                    if resolved not in roots:
                        roots.append(resolved)
        except OSError:
            pass
    return tuple(roots)


@dataclass(frozen=True, slots=True)
class CaptureBinding:
    """Where a dispatch's effects are observed vs where its receipts persist."""

    lane: Literal["A", "B"]
    write_tree: Path
    receipt_tree: Path
    mount_root: Path
    repo_roots: tuple[Path, ...]

    @classmethod
    def lane_a(
        cls,
        cfg: WorkerConfig,
        *,
        dispatch_source_repo: Path | None = None,
    ) -> CaptureBinding:
        hub = cfg.source_repo.resolve()
        write_tree = (dispatch_source_repo or hub).resolve()
        receipt_tree = hub
        mount = resolve_mount_root(write_tree)
        if write_tree == hub:
            repo_roots = _lane_a_repo_roots(cfg, mount)
        else:
            repo_roots = (write_tree,)
        return cls(
            lane="A",
            write_tree=write_tree,
            receipt_tree=receipt_tree,
            mount_root=mount,
            repo_roots=repo_roots,
        )

    @classmethod
    def lane_b(cls, cfg: WorkerConfig, write_tree: Path) -> CaptureBinding:
        wt = write_tree.resolve()
        return cls(
            lane="B",
            write_tree=wt,
            receipt_tree=cfg.source_repo.resolve(),
            mount_root=wt,
            repo_roots=(wt,),
        )


def binding_for_dispatch(
    *,
    cfg: WorkerConfig,
    lease_key: str | None = None,
    dispatch_source_repo: Path | None = None,
) -> CaptureBinding:
    """Rebuild capture binding from a dispatch lease key."""
    if lease_key and is_managed_worktree(Path(lease_key), cfg.worktree_root):
        return CaptureBinding.lane_b(cfg, Path(lease_key))
    return CaptureBinding.lane_a(cfg, dispatch_source_repo=dispatch_source_repo)
