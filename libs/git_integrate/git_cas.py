"""Pure git mechanics for arc-worktree integration.

All async functions use _run_command (SIGTERM→SIGKILL timeout pattern).
diff_sha256 is synchronous (called from
validate_integrate which runs in run_in_executor).

Ref-advance shape chosen: direct CAS on refs/heads/master via
``git update-ref refs/heads/master <new> <old>``. The worker layer must
ensure master is NOT checked out as HEAD of any linked worktree it
controls (Git ≥2.35 worktree HEAD protection). The live developer
checkout pulls --ff-only on its own cadence; integration never writes
to the live working tree.

If the worker cannot guarantee the above, the worker layer should
switch to advancing refs/integration/master (A″ shape) instead —
the lib CAS call site is a single-line change in that case.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import subprocess
import tempfile

from universal_logging import get_logger

from git_integrate.schema import (
    RC_CLEAN_TREE,
    RC_COMMIT_FAILED,
    CasResult,
    CommitResult,
    MergeResult,
)

_GIT_TIMEOUT = 30.0
_logger = get_logger(__name__)


def _merge_base(worktree_path: str) -> str:
    """Return merge-base(HEAD, refs/heads/master) or "" on failure."""
    try:
        mb = subprocess.run(
            ["git", "-C", worktree_path, "merge-base", "HEAD", "refs/heads/master"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if mb.returncode != 0:
            return ""
        return mb.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def is_dirty(worktree_path: str) -> bool:
    """True when the worktree has uncommitted changes (incl. untracked)."""
    try:
        proc = subprocess.run(
            ["git", "-C", worktree_path, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        return bool(proc.stdout.strip()) if proc.returncode == 0 else False
    except (OSError, subprocess.TimeoutExpired):
        return False


def _scratch_index_diff(
    worktree_path: str,
    merge_base: str,
    *,
    cached: bool,
    path_filter: str = "",
    numstat: bool = False,
) -> str:
    """Compute diff via a temporary index — does not mutate the real index.

    ``numstat=True`` swaps the unified body for ``--numstat`` machine output
    (per-file insertions/deletions) so callers can summarize the change set
    without paying the full-hunk cost.
    """
    index_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            index_path = tmp.name
        env = {**os.environ, "GIT_INDEX_FILE": index_path}
        read_tree = subprocess.run(
            ["git", "-C", worktree_path, "read-tree", "HEAD"],
            env=env,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if read_tree.returncode != 0:
            return ""
        add_all = subprocess.run(
            ["git", "-C", worktree_path, "add", "-A"],
            env=env,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if add_all.returncode != 0:
            return ""
        diff_cmd = ["git", "-C", worktree_path, "diff"]
        if cached:
            diff_cmd.append("--cached")
        if numstat:
            diff_cmd.append("--numstat")
        diff_cmd.append(merge_base)
        if path_filter:
            diff_cmd.extend(["--", path_filter])
        diff = subprocess.run(
            diff_cmd,
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


def land_fingerprint(worktree_path: str) -> str:
    """SHA-256 of the diff that would be committed and integrated."""
    if not is_dirty(worktree_path):
        return diff_sha256(worktree_path)
    merge_base = _merge_base(worktree_path)
    if not merge_base:
        return ""
    diff_text = _scratch_index_diff(worktree_path, merge_base, cached=True)
    return hashlib.sha256(diff_text.encode()).hexdigest()


def land_diff_text(worktree_path: str, path_filter: str = "") -> str:
    """Unified diff of what would land (dirty-aware, read-only)."""
    merge_base = _merge_base(worktree_path)
    if not merge_base:
        return ""
    if is_dirty(worktree_path):
        return _scratch_index_diff(
            worktree_path, merge_base, cached=True, path_filter=path_filter
        )
    diff_cmd = ["git", "-C", worktree_path, "diff", merge_base, "HEAD"]
    if path_filter:
        diff_cmd.extend(["--", path_filter])
    try:
        diff = subprocess.run(
            diff_cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        return diff.stdout if diff.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def land_diff_numstat(worktree_path: str) -> str:
    """``--numstat`` of what would land (dirty-aware, read-only).

    Describes the full arc-vs-master change set — the same set the
    ``diff_sha256`` fingerprint covers — so the summary stays authoritative.
    No path_filter: a stat is a whole-change-set fingerprint, not a view.
    """
    merge_base = _merge_base(worktree_path)
    if not merge_base:
        return ""
    if is_dirty(worktree_path):
        return _scratch_index_diff(worktree_path, merge_base, cached=True, numstat=True)
    try:
        diff = subprocess.run(
            ["git", "-C", worktree_path, "diff", "--numstat", merge_base, "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        return diff.stdout if diff.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


async def commit_arc(worktree_path: str, message: str) -> CommitResult:
    """Deterministically commit all staged/unstaged changes in the arc worktree."""
    add_proc = await _run_command(
        ["git", "-C", worktree_path, "add", "-A"],
        timeout=_GIT_TIMEOUT,
    )
    if add_proc.returncode != 0:
        return CommitResult(
            committed=False,
            reason_code=RC_COMMIT_FAILED,
        )

    commit_proc = await _run_command(
        ["git", "-C", worktree_path, "commit", "-m", message],
        timeout=_GIT_TIMEOUT,
    )
    if commit_proc.returncode != 0:
        combined = commit_proc.stdout + commit_proc.stderr
        if "nothing to commit" in combined:
            sha = await current_sha(worktree_path, "HEAD")
            return CommitResult(
                committed=False,
                commit_sha=sha,
                reason_code=RC_CLEAN_TREE,
            )
        return CommitResult(
            committed=False,
            reason_code=RC_COMMIT_FAILED,
        )

    sha = await current_sha(worktree_path, "HEAD")
    return CommitResult(committed=True, commit_sha=sha)


def diff_sha256(worktree_path: str) -> str:
    """SHA-256 of the diff from merge-base with master to HEAD.

    Used as a stable fingerprint of the operator-approved change set.
    Returns "" on any git failure (caller treats empty as mismatch if
    expected is non-empty).
    """
    try:
        mb = subprocess.run(
            ["git", "-C", worktree_path, "merge-base", "HEAD", "refs/heads/master"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if mb.returncode != 0:
            return ""
        merge_base = mb.stdout.strip()
        diff = subprocess.run(
            ["git", "-C", worktree_path, "diff", merge_base, "HEAD"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if diff.returncode != 0:
            return ""
        return hashlib.sha256(diff.stdout.encode()).hexdigest()
    except (OSError, subprocess.TimeoutExpired):
        return ""


async def current_sha(repo_path: str, ref: str) -> str:
    """Resolve a git ref to its full SHA. Returns "" on failure."""
    proc = await _run_command(
        ["git", "-C", repo_path, "rev-parse", ref],
        timeout=_GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


async def commit_exists(source_repo: str, sha: str) -> bool:
    """True ⟺ ``sha`` resolves to a commit object in ``source_repo``.

    ``git rev-parse --quiet --verify <sha>^{commit}`` (rc 0 ⟹ exists).
    Distinguishes a phantom SHA (claimed commit absent from the repo) from a
    real commit that merely isn't on master — the reachability audit needs
    both signals to classify a stranded-land claim.
    """
    proc = await _run_command(
        [
            "git",
            "-C",
            source_repo,
            "rev-parse",
            "--quiet",
            "--verify",
            f"{sha}^{{commit}}",
        ],
        timeout=_GIT_TIMEOUT,
    )
    return proc.returncode == 0


async def is_reachable_from_master(source_repo: str, sha: str) -> bool:
    """True ⟺ ``sha`` is an ancestor of (or equal to) refs/heads/master.

    Wraps ``git -C <source_repo> merge-base --is-ancestor <sha> refs/heads/master``
    (rc 0 ⟹ reachable). Reconciles against **local** master only — origin push
    is separate and operator-owned, so a not-yet-pushed land still reads
    reachable here. Returns False on any non-zero rc (not-ancestor, phantom SHA,
    or git error); callers needing phantom-vs-not-reachable should consult
    ``commit_exists`` first.
    """
    proc = await _run_command(
        [
            "git",
            "-C",
            source_repo,
            "merge-base",
            "--is-ancestor",
            sha,
            "refs/heads/master",
        ],
        timeout=_GIT_TIMEOUT,
    )
    return proc.returncode == 0


async def fetch_master(worktree_path: str) -> None:
    """Best-effort update of master ref visibility.

    Arc worktrees share the .git ODB with source_repo, so refs/heads/master
    is already visible. This is a no-op for local worktrees and a best-effort
    fetch from '.' for any setup that mirrors refs internally.
    Failure is intentionally non-fatal — the CAS step catches stale state.
    """
    await _run_command(
        [
            "git",
            "-C",
            worktree_path,
            "fetch",
            "--no-tags",
            "--quiet",
            "--update-head-ok",
            ".",
        ],
        timeout=_GIT_TIMEOUT,
    )


async def merge_master_into(worktree_path: str) -> MergeResult:
    """Merge refs/heads/master into the arc branch (no-edit, no fast-forward).

    Returns MergeResult(conflict=True) for any non-zero exit (conflict or
    other merge failure). Caller must abort_merge on conflict.
    """
    proc = await _run_command(
        [
            "git",
            "-C",
            worktree_path,
            "merge",
            "refs/heads/master",
            "--no-edit",
            "--no-ff",
        ],
        timeout=_GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        return MergeResult(conflict=True)

    sha_proc = await _run_command(
        ["git", "-C", worktree_path, "rev-parse", "HEAD"],
        timeout=_GIT_TIMEOUT,
    )
    merge_commit = sha_proc.stdout.strip() if sha_proc.returncode == 0 else ""
    return MergeResult(conflict=False, merge_commit=merge_commit)


async def abort_merge(worktree_path: str) -> None:
    """Abort a pending merge (git merge --abort)."""
    await _run_command(
        ["git", "-C", worktree_path, "merge", "--abort"],
        timeout=_GIT_TIMEOUT,
    )


async def reset_hard_to(worktree_path: str, sha: str) -> None:
    """Hard-reset the worktree to sha (leaves arc branch clean for re-review)."""
    await _run_command(
        ["git", "-C", worktree_path, "reset", "--hard", sha],
        timeout=_GIT_TIMEOUT,
    )


async def advance_master_cas(
    source_repo: str,
    worktree_path: str,
    *,
    expected: str,
) -> CasResult:
    """CAS-advance refs/heads/master from expected to arc HEAD.

    The arc HEAD already contains master (we merged it), so master can
    fast-forward. git update-ref provides the atomic compare-and-swap:
    it fails if master != expected (someone advanced master mid-span).

    Returns CasResult(non_ff=True) on any failure; caller retries.
    """
    new_sha = await current_sha(worktree_path, "HEAD")
    if not new_sha:
        return CasResult(non_ff=True)

    proc = await _run_command(
        [
            "git",
            "-C",
            source_repo,
            "update-ref",
            "refs/heads/master",
            new_sha,
            expected,
        ],
        timeout=_GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        return CasResult(non_ff=True)
    return CasResult(non_ff=False, new_sha=new_sha)


async def _run_command(
    cmd: list[str],
    cwd: str | None = None,
    timeout: float = _GIT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with SIGTERM→SIGKILL timeout.

    ∀ network-touching or potentially-blocking git ops: explicit timeout
    prevents a misconfigured repo from hanging the worker indefinitely.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (ProcessLookupError, TimeoutError):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        return subprocess.CompletedProcess(
            cmd,
            124,
            stdout="",
            stderr=f"timeout after {timeout:.0f}s",
        )

    return subprocess.CompletedProcess(
        cmd,
        proc.returncode or 0,
        stdout=stdout_b.decode(errors="replace"),
        stderr=stderr_b.decode(errors="replace"),
    )
