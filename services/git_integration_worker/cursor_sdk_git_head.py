"""Git HEAD resolution and admit→closeout diff helpers (6341 L2)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_git_head(source_repo: Path) -> str | None:
    """Resolved ``git rev-parse HEAD`` for *source_repo*, or ``None`` when absent."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    sha = proc.stdout.decode("utf-8", errors="replace").strip()
    return sha or None


def git_diff_paths_between(
    source_repo: Path,
    *,
    admit_head: str | None,
    closeout_head: str | None,
) -> frozenset[str]:
    """Paths in ``git diff --name-only admit_head..closeout_head`` (once per closeout)."""
    if not admit_head or not closeout_head:
        return frozenset()
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "diff",
                "--name-only",
                f"{admit_head}..{closeout_head}",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return frozenset()
    return frozenset(
        chunk.decode("utf-8", errors="replace")
        for chunk in proc.stdout.splitlines()
        if chunk
    )


def commits_between(
    source_repo: Path,
    *,
    admit_head: str | None,
    closeout_head: str | None,
) -> list[str]:
    """Commit SHAs in ``admit_head..closeout_head`` (oldest first)."""
    if not admit_head or not closeout_head or admit_head == closeout_head:
        return []
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "log",
                f"{admit_head}..{closeout_head}",
                "--reverse",
                "--format=%H",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return []
    return [
        line.decode("utf-8", errors="replace").strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    ]


def enumerate_tip_window_commits(
    source_repo: Path,
    *,
    admit_head: str | None,
    closeout_head: str | None,
) -> list[tuple[str, str]]:
    """Commit ``(sha, author_email)`` pairs in ``admit_head..closeout_head`` (oldest first).

    Single subprocess for dual-meter stamping — failure collapses to ``[]`` once (S10a).
    """
    if not admit_head or not closeout_head or admit_head == closeout_head:
        return []
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "log",
                f"{admit_head}..{closeout_head}",
                "--reverse",
                "--format=%H%x00%ae",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return []
    rows: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        parts = line.split(b"\x00", 1)
        if len(parts) != 2:
            continue
        sha = parts[0].decode("utf-8", errors="replace").strip()
        email = parts[1].decode("utf-8", errors="replace").strip()
        if sha:
            rows.append((sha, email))
    return rows


def partition_tip_window_meters(
    rows: list[tuple[str, str]],
    *,
    dispatch_id: str,
) -> tuple[list[str], list[str]]:
    """Split tip-window *rows* into authored vs unfiltered SHA lists."""
    from services.git_integration_worker.cursor_home import dispatch_git_identity

    _name, dispatch_email = dispatch_git_identity(dispatch_id)
    unfiltered = [sha for sha, _ in rows]
    authored = [sha for sha, author_email in rows if author_email == dispatch_email]
    return authored, unfiltered


def tip_window_meter_counts(
    source_repo: Path,
    *,
    dispatch_id: str,
    admit_head: str | None,
    closeout_head: str | None,
) -> tuple[int, int] | None:
    """Authored and unfiltered commit counts from one tip-window enumeration.

    Returns ``None`` when either head is missing. When heads resolve, returns
    ``(authored_count, unfiltered_count)`` — including ``(0, 0)`` for an empty
    range or a single subprocess failure (S10a co-collapse).
    """
    if not admit_head or not closeout_head:
        return None
    rows = enumerate_tip_window_commits(
        source_repo,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    authored, unfiltered = partition_tip_window_meters(rows, dispatch_id=dispatch_id)
    return len(authored), len(unfiltered)


def land_paths_from_merge_sha(source_repo: Path, sha: str) -> tuple[str, ...]:
    """Recover land-shaped paths from a merge (or any) commit SHA.

    Uses ``diff-tree -m --name-only`` only — never ``git show --name-only``.
    """
    return tuple(sorted(paths_in_commit(source_repo, sha)))


def paths_in_commit(source_repo: Path, sha: str) -> frozenset[str]:
    """Paths touched by a single commit (diff-tree name-only)."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "diff-tree",
                "--no-commit-id",
                "-r",
                "--name-only",
                "-m",
                sha,
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return frozenset()
    return frozenset(
        chunk.decode("utf-8", errors="replace")
        for chunk in proc.stdout.splitlines()
        if chunk
    )


def paths_exclusive_to_lane(
    source_repo: Path,
    *,
    dispatch_id: str,
    admit_head: str | None,
    closeout_head: str | None,
) -> frozenset[str]:
    """Paths in admit→closeout diff touched only by this dispatch's lane commits."""
    lane_refs = observed_lane_git_refs(
        source_repo,
        dispatch_id=dispatch_id,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    if not lane_refs:
        return frozenset()
    all_diff = git_diff_paths_between(
        source_repo,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    lane_set = set(lane_refs)
    lane_paths: set[str] = set()
    peer_paths: set[str] = set()
    for sha in commits_between(
        source_repo, admit_head=admit_head, closeout_head=closeout_head
    ):
        commit_paths = paths_in_commit(source_repo, sha)
        if sha in lane_set:
            lane_paths |= commit_paths
        else:
            peer_paths |= commit_paths
    return frozenset(p for p in all_diff if p in lane_paths and p not in peer_paths)


def observed_lane_git_refs(
    source_repo: Path,
    *,
    dispatch_id: str,
    admit_head: str | None,
    closeout_head: str | None,
) -> list[str]:
    """SHAs of lane commits authored by *dispatch_id* between admit and closeout."""
    if not admit_head or not closeout_head or admit_head == closeout_head:
        return []
    from services.git_integration_worker.cursor_home import dispatch_git_identity

    _name, email = dispatch_git_identity(dispatch_id)
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "log",
                f"{admit_head}..{closeout_head}",
                f"--author={email}",
                "--format=%H",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return []
    return [
        line.decode("utf-8", errors="replace").strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    ]
