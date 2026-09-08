"""Git worktree lock helpers for Lane-B pin enforcement (S1 Leg A)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_GIT_TIMEOUT_S = 60.0
_LOCK_REASON_PREFIX = "ulg:"
_LOCK_GRAMMAR_RE = re.compile(
    r"^ulg:dispatch=(?P<dispatch>[^;]+);thread=(?P<thread>[^;]+);pinned_at=(?P<pinned>.+)$"
)


class ForeignLockError(RuntimeError):
    """A worktree carries a lock reason this service must not override."""


@dataclass(frozen=True, slots=True)
class ParsedLockReason:
    dispatch_id: str
    thread_id: str
    pinned_at: str


@dataclass(frozen=True, slots=True)
class LockedWorktree:
    path: Path
    exists: bool
    reason: str | None
    parsed: ParsedLockReason | None


def _format_lock_reason(*, dispatch_id: str, thread_id: str) -> str:
    pinned_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"ulg:dispatch={dispatch_id};thread={thread_id};pinned_at={pinned_at}"


def parse_lock_reason(reason: str | None) -> ParsedLockReason | None:
    if not reason or not reason.startswith(_LOCK_REASON_PREFIX):
        return None
    match = _LOCK_GRAMMAR_RE.match(reason.strip())
    if match is None:
        return None
    return ParsedLockReason(
        dispatch_id=match.group("dispatch"),
        thread_id=match.group("thread"),
        pinned_at=match.group("pinned"),
    )


def list_locked_worktrees(source_repo: Path) -> list[LockedWorktree]:
    """Return locked worktrees from ``git worktree list --porcelain``."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(source_repo.resolve()),
            "worktree",
            "list",
            "--porcelain",
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return []
    out: list[LockedWorktree] = []
    path: Path | None = None
    locked_reason: str | None = None

    def flush() -> None:
        nonlocal path, locked_reason
        if path is not None and locked_reason is not None:
            out.append(
                LockedWorktree(
                    path=path,
                    exists=path.is_dir(),
                    reason=locked_reason,
                    parsed=parse_lock_reason(locked_reason),
                )
            )
        path, locked_reason = None, None

    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            flush()
            path = Path(line[len("worktree ") :].strip()).resolve()
        elif line.startswith("locked "):
            locked_reason = line[len("locked ") :].strip()
    flush()
    return out


def _current_lock_reason(source_repo: Path, worktree_path: Path) -> str | None:
    target = worktree_path.resolve()
    for entry in list_locked_worktrees(source_repo):
        if entry.path == target:
            return entry.reason
    return None


def unlock_lane_worktree(
    source_repo: Path,
    worktree_path: Path,
    *,
    thread_id: str,
) -> bool:
    """Unlock when the lock reason parses as ULG for ``thread_id``."""
    reason = _current_lock_reason(source_repo, worktree_path)
    if reason is None:
        return True
    parsed = parse_lock_reason(reason)
    if parsed is None:
        return False
    if parsed.thread_id != thread_id:
        return False
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(source_repo.resolve()),
            "worktree",
            "unlock",
            str(worktree_path.resolve()),
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    return proc.returncode == 0


def lock_lane_worktree(
    source_repo: Path,
    worktree_path: Path,
    *,
    dispatch_id: str,
    thread_id: str,
) -> str:
    """Lock a lane worktree with the ULG reason grammar (idempotent per thread)."""
    repo = source_repo.resolve()
    wt = worktree_path.resolve()
    reason = _current_lock_reason(repo, wt)
    if reason is not None:
        parsed = parse_lock_reason(reason)
        if parsed is None:
            raise ForeignLockError(f"foreign lock on {wt}: {reason!r}")
        if parsed.thread_id == thread_id:
            unlock_lane_worktree(repo, wt, thread_id=thread_id)
        else:
            raise ForeignLockError(
                f"lock held by thread {parsed.thread_id!r}, not {thread_id!r}"
            )
    lock_reason = _format_lock_reason(dispatch_id=dispatch_id, thread_id=thread_id)
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "lock",
            "--reason",
            lock_reason,
            str(wt),
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or f"git worktree lock failed for {wt}"
        )
    return lock_reason


__all__ = [
    "ForeignLockError",
    "LockedWorktree",
    "ParsedLockReason",
    "list_locked_worktrees",
    "lock_lane_worktree",
    "parse_lock_reason",
    "unlock_lane_worktree",
]
