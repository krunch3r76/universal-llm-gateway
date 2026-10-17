"""Sync a checked-out master worktree after a ref-only CAS land.

``advance_master_cas`` moves ``refs/heads/master`` and does not touch any
index. When master is checked out, that leaves the hub index and worktree
on the pre-land tree. This module updates only the paths in ``old..new``.

A landed path whose worktree bytes are not the incoming blob is left
untouched and the plan is ``blocked``. Callers must not CAS in that case:
moving the ref and then skipping the checkout is the stale-tree failure.
Paths outside the land diff are never named, so unrelated dirt stays.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Set while a post-move checkout is running so a nested reference-transaction
# hook does not re-enter the repair.
REPAIR_ENV = "GIW_MASTER_REF_REPAIR"

# A reference-transaction hook inherits GIT_DIR from the worktree that ran
# the ref update. ``git -C <hub>`` does not override that, so a lane
# ``update-ref`` would checkout into the lane index and only half-update
# the hub worktree. Every git call in this module names its repo with -C
# and must not inherit those variables.
_GIT_ENV_BLOCKLIST = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


def _git_subprocess_env() -> dict[str, str]:
    """Environment for ``git -C <explicit repo>`` with hook location vars removed."""
    env = os.environ.copy()
    for key in _GIT_ENV_BLOCKLIST:
        env.pop(key, None)
    return env


_GIT_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class HubSyncPlan:
    """What a post-CAS checkout is allowed to touch."""

    checkout: str
    blocked: bool
    porcelain: str
    checkout_paths: tuple[str, ...]
    remove_paths: tuple[str, ...]


def master_worktree(source_repo: str) -> str:
    """Path of the worktree that has ``refs/heads/master`` checked out, or ""."""
    path, _error = _probe_master_worktree(source_repo)
    return path


def _probe_master_worktree(source_repo: str) -> tuple[str, str]:
    """``(path, error)``. ``error`` is set when the probe itself failed."""
    try:
        proc = subprocess.run(
            ["git", "-C", source_repo, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
            env=_git_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", "worktree-list-failed"
    if proc.returncode != 0:
        return "", "worktree-list-failed"
    path = ""
    branch = ""
    found = ""
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            if path and branch == "refs/heads/master":
                found = path
            path = line[len("worktree ") :].strip()
            branch = ""
        elif line.startswith("branch "):
            branch = line[len("branch ") :].strip()
    if path and branch == "refs/heads/master":
        found = path
    return found, ""


def _run_git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
            env=_git_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            args=["git", "-C", repo, *args],
            returncode=1,
            stdout="",
            stderr=str(exc),
        )


def _run_git_bytes(repo: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            check=False,
            env=_git_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            args=["git", "-C", repo, *args],
            returncode=1,
            stdout=b"",
            stderr=str(exc).encode(),
        )


def parse_status_z(data: bytes) -> list[tuple[str, str]]:
    """``(unquoted path, 'XY path')`` from ``git status --porcelain=v1 -z``.

    Rename and copy records are keyed on the destination path. NUL framing
    is required: the text porcelain quotes paths that contain a space, and
    a quoted key does not match the path ``diff`` reports.
    """
    found: list[tuple[str, str]] = []
    parts = data.split(b"\0")
    index = 0
    while index < len(parts):
        rec = parts[index]
        if not rec:
            break
        if len(rec) < 4:
            index += 1
            continue
        xy = rec[:2].decode("ascii", "replace")
        path = rec[3:].decode("utf-8", "surrogateescape")
        if "R" in xy or "C" in xy:
            index += 1
            if index >= len(parts) or not parts[index]:
                break
            path = parts[index].decode("utf-8", "surrogateescape")
        found.append((path, f"{xy} {path}"))
        index += 1
    return found


def status_paths_z(worktree: str) -> frozenset[str] | None:
    """Unquoted dirty paths in ``worktree``. ``None`` when status cannot be read."""
    proc = _run_git_bytes(worktree, "status", "--porcelain=v1", "-z")
    if proc.returncode != 0:
        return None
    return frozenset(path for path, _line in parse_status_z(proc.stdout))


def _dirty_map(worktree: str, paths: list[str]) -> dict[str, str] | None:
    """Unquoted path → porcelain line. ``None`` when status cannot be read."""
    found: dict[str, str] = {}
    step = 50
    for start in range(0, len(paths), step):
        chunk = paths[start : start + step]
        proc = _run_git_bytes(worktree, "status", "--porcelain=v1", "-z", "--", *chunk)
        if proc.returncode != 0:
            return None
        for path, line in parse_status_z(proc.stdout):
            found[path] = line
    return found


_PATH_CHUNK = 50


def _changed_entries(
    source_repo: str, old_sha: str, new_sha: str
) -> list[tuple[str, str]] | None:
    """``(kind, path)`` for ``old..new``. ``None`` when the diff cannot be read.

    Renames become a delete of the old path plus an add of the new path.
    """
    if not old_sha or not new_sha:
        return None
    proc = _run_git(
        source_repo,
        "diff",
        "--name-status",
        "--find-renames",
        old_sha,
        new_sha,
    )
    if proc.returncode != 0:
        return None
    entries: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            entries.append(("D", parts[1]))
            entries.append(("A", parts[2]))
            continue
        if len(parts) < 2:
            continue
        kind = "D" if status.startswith("D") else status[:1]
        entries.append((kind, parts[1]))
    return entries


def porcelain_for_paths(worktree: str, paths: list[str]) -> str:
    """``git status --porcelain -- <paths>``. Empty when ``paths`` is empty."""
    if not paths:
        return ""
    dirty = _dirty_map(worktree, paths)
    if dirty is None:
        return "git status failed"
    return "\n".join(line for _path, line in dirty.items() if line)


def _blob_bytes(repo: str, sha: str, path: str) -> bytes | None:
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "show", f"{sha}:{path}"],
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            check=False,
            env=_git_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _file_bytes(root: str, rel_path: str) -> bytes | None:
    path = Path(root) / rel_path
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def plan_hub_sync(source_repo: str, old_sha: str, new_sha: str) -> HubSyncPlan:
    """Decide which landed paths a checkout may update.

    ``blocked`` means at least one landed path is dirty and its bytes are
    not the blob ``new_sha`` would write (or, for a deletion, not the blob
    ``old_sha`` still has). The caller must not move the ref.
    """
    checkout, probe_error = _probe_master_worktree(source_repo)
    empty = HubSyncPlan(
        checkout="",
        blocked=False,
        porcelain="",
        checkout_paths=(),
        remove_paths=(),
    )
    if probe_error:
        return HubSyncPlan(
            checkout="",
            blocked=True,
            porcelain=probe_error,
            checkout_paths=(),
            remove_paths=(),
        )
    if not checkout:
        return empty
    entries = _changed_entries(source_repo, old_sha, new_sha)
    if entries is None:
        return HubSyncPlan(
            checkout=checkout,
            blocked=True,
            porcelain="diff-failed",
            checkout_paths=(),
            remove_paths=(),
        )
    if not entries:
        return HubSyncPlan(
            checkout=checkout,
            blocked=False,
            porcelain="",
            checkout_paths=(),
            remove_paths=(),
        )
    paths = [path for _kind, path in entries]
    dirty = _dirty_map(checkout, paths)
    if dirty is None:
        return HubSyncPlan(
            checkout=checkout,
            blocked=True,
            porcelain="git status failed",
            checkout_paths=(),
            remove_paths=(),
        )
    blocked_lines: list[str] = []
    checkout_paths: list[str] = []
    remove_paths: list[str] = []
    for kind, path in entries:
        line = dirty.get(path, "")
        current = _file_bytes(checkout, path)
        if kind == "D":
            old_blob = _blob_bytes(source_repo, old_sha, path)
            if line and current is not None and current != old_blob:
                blocked_lines.append(line)
            else:
                remove_paths.append(path)
            continue
        target = _blob_bytes(source_repo, new_sha, path)
        if line and current != target:
            blocked_lines.append(line)
            continue
        checkout_paths.append(path)
    if blocked_lines:
        return HubSyncPlan(
            checkout=checkout,
            blocked=True,
            porcelain="\n".join(blocked_lines),
            checkout_paths=(),
            remove_paths=(),
        )
    return HubSyncPlan(
        checkout=checkout,
        blocked=False,
        porcelain="",
        checkout_paths=tuple(checkout_paths),
        remove_paths=tuple(remove_paths),
    )


def apply_hub_sync(plan: HubSyncPlan, new_sha: str) -> str:
    """Checkout ``plan`` paths from ``new_sha``. No-op when blocked or no checkout.

    Returns porcelain of the touched paths afterward. A non-empty return
    means the ref and the worktree still disagree.
    """
    if not plan.checkout or plan.blocked:
        return plan.porcelain
    watched = list(plan.checkout_paths) + list(plan.remove_paths)
    if plan.checkout_paths:
        failed = _git_in_chunks(
            plan.checkout, ("checkout", new_sha, "--"), plan.checkout_paths
        )
        if failed:
            return f"checkout-failed:{failed}"
    if plan.remove_paths:
        failed = _git_in_chunks(plan.checkout, ("rm", "-f", "--"), plan.remove_paths)
        if failed:
            return f"rm-failed:{failed}"
    return porcelain_for_paths(plan.checkout, watched)


def _git_in_chunks(repo: str, prefix: tuple[str, ...], paths: tuple[str, ...]) -> str:
    """Run ``git prefix paths`` in chunks. Empty string on success."""
    for start in range(0, len(paths), _PATH_CHUNK):
        chunk = paths[start : start + _PATH_CHUNK]
        proc = _run_git(repo, *prefix, *chunk)
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip() or "git failed"
    return ""


def paths_in_commit(source_repo: str, sha: str) -> list[str]:
    """Paths a commit changes against its first parent.

    ``diff-tree -m`` would also list the other side of a merge. A root
    commit falls back to ``--root``.
    """
    parent = _run_git(source_repo, "rev-parse", "--verify", "--quiet", f"{sha}^")
    if parent.returncode == 0 and parent.stdout.strip():
        proc = _run_git(
            source_repo,
            "diff",
            "--name-status",
            "--find-renames",
            parent.stdout.strip(),
            sha,
        )
    else:
        proc = _run_git(
            source_repo,
            "diff-tree",
            "--no-commit-id",
            "-r",
            "--name-status",
            "--root",
            sha,
        )
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        candidates = parts[1:]
        for rel in candidates:
            if rel and rel not in seen:
                seen.add(rel)
                paths.append(rel)
    return paths


def _rev_spec(repo: str, spec: str) -> str | None:
    """Object name for ``spec``, or None when it does not resolve."""
    proc = _run_git(repo, "rev-parse", "--verify", "--quiet", spec)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def _bytes_match_old_or_new(
    current: bytes | None,
    old_blob: bytes | None,
    new_blob: bytes | None,
) -> bool:
    """True when the worktree is the pre-land blob, the incoming blob, or absent like either."""
    return current == old_blob or current == new_blob


def master_ref_move_refusal(source_repo: str, old_sha: str, new_sha: str) -> str | None:
    """Refuse a master ref move that would clobber divergent hub bytes.

    A path whose index already matches ``new_sha`` is a commit or merge that
    has staged the incoming blob; later unstaged bytes on that path stay.
    A path whose worktree still matches ``old_sha`` or already matches
    ``new_sha`` is safe to repair after the ref moves. Any other bytes are
    an operator edit. Returns a reason, or None when the move may proceed.
    No checked-out master means there is no index to disagree with.
    """
    if not old_sha or not new_sha or old_sha == new_sha:
        return None
    checkout, probe_error = _probe_master_worktree(source_repo)
    if probe_error:
        return probe_error
    if not checkout:
        return None
    entries = _changed_entries(source_repo, old_sha, new_sha)
    if entries is None:
        return "diff-failed"
    divergent: list[str] = []
    for kind, path in entries:
        new_spec = f"{new_sha}:{path}"
        index_oid = _rev_spec(checkout, f":{path}")
        new_oid = _rev_spec(source_repo, new_spec)
        if index_oid is not None and index_oid == new_oid:
            continue
        old_blob = None if kind == "A" else _blob_bytes(source_repo, old_sha, path)
        new_blob = None if kind == "D" else _blob_bytes(source_repo, new_sha, path)
        current = _file_bytes(checkout, path)
        if _bytes_match_old_or_new(current, old_blob, new_blob):
            continue
        divergent.append(path)
    if not divergent:
        return None
    return "divergent hub bytes: " + ", ".join(divergent)


def repair_master_worktree(source_repo: str, old_sha: str, new_sha: str) -> str:
    """Checkout landed paths that still hold the pre-move blob.

    Call this after ``refs/heads/master`` has moved. Re-planning with
    ``plan_hub_sync`` at that point treats the stale index as divergent
    dirt and skips the checkout. This repair uses the old and new blobs
    instead. Worktree bytes that match neither blob are left in place.
    Returns porcelain of the paths it tried to update.
    """
    if os.environ.get(REPAIR_ENV) == "1":
        return ""
    if not old_sha or not new_sha or old_sha == new_sha:
        return ""
    checkout, probe_error = _probe_master_worktree(source_repo)
    if probe_error:
        return probe_error
    if not checkout:
        return ""
    entries = _changed_entries(source_repo, old_sha, new_sha)
    if not entries:
        return ""
    checkout_paths: list[str] = []
    remove_paths: list[str] = []
    for kind, path in entries:
        new_oid = None if kind == "D" else _rev_spec(source_repo, f"{new_sha}:{path}")
        index_oid = _rev_spec(checkout, f":{path}")
        current = _file_bytes(checkout, path)
        old_blob = None if kind == "A" else _blob_bytes(source_repo, old_sha, path)
        new_blob = None if kind == "D" else _blob_bytes(source_repo, new_sha, path)
        if not _bytes_match_old_or_new(current, old_blob, new_blob):
            continue
        if kind == "D":
            if index_oid is None and current is None:
                continue
            remove_paths.append(path)
            continue
        if index_oid == new_oid and current == new_blob:
            continue
        checkout_paths.append(path)
    if not checkout_paths and not remove_paths:
        return ""
    os.environ[REPAIR_ENV] = "1"
    try:
        watched = checkout_paths + remove_paths
        if checkout_paths:
            failed = _git_in_chunks(
                checkout, ("checkout", new_sha, "--"), tuple(checkout_paths)
            )
            if failed:
                return f"checkout-failed:{failed}"
        if remove_paths:
            failed = _git_in_chunks(checkout, ("rm", "-f", "--"), tuple(remove_paths))
            if failed:
                return f"rm-failed:{failed}"
        return porcelain_for_paths(checkout, watched)
    finally:
        os.environ.pop(REPAIR_ENV, None)


def hub_porcelain_for_sha(source_repo: str, sha: str) -> str | None:
    """Porcelain of ``sha``'s paths on the checked-out master worktree.

    ``None`` when master is not checked out (nothing to disagree with).
    ``""`` when those paths are clean.
    """
    checkout, probe_error = _probe_master_worktree(source_repo)
    if probe_error:
        return probe_error
    if not checkout:
        return None
    paths = paths_in_commit(source_repo, sha)
    return porcelain_for_paths(checkout, paths)
