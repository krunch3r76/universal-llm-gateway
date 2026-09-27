"""Path-overlap dirty predicate for master land (S1a dirty_master successor).

The CAS primitive advances ``refs/heads/master`` via ``update-ref``. The land
loop then checks out only the landed paths on a master worktree when those
paths are clean or byte-identical to the incoming blob. This module refuses
before that checkout when a checked-out master worktree has porcelain dirt on
a landing path whose bytes diverge from the arc tip. The refusal is
``NOT landed@working-tree`` plus the porcelain line, and the path is not
written. Byte-identical overlap and disjoint-only dirt are allowed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_GIT_TIMEOUT = 30.0


def landing_path_names(worktree_path: str) -> frozenset[str]:
    """Return paths that would advance onto master for this arc worktree.

    Union of ``merge-base..HEAD`` name-only and the arc's porcelain paths so
    uncommitted land content is included without mutating the real index.
    Returns empty on git failure (caller treats as no overlap).
    """
    paths: set[str] = set()
    try:
        mb = subprocess.run(
            [
                "git",
                "-C",
                worktree_path,
                "merge-base",
                "HEAD",
                "refs/heads/master",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if mb.returncode != 0:
            return frozenset()
        merge_base = mb.stdout.strip()
        if not merge_base:
            return frozenset()
        diff = subprocess.run(
            [
                "git",
                "-C",
                worktree_path,
                "diff",
                "--name-only",
                merge_base,
                "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if diff.returncode == 0:
            paths.update(
                line.strip() for line in diff.stdout.splitlines() if line.strip()
            )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    paths.update(_porcelain_paths(worktree_path))
    return frozenset(paths)


def _parse_porcelain_paths(porcelain: str) -> frozenset[str]:
    """Parse text porcelain. Prefer ``status_paths_z``; quotes break this form."""
    paths: set[str] = set()
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        body = line[3:]
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        rel = body.strip()
        if len(rel) >= 2 and rel[0] == '"' and rel[-1] == '"':
            rel = rel[1:-1]
        if rel:
            paths.add(rel)
    return frozenset(paths)


def _path_porcelain(worktree_path: str, rel_path: str) -> str:
    """Porcelain line for one path, or empty when the path is clean."""
    try:
        proc = subprocess.run(
            ["git", "-C", worktree_path, "status", "--porcelain", "--", rel_path],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _porcelain_paths(worktree_path: str) -> frozenset[str]:
    """Return dirty/untracked paths in ``worktree_path``, or empty on failure.

    ``-z`` keeps paths that contain a space unquoted so they match the
    landing path set. Text porcelain would quote them and miss the overlap.
    """
    from git_integrate.hub_tree_sync import status_paths_z

    found = status_paths_z(worktree_path)
    if found is None:
        return frozenset()
    return found


def _file_bytes(root: str, rel_path: str) -> bytes | None:
    """Read file bytes at ``root/rel_path``; ``None`` when absent or unreadable."""
    path = Path(root) / rel_path
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _parse_worktree_blocks(porcelain: str) -> list[tuple[str, str]]:
    """Return ``(worktree_path, branch_ref)`` pairs from worktree porcelain."""
    blocks: list[tuple[str, str]] = []
    path = ""
    branch = ""
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if path:
                blocks.append((path, branch))
            path = line[len("worktree ") :].strip()
            branch = ""
        elif line.startswith("branch "):
            branch = line[len("branch ") :].strip()
    if path:
        blocks.append((path, branch))
    return blocks


def checked_out_master_dirty(
    source_repo: str,
    landing_worktree: str,
) -> tuple[bool, str]:
    """True when checked-out master has divergent dirt on a landing path.

    ``landing_worktree`` is the arc worktree whose path set is about to land.
    Disjoint dirt and byte-identical overlap do not refuse. Returns
    ``(False, "")`` when no master checkout exists or git probes fail closed
    toward allow only after an explicit empty overlap (probe failure yields
    no blocks → allow; callers still hold the land lease).
    """
    repo = str(Path(source_repo).resolve())
    landing = str(Path(landing_worktree).resolve())
    landing_paths = landing_path_names(landing)
    if not landing_paths:
        return False, ""

    try:
        proc = subprocess.run(
            ["git", "-C", repo, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    if proc.returncode != 0:
        return False, ""

    for worktree_path, branch_ref in _parse_worktree_blocks(proc.stdout):
        if branch_ref != "refs/heads/master":
            continue
        dirty_paths = _porcelain_paths(worktree_path)
        overlap = dirty_paths & landing_paths
        for rel in sorted(overlap):
            master_bytes = _file_bytes(worktree_path, rel)
            landing_bytes = _file_bytes(landing, rel)
            if master_bytes == landing_bytes:
                continue
            porcelain = _path_porcelain(worktree_path, rel)
            return True, (
                "NOT landed@working-tree\n"
                f"hub-porcelain:{porcelain}\n"
                "checked-out master worktree has divergent dirt on landing "
                f"path {rel!r} ({worktree_path!r}); refusing merge-out"
            )
    return False, ""
