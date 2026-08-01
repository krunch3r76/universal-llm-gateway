"""Lane-B quiescent sweeper — commit only ledger-registered paths on closed arcs.

Owner: git_integration_worker periodic loop (unattended git substrate).
Quiescence: arc ``closed`` (authoring seat moved on) AND ``last_touch_at`` older
than ``LANE_B_QUIESCENCE_S`` (default 300s). Never touches unregistered paths.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from git_integrate.commit_paths import commit_paths
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_closeout import capture_wt_baseline
from services.git_integration_worker.seat_write_ledger import (
    QuiescentArcBatch,
    SeatWriteLedger,
)

logger = get_logger(__name__)

LANE_B_QUIESCENCE_S = float(os.getenv("LANE_B_QUIESCENCE_S", "300"))
LANE_B_SWEEP_INTERVAL_S = float(os.getenv("LANE_B_SWEEP_INTERVAL_S", "120"))
_GIT_TIMEOUT_S = 30.0

# Paths the registration substrate cannot observe (disclosed, not smoothed over).
REGISTRATION_GAPS: tuple[str, ...] = (
    "Tab inline completion edits (afterTabFileEdit hook not wired in v1)",
    "External editor / shell writes outside Cursor hook surface",
    "cursor-sdk dispatch paths (lane A wt_baseline — not seat ledger)",
)


@dataclass(frozen=True, slots=True)
class SweepResult:
    """Outcome of one lane-B sweep pass."""

    committed: tuple[tuple[str, str, int], ...]
    skipped_open_arc: tuple[str, ...]
    skipped_unregistered: tuple[str, ...]
    skipped_not_dirty: tuple[str, ...]

    @property
    def paths_committed(self) -> int:
        return sum(count for _sha, _arc, count in self.committed)


def dirty_paths(source_repo: Path) -> set[str]:
    """Repo-relative paths dirty vs HEAD (porcelain names only)."""
    baseline = capture_wt_baseline(source_repo) or {}
    return set(baseline.keys())


def _current_branch(source_repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    branch = proc.stdout.strip()
    return branch if branch and branch != "HEAD" else None


def select_sweep_paths(
    *,
    batch: QuiescentArcBatch,
    dirty: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (to_commit, skipped_not_dirty) for one closed-arc batch."""
    to_commit: list[str] = []
    skipped_not_dirty: list[str] = []
    for path in batch.paths:
        if path not in dirty:
            skipped_not_dirty.append(path)
            continue
        to_commit.append(path)
    return tuple(to_commit), tuple(skipped_not_dirty)


async def sweep_lane_b_writes(
    source_repo: Path,
    *,
    quiescence_s: float | None = None,
    dry_run: bool = False,
) -> SweepResult:
    """Commit closed-arc registered paths; never unregistered dirty paths."""
    repo = source_repo.resolve()
    window = LANE_B_QUIESCENCE_S if quiescence_s is None else quiescence_s
    ledger = SeatWriteLedger.instance()
    dirty = dirty_paths(repo)
    committed: list[tuple[str, str, int]] = []
    skipped_not_dirty: list[str] = []
    skipped_unregistered: list[str] = []

    for batch in ledger.quiescent_batches(source_repo=str(repo), quiescence_s=window):
        paths, not_dirty = select_sweep_paths(batch=batch, dirty=dirty)
        skipped_not_dirty.extend(not_dirty)
        if not paths:
            continue
        if dry_run:
            committed.append(("dry-run", batch.arc_id, len(paths)))
            continue
        branch = _current_branch(repo)
        if branch is None:
            logger.warning("lane_b_sweep: cannot resolve branch repo=%s", repo)
            continue
        message = f"lane-b: {batch.arc_id} seat={batch.seat_id} paths={len(paths)}"
        result = await commit_paths(str(repo), list(paths), message)
        if result.committed and result.commit_sha:
            committed.append((result.commit_sha, batch.arc_id, len(paths)))
            ledger.clear_swept_paths(arc_id=batch.arc_id, paths=paths)
            dirty -= set(paths)

    for path in dirty:
        if path not in ledger.registered_paths(source_repo=str(repo)):
            skipped_unregistered.append(path)

    return SweepResult(
        committed=tuple(committed),
        skipped_open_arc=ledger.open_arcs_with_paths(source_repo=str(repo)),
        skipped_unregistered=tuple(sorted(set(skipped_unregistered))),
        skipped_not_dirty=tuple(skipped_not_dirty),
    )

async def lane_b_sweeper_loop(app) -> None:
    """Periodic unattended sweep — mirrors ``stale_lease_sweeper`` shape."""
    while True:
        await asyncio.sleep(LANE_B_SWEEP_INTERVAL_S)
        cfg = getattr(app.state, "worker_config", None)
        if cfg is None:
            continue
        repo = Path(cfg.source_repo)
        if not repo.is_dir():
            continue
        try:
            result = await sweep_lane_b_writes(repo)
            if result.committed:
                logger.info(
                    "lane_b_sweep committed=%d open_skipped=%d unregistered=%d",
                    result.paths_committed,
                    len(result.skipped_open_arc),
                    len(result.skipped_unregistered),
                )
        except Exception as exc:
            logger.warning("lane_b_sweep failed: %s", exc)


__all__ = [
    "LANE_B_QUIESCENCE_S",
    "LANE_B_SWEEP_INTERVAL_S",
    "REGISTRATION_GAPS",
    "SweepResult",
    "dirty_paths",
    "lane_b_sweeper_loop",
    "select_sweep_paths",
    "sweep_lane_b_writes",
]
