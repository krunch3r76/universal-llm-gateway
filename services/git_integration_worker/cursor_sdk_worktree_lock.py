"""Git worktree lock helpers for Lane-B pin enforcement (S1 Leg A).

Admit calls ``lock_lane_worktree`` after resolving ``source_repo``. A satellite
tree locked with hub ULG as ``-C`` used to fail as git's ``is not a working
tree``; the helper now refuses that mismatch by comparing ``git-common-dir``.
"""

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


class SourceRepoMismatchError(RuntimeError):
    """``source_repo`` is not the git that owns ``worktree_path``.

    Raised before ``git worktree lock`` so a hub-vs-satellite pin surfaces as
    ``CURSOR_WORKTREE_SOURCE_REPO_MISMATCH`` instead of git's
    ``is not a working tree``. The code lives on ``.code`` and as the
    message prefix. Admit currently folds this into retryable 503
    ``CURSOR_LANE_PIN_FAILED``.
    """

    code = "CURSOR_WORKTREE_SOURCE_REPO_MISMATCH"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


@dataclass(frozen=True, slots=True)
class LockLaneResult:
    """Outcome of ``lock_lane_worktree`` — ``inherited`` skips registry supersede."""

    lock_reason: str
    inherited: bool = False


def _format_lock_reason(*, dispatch_id: str, thread_id: str) -> str:
    pinned_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"ulg:dispatch={dispatch_id};thread={thread_id};pinned_at={pinned_at}"


def parse_lock_reason(reason: str | None) -> ParsedLockReason | None:
    """Parse a ULG lock reason, or return ``None`` when the string is foreign.

    Only ``ulg:dispatch=…;thread=…;pinned_at=…`` matches. Callers treat
    ``None`` as a lock this service must not override (``ForeignLockError``).
    """
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


def _git_common_dir(cwd: Path) -> Path | None:
    """Return the resolved ``--git-common-dir`` for ``cwd``, or ``None`` on failure.

    Relative git output is resolved against ``cwd`` because ``git -C`` prints
    common-dir relative to that tree. Two checkouts of the same repo share
    this path; a satellite worktree and hub ULG do not.
    """
    proc = subprocess.run(
        ["git", "-C", str(cwd.resolve()), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = cwd.resolve() / path
    return path.resolve()


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
    inherit_lane_thread_id: str | None = None,
) -> LockLaneResult:
    """Lock a lane worktree with the ULG reason grammar (idempotent per thread).

    When ``inherit_lane_thread_id`` is set and the worktree is already locked for
    that lane thread, return the existing reason without re-locking onto
    ``thread_id`` (CSE nest on the holder's Lane-B tree).

    Compares ``git-common-dir`` of ``worktree_path`` against ``source_repo``
    before lock so a mismatched caller gets ``SourceRepoMismatchError`` rather
    than git's ``is not a working tree``. Probe failure falls through to lock.
    """
    repo = source_repo.resolve()
    wt = worktree_path.resolve()
    wt_common = _git_common_dir(wt)
    repo_common = _git_common_dir(repo)
    if wt_common is not None and repo_common is not None and wt_common != repo_common:
        raise SourceRepoMismatchError(
            f"{SourceRepoMismatchError.code}: source_repo {repo} does not own "
            f"worktree {wt} (git-common-dir {wt_common} != {repo_common})"
        )
    reason = _current_lock_reason(repo, wt)
    if reason is not None:
        parsed = parse_lock_reason(reason)
        if parsed is None:
            raise ForeignLockError(f"foreign lock on {wt}: {reason!r}")
        if (
            inherit_lane_thread_id is not None
            and parsed.thread_id == inherit_lane_thread_id
            and thread_id != inherit_lane_thread_id
        ):
            return LockLaneResult(lock_reason=reason, inherited=True)
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
        raise RuntimeError(proc.stderr.strip() or f"git worktree lock failed for {wt}")
    return LockLaneResult(lock_reason=lock_reason, inherited=False)


__all__ = [
    "ForeignLockError",
    "LockLaneResult",
    "LockedWorktree",
    "ParsedLockReason",
    "SourceRepoMismatchError",
    "list_locked_worktrees",
    "lock_lane_worktree",
    "parse_lock_reason",
    "unlock_lane_worktree",
]
