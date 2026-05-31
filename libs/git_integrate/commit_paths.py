"""Path-scoped commit mechanics for non-arc working-tree commits.

Unlike ``git_cas.commit_arc`` (which stages the whole tree via ``git add -A``),
these primitives operate on an EXPLICIT path set only — they never capture
concurrent unrelated edits in a shared working tree (the thread-1147 footgun).
Both the fingerprint and the commit restrict to the named paths.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile

from universal_logging import get_logger

from git_integrate.git_cas import _run_command, current_sha
from git_integrate.schema import (
    RC_COMMIT_FAILED,
    RC_NO_CHANGES_FOR_PATHS,
    CommitResult,
)

_GIT_TIMEOUT = 30.0
_logger = get_logger(__name__)


def _scratch_stage_paths(worktree_path: str, paths: list[str], env: dict) -> bool:
    """read-tree HEAD then stage only ``paths`` into the scratch index."""
    read_tree = subprocess.run(
        ["git", "-C", worktree_path, "read-tree", "HEAD"],
        env=env,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if read_tree.returncode != 0:
        return False
    add = subprocess.run(
        ["git", "-C", worktree_path, "add", "--", *paths],
        env=env,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    return add.returncode == 0


def commit_paths_fingerprint(worktree_path: str, paths: list[str]) -> str:
    """SHA-256 of the staged diff for exactly ``paths`` vs HEAD.

    Uses a scratch index (``GIT_INDEX_FILE``) so the real index is never
    mutated. Captures tracked modifications and new files for the named paths;
    ignores every other dirty path in the working tree. Returns "" on failure.
    """
    if not paths:
        return ""
    index_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            index_path = tmp.name
        env = {**os.environ, "GIT_INDEX_FILE": index_path}
        if not _scratch_stage_paths(worktree_path, paths, env):
            return ""
        diff = subprocess.run(
            ["git", "-C", worktree_path, "diff", "--cached", "HEAD", "--", *paths],
            env=env,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if diff.returncode != 0:
            return ""
        return hashlib.sha256(diff.stdout.encode()).hexdigest()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    finally:
        if index_path:
            try:
                os.unlink(index_path)
            except OSError:
                pass


def commit_paths_numstat(worktree_path: str, paths: list[str]) -> str:
    """``git diff --numstat`` for ``paths`` vs HEAD (scratch index, read-only)."""
    if not paths:
        return ""
    index_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            index_path = tmp.name
        env = {**os.environ, "GIT_INDEX_FILE": index_path}
        if not _scratch_stage_paths(worktree_path, paths, env):
            return ""
        diff = subprocess.run(
            [
                "git",
                "-C",
                worktree_path,
                "diff",
                "--cached",
                "--numstat",
                "HEAD",
                "--",
                *paths,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        return diff.stdout if diff.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""
    finally:
        if index_path:
            try:
                os.unlink(index_path)
            except OSError:
                pass


async def commit_paths(
    worktree_path: str, paths: list[str], message: str
) -> CommitResult:
    """Stage and commit ONLY the named paths on the current branch.

    Real-index ``git add -- <paths>`` then ``git commit -- <paths>``. The
    pathspec on commit isolates the commit to the named paths regardless of
    what else is staged or dirty in the shared working tree.
    """
    add_proc = await _run_command(
        ["git", "-C", worktree_path, "add", "--", *paths], timeout=_GIT_TIMEOUT
    )
    if add_proc.returncode != 0:
        return CommitResult(committed=False, reason_code=RC_COMMIT_FAILED)

    commit_proc = await _run_command(
        ["git", "-C", worktree_path, "commit", "-m", message, "--", *paths],
        timeout=_GIT_TIMEOUT,
    )
    if commit_proc.returncode != 0:
        combined = commit_proc.stdout + commit_proc.stderr
        if "nothing to commit" in combined or "no changes added" in combined:
            return CommitResult(committed=False, reason_code=RC_NO_CHANGES_FOR_PATHS)
        return CommitResult(committed=False, reason_code=RC_COMMIT_FAILED)

    sha = await current_sha(worktree_path, "HEAD")
    return CommitResult(committed=True, commit_sha=sha)
