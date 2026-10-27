#!/usr/bin/env python3
"""Gated dry-run-first reclaim of unlanded-tip Lane-B worktree directories.

Implements todo:giw-worktree-reaper-step3 Phase A (RULING 20888#11 R1–R5).
Default mode prints one line per candidate and writes a JSONL ledger; ``--execute``
may remove dirs after gates pass (never deletes ``cursor-sdk/lane-N`` branches).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.git_integration_worker.config import load_config  # noqa: E402
from services.git_integration_worker.cursor_sdk_conductor_park_gate import (  # noqa: E402
    open_restart_park_sql,
)
from services.git_integration_worker.cursor_sdk_land_dirty import (  # noqa: E402
    _parse_worktree_blocks,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (  # noqa: E402
    _LIVE_LEDGER_STATUSES,
    ledger_connection,
)

_GIT_TIMEOUT_S = 60.0
_PROTECTED_THREAD_IDS = frozenset({"20793", "11767"})
_AGE_EXCLUSION_S = 48 * 3600
_TRACKED_LEDGER_TEMPLATE_REL = Path("tasks/specs/giw-worktree-reclaim-ledger.jsonl")
_RUNTIME_LEDGER_REL = Path("tmp/giw/giw-worktree-reclaim-ledger.jsonl")
_WIP_COMMIT_PREFIX = "wip: pre-reclaim snapshot"
_WIP_REF_PREFIX = "refs/giw-wip/"
_RECLAIM_MISMATCH_HINT = (
    "pass --execute --reclaim-mismatch to reclaim branch-mismatch lanes"
)


class LedgerUnavailableError(Exception):
    """Ledger could not be read; every lane must be skipped fail-closed."""


@dataclass(frozen=True, slots=True)
class LedgerRow:
    record_type: str  # lane | orphan_lock_reconciliation
    lane: str | None = None
    dir_mtime: str | None = None
    branch_ref: str | None = None
    porcelain_empty: bool | None = None
    wip_commit_sha_or_none: str | None = None
    wip_ref: str | None = None
    detached_head_or_none: str | None = None
    disposition: str | None = None  # removed | skipped
    reason: str | None = None
    reclaim_hint: str | None = None
    orphan_lock_string: str | None = None
    locked_worktree_count: int | None = None
    lane_dir_count: int | None = None


def _git(
    *args: str,
    cwd: Path | str,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=check,
    )


def _lane_thread_id(lane_name: str) -> str | None:
    if not lane_name.startswith("lane-"):
        return None
    return lane_name.removeprefix("lane-")


def _repo_lane_root(source_repo: Path, worktree_root: Path) -> Path:
    return worktree_root / source_repo.name


def _list_lane_dirs(lane_root: Path) -> list[tuple[str, Path, float]]:
    """Return ``(lane_name, path, mtime)`` sorted by mtime ascending (R1 order only)."""
    found: list[tuple[str, Path, float]] = []
    if not lane_root.is_dir():
        return found
    for child in lane_root.iterdir():
        if not child.is_dir() or not child.name.startswith("lane-"):
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            mtime = 0.0
        found.append((child.name, child, mtime))
    found.sort(key=lambda item: item[2])
    return found


def _parse_locked_blocks(porcelain: str) -> list[tuple[str, str | None]]:
    """Return ``(worktree_path, lock_reason)`` for every locked block."""
    blocks: list[tuple[str, str | None]] = []
    path = ""
    lock_reason: str | None = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if path and lock_reason is not None:
                blocks.append((path, lock_reason))
            path = line[len("worktree ") :].strip()
            lock_reason = None
        elif line.startswith("locked "):
            lock_reason = line[len("locked ") :].strip()
    if path and lock_reason is not None:
        blocks.append((path, lock_reason))
    return blocks


def _orphan_lock_rows(
    *,
    source_repo: Path,
    lane_root: Path,
) -> tuple[list[LedgerRow], list[str]]:
    proc = _git("worktree", "list", "--porcelain", cwd=source_repo)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git worktree list failed: {proc.stderr.strip() or proc.returncode}"
        )
    locked_blocks = _parse_locked_blocks(proc.stdout)
    lane_dir_paths = {
        str(path.resolve()) for _name, path, _mtime in _list_lane_dirs(lane_root)
    }
    orphan_strings: list[str] = []
    for wt_path, lock_str in locked_blocks:
        resolved = str(Path(wt_path).resolve())
        if resolved in lane_dir_paths and Path(wt_path).is_dir():
            continue
        base = Path(wt_path).name
        if base.startswith("lane-"):
            orphan_strings.append(lock_str)
        elif not Path(wt_path).is_dir():
            orphan_strings.append(lock_str)
    rows: list[LedgerRow] = []
    if orphan_strings:
        for lock_str in orphan_strings:
            rows.append(
                LedgerRow(
                    record_type="orphan_lock_reconciliation",
                    orphan_lock_string=lock_str,
                    locked_worktree_count=len(locked_blocks),
                    lane_dir_count=len(lane_dir_paths),
                    disposition="skipped",
                    reason="orphan_lock_no_matching_dir",
                )
            )
    else:
        rows.append(
            LedgerRow(
                record_type="orphan_lock_reconciliation",
                orphan_lock_string=None,
                locked_worktree_count=len(locked_blocks),
                lane_dir_count=len(lane_dir_paths),
                disposition="skipped",
                reason="no_orphan_lock",
            )
        )
    return rows, orphan_strings


def _report_ledger_unavailable(exc: Exception) -> None:
    print(f"ledger_unavailable: {exc.__class__.__name__}", file=sys.stderr)


def _live_dispatch_thread_ids() -> set[str]:
    placeholders = ", ".join("?" for _ in _LIVE_LEDGER_STATUSES)
    open_restart = open_restart_park_sql("d")
    now = datetime.now(UTC).isoformat()
    ids: set[str] = set()
    try:
        with ledger_connection() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT d.thread_id FROM cursor_sdk_dispatches d "
                f"WHERE d.status IN ({placeholders}) OR {open_restart}",
                (*_LIVE_LEDGER_STATUSES, now),
            ).fetchall()
    except Exception as exc:
        _report_ledger_unavailable(exc)
        raise LedgerUnavailableError from exc
    for row in rows:
        tid = row["thread_id"]
        if tid:
            ids.add(str(tid))
    return ids


def _non_terminal_dispatch_for_thread(
    thread_id: str,
) -> tuple[str, str] | None | Literal["ledger_unavailable"]:
    """Return blocking dispatch, ``None`` when clear, or ``ledger_unavailable``."""
    placeholders = ", ".join("?" for _ in _LIVE_LEDGER_STATUSES)
    open_restart = open_restart_park_sql("d")
    now = datetime.now(UTC).isoformat()
    try:
        with ledger_connection() as conn:
            row = conn.execute(
                f"SELECT d.dispatch_id, d.status FROM cursor_sdk_dispatches d "
                f"WHERE d.thread_id=? AND (d.status IN ({placeholders}) OR {open_restart}) "
                f"ORDER BY d.admitted_at DESC LIMIT 1",
                (thread_id, *_LIVE_LEDGER_STATUSES, now),
            ).fetchone()
    except Exception as exc:
        _report_ledger_unavailable(exc)
        return "ledger_unavailable"
    if row is None:
        return None
    return str(row["dispatch_id"]), str(row["status"])


def _porcelain_empty(lane_dir: Path) -> bool:
    proc = _git("status", "--porcelain", cwd=lane_dir)
    return proc.returncode == 0 and not proc.stdout.strip()


def _head_sha(lane_dir: Path) -> str | None:
    proc = _git("rev-parse", "HEAD", cwd=lane_dir)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _branch_ref(lane_dir: Path) -> tuple[str | None, str | None]:
    """Return ``(branch_ref, detached_head_sha)``."""
    proc = _git("symbolic-ref", "-q", "HEAD", cwd=lane_dir)
    if proc.returncode == 0:
        ref = proc.stdout.strip()
        return ref, None
    head = _head_sha(lane_dir)
    return None, head


def _branch_contains_head(*, lane_dir: Path, thread_id: str) -> bool:
    branch = f"refs/heads/cursor-sdk/lane-{thread_id}"
    head = _head_sha(lane_dir)
    if not head:
        return False
    proc = _git("merge-base", "--is-ancestor", head, branch, cwd=lane_dir)
    return proc.returncode == 0


def _worktree_branch_from_list(source_repo: Path, lane_dir: Path) -> str | None:
    proc = _git("worktree", "list", "--porcelain", cwd=source_repo)
    if proc.returncode != 0:
        return None
    target = str(lane_dir.resolve())
    for wt_path, branch_ref in _parse_worktree_blocks(proc.stdout):
        if str(Path(wt_path).resolve()) == target:
            return branch_ref or None
    return None


def _wip_ref_name(lane_name: str) -> str:
    return f"{_WIP_REF_PREFIX}{lane_name}"


def _commit_wip_snapshot(
    *,
    source_repo: Path,
    lane_dir: Path,
    lane_name: str,
) -> tuple[str | None, str | None]:
    """Commit dirt, pin sha under ``refs/giw-wip/<lane>``; return ``(sha, ref)``."""
    _git("add", "-A", cwd=lane_dir)
    msg = f"{_WIP_COMMIT_PREFIX} {lane_name}"
    proc = _git("commit", "-m", msg, cwd=lane_dir)
    if proc.returncode != 0:
        return None, None
    sha = _head_sha(lane_dir)
    if not sha:
        return None, None
    ref = _wip_ref_name(lane_name)
    update = _git("update-ref", ref, sha, cwd=source_repo)
    if update.returncode != 0:
        return sha, None
    return sha, ref


def _remove_worktree(*, source_repo: Path, lane_dir: Path) -> bool:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(source_repo),
            "worktree",
            "remove",
            "--force",
            "--force",
            str(lane_dir),
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    return proc.returncode == 0


def _lane_row(
    *,
    lane_name: str,
    mtime_iso: str,
    branch_ref: str | None,
    porcelain_empty: bool,
    detached_head: str | None,
    disposition: str,
    reason: str,
    wip_sha: str | None = None,
    wip_ref: str | None = None,
    reclaim_hint: str | None = None,
) -> LedgerRow:
    return LedgerRow(
        record_type="lane",
        lane=lane_name,
        dir_mtime=mtime_iso,
        branch_ref=branch_ref,
        porcelain_empty=porcelain_empty,
        wip_commit_sha_or_none=wip_sha,
        wip_ref=wip_ref,
        detached_head_or_none=detached_head,
        disposition=disposition,
        reason=reason,
        reclaim_hint=reclaim_hint,
    )


def _evaluate_lane(
    *,
    lane_name: str,
    lane_dir: Path,
    mtime: float,
    source_repo: Path,
    live_threads: set[str],
    execute: bool,
    reclaim_mismatch: bool,
    ledger_unavailable: bool,
) -> tuple[LedgerRow, str]:
    thread_id = _lane_thread_id(lane_name) or ""
    mtime_iso = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    branch_ref, detached_head = _branch_ref(lane_dir)
    porcelain_empty = _porcelain_empty(lane_dir)
    if branch_ref is None and detached_head is None:
        branch_ref = _worktree_branch_from_list(source_repo, lane_dir)

    if thread_id in _PROTECTED_THREAD_IDS:
        skip_reason = f"excluded_protected_lane-{thread_id}"
        line = f"{lane_name} skipped {skip_reason}"
        return (
            _lane_row(
                lane_name=lane_name,
                mtime_iso=mtime_iso,
                branch_ref=branch_ref,
                porcelain_empty=porcelain_empty,
                detached_head=detached_head,
                disposition="skipped",
                reason=skip_reason,
            ),
            line,
        )

    if ledger_unavailable:
        skip_reason = "ledger_unavailable"
        line = f"{lane_name} skipped {skip_reason}"
        return (
            _lane_row(
                lane_name=lane_name,
                mtime_iso=mtime_iso,
                branch_ref=branch_ref,
                porcelain_empty=porcelain_empty,
                detached_head=detached_head,
                disposition="skipped",
                reason=skip_reason,
            ),
            line,
        )

    skip_reason: str | None = None
    reclaim_hint: str | None = None
    branch_mismatch = not _branch_contains_head(lane_dir=lane_dir, thread_id=thread_id)

    if thread_id in live_threads:
        skip_reason = f"excluded_live_dispatch thread={thread_id}"
    elif (datetime.now(tz=UTC).timestamp() - mtime) < _AGE_EXCLUSION_S:
        skip_reason = "excluded_dir_mtime_under_48h"
    else:
        blocking = _non_terminal_dispatch_for_thread(thread_id)
        if blocking == "ledger_unavailable":
            skip_reason = "ledger_unavailable"
        elif blocking is not None:
            dispatch_id, status = blocking
            skip_reason = (
                f"ledger_non_terminal dispatch_id={dispatch_id} status={status}"
            )
        elif branch_mismatch and not (reclaim_mismatch and execute):
            skip_reason = "branch_ref_does_not_contain_head"
            reclaim_hint = _RECLAIM_MISMATCH_HINT

    if skip_reason:
        line = f"{lane_name} skipped {skip_reason}"
        return (
            _lane_row(
                lane_name=lane_name,
                mtime_iso=mtime_iso,
                branch_ref=branch_ref,
                porcelain_empty=porcelain_empty,
                detached_head=detached_head,
                disposition="skipped",
                reason=skip_reason,
                reclaim_hint=reclaim_hint,
            ),
            line,
        )

    wip_sha: str | None = None
    wip_ref: str | None = None
    if not porcelain_empty:
        if execute:
            wip_sha, wip_ref = _commit_wip_snapshot(
                source_repo=source_repo,
                lane_dir=lane_dir,
                lane_name=lane_name,
            )
            if wip_sha is None:
                skip_reason = "wip_commit_failed"
                line = f"{lane_name} skipped {skip_reason}"
                return (
                    _lane_row(
                        lane_name=lane_name,
                        mtime_iso=mtime_iso,
                        branch_ref=branch_ref,
                        porcelain_empty=False,
                        detached_head=detached_head,
                        disposition="skipped",
                        reason=skip_reason,
                    ),
                    line,
                )
            if wip_ref is None:
                skip_reason = "wip_ref_create_failed"
                line = f"{lane_name} skipped {skip_reason}"
                return (
                    _lane_row(
                        lane_name=lane_name,
                        mtime_iso=mtime_iso,
                        branch_ref=branch_ref,
                        porcelain_empty=False,
                        detached_head=detached_head,
                        disposition="skipped",
                        reason=skip_reason,
                        wip_sha=wip_sha,
                    ),
                    line,
                )
        else:
            hint = reclaim_hint if branch_mismatch else None
            line = f"{lane_name} would_reclaim dirty=1 porcelain_empty=0"
            return (
                _lane_row(
                    lane_name=lane_name,
                    mtime_iso=mtime_iso,
                    branch_ref=branch_ref,
                    porcelain_empty=False,
                    detached_head=detached_head,
                    disposition="skipped",
                    reason="dry_run_dirty_would_wip_then_remove",
                    reclaim_hint=hint,
                ),
                line,
            )

    if not execute:
        hint = reclaim_hint if branch_mismatch else None
        line = f"{lane_name} would_reclaim gates_pass porcelain_empty={int(porcelain_empty)}"
        return (
            _lane_row(
                lane_name=lane_name,
                mtime_iso=mtime_iso,
                branch_ref=branch_ref,
                porcelain_empty=porcelain_empty,
                detached_head=detached_head,
                disposition="skipped",
                reason="dry_run_gates_pass",
                reclaim_hint=hint,
            ),
            line,
        )

    if not _remove_worktree(source_repo=source_repo, lane_dir=lane_dir):
        skip_reason = "worktree_remove_failed"
        line = f"{lane_name} skipped {skip_reason}"
        return (
            _lane_row(
                lane_name=lane_name,
                mtime_iso=mtime_iso,
                branch_ref=branch_ref,
                porcelain_empty=porcelain_empty,
                detached_head=detached_head,
                disposition="skipped",
                reason=skip_reason,
                wip_sha=wip_sha,
                wip_ref=wip_ref,
            ),
            line,
        )

    line = f"{lane_name} removed gates_pass"
    return (
        _lane_row(
            lane_name=lane_name,
            mtime_iso=mtime_iso,
            branch_ref=branch_ref,
            porcelain_empty=porcelain_empty,
            detached_head=detached_head,
            disposition="removed",
            reason="gates_pass",
            wip_sha=wip_sha,
            wip_ref=wip_ref,
        ),
        line,
    )


def _synthetic_skip_row(lane_name: str, reason: str) -> LedgerRow:
    return LedgerRow(
        record_type="lane",
        lane=lane_name,
        dir_mtime=None,
        branch_ref=None,
        porcelain_empty=None,
        wip_commit_sha_or_none=None,
        wip_ref=None,
        detached_head_or_none=None,
        disposition="skipped",
        reason=reason,
    )


def run_reclaim(
    *,
    source_repo: Path,
    worktree_root: Path,
    ledger_path: Path,
    execute: bool,
    reclaim_mismatch: bool = False,
) -> list[LedgerRow]:
    lane_root = _repo_lane_root(source_repo, worktree_root)
    rows, _orphans = _orphan_lock_rows(source_repo=source_repo, lane_root=lane_root)
    ledger_unavailable = False
    try:
        live_threads = _live_dispatch_thread_ids()
    except LedgerUnavailableError:
        ledger_unavailable = True
        live_threads = set()
    seen_lanes: set[str] = set()

    for lane_name, lane_dir, mtime in _list_lane_dirs(lane_root):
        seen_lanes.add(lane_name)
        row, line = _evaluate_lane(
            lane_name=lane_name,
            lane_dir=lane_dir,
            mtime=mtime,
            source_repo=source_repo,
            live_threads=live_threads,
            execute=execute,
            reclaim_mismatch=reclaim_mismatch,
            ledger_unavailable=ledger_unavailable,
        )
        rows.append(row)
        print(line)

    if not ledger_unavailable:
        for tid in sorted(_PROTECTED_THREAD_IDS):
            lane_name = f"lane-{tid}"
            if lane_name in seen_lanes:
                continue
            reason = f"excluded_protected_lane-{tid}"
            rows.append(_synthetic_skip_row(lane_name, reason))
            print(f"{lane_name} skipped {reason}")

        for tid in sorted(live_threads):
            lane_name = f"lane-{tid}"
            if lane_name in seen_lanes or tid in _PROTECTED_THREAD_IDS:
                continue
            reason = f"excluded_live_dispatch thread={tid}"
            rows.append(_synthetic_skip_row(lane_name, reason))
            print(f"{lane_name} skipped {reason}")

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), sort_keys=True) + "\n")
    return rows


def _default_ledger_path(source_repo: Path) -> Path:
    override = os.environ.get("GIW_WORKTREE_RECLAIM_LEDGER", "").strip()
    if override:
        path = Path(override)
        return path if path.is_absolute() else source_repo / path
    runtime_dir = os.environ.get("GIT_INTEGRATION_RUNTIME_DIR", "").strip()
    if runtime_dir:
        return Path(runtime_dir).expanduser() / "giw-worktree-reclaim-ledger.jsonl"
    return source_repo / _RUNTIME_LEDGER_REL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GIW unlanded-tip worktree reclaim")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply removals after gates pass (default is dry-run only)",
    )
    parser.add_argument(
        "--reclaim-mismatch",
        action="store_true",
        help="With --execute, reclaim clean branch-mismatch lanes (R5)",
    )
    args = parser.parse_args(argv)

    cfg = load_config()
    source_repo = Path(
        os.environ.get("GIT_INTEGRATION_SOURCE_REPO", str(cfg.source_repo))
    ).resolve()
    worktree_root = Path(
        os.environ.get("GIT_INTEGRATION_WORKTREE_ROOT", str(cfg.worktree_root))
    ).resolve()
    ledger_path = _default_ledger_path(source_repo)

    try:
        run_reclaim(
            source_repo=source_repo,
            worktree_root=worktree_root,
            ledger_path=ledger_path,
            execute=args.execute,
            reclaim_mismatch=args.reclaim_mismatch,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
