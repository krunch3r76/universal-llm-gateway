"""Archive a branch tip as an ``archive/*`` tag before any delete.

No branch deletion in this service is allowed to be the last copy of anything.
Tagging the tip first costs one ref and makes every reap recoverable, which is
what licenses deleting at all: the question at a delete site stops being "might
this hold unique work" and becomes "is it still owed".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_sdk_lane_b_archived

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
ARCHIVE_TAG_PREFIX = "archive/"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )


def archive_tag_name(branch_name: str, tip_sha: str) -> str:
    """Compose the deterministic archive tag for a branch tip."""
    return f"{ARCHIVE_TAG_PREFIX}{branch_name}-{tip_sha[:8]}"


def archive_branch(*, repo: Path, branch_name: str) -> str | None:
    """Tag *branch_name*'s tip and return the tag, or ``None`` on failure.

    Idempotent: an existing tag on the same tip is reused rather than re-created,
    so repeated sweeps over a retained branch stay quiet.
    """
    root = repo.resolve()
    rev = _git(root, "rev-parse", "--verify", f"{branch_name}^{{commit}}")
    if rev.returncode != 0:
        logger.warning(
            "branch archive skipped — tip unresolved branch=%s err=%s",
            branch_name,
            rev.stderr.strip(),
        )
        return None
    tip_sha = rev.stdout.strip()
    tag = archive_tag_name(branch_name, tip_sha)

    existing = _git(root, "rev-parse", "--verify", f"refs/tags/{tag}")
    if existing.returncode == 0:
        return tag

    created = _git(root, "tag", tag, tip_sha)
    if created.returncode != 0:
        logger.error(
            "branch archive FAILED branch=%s tag=%s err=%s",
            branch_name,
            tag,
            created.stderr.strip(),
        )
        return None
    emit_sdk_lane_b_archived(branch=branch_name, tag=tag, tip_sha=tip_sha)
    return tag


def branch_checked_out_at(*, repo: Path, branch_name: str) -> str | None:
    """Path of the worktree holding *branch_name*, when one does.

    ``git branch -D`` refuses a checked-out branch, so a delete site needs this
    to report the pin rather than surface an opaque git failure.
    """
    proc = _git(repo.resolve(), "worktree", "list", "--porcelain")
    if proc.returncode != 0:
        return None
    current: str | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            current = line[len("worktree ") :].strip()
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            if ref == f"refs/heads/{branch_name}":
                return current
    return None
