"""Admit-time worktree porcelain snapshot, content hashing, and change-set derivation.

Captures git porcelain (-z) at admit, optional content hashes plus outside-repo
census, then derives created/modified/deleted paths against that baseline with
polarity proofs. ``reconcile_workspace_changes`` extends the git delta with
gitignored and outside-repo paths across registered roots. ``capture_wt_baseline``
is a monkeypatch seam: same-module callers (``capture_wt_baseline_with_hashes``,
``changed_paths``) already look up the global at call time; keep that. Logger
is the only logger in this package today — ``get_logger(__name__)``, never
``logging.getLogger``. The function-local re-import of ``resolve_mount_root``
inside ``reconcile_workspace_changes`` stays function-local ([scope]).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from implement_admission.closeout_models import EffectsManifest
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    baseline_dirty_in_expected,
    gitignored_manifest_paths,
    normalize_wt_baseline,
)
from services.git_integration_worker.cursor_sdk_git_head import resolve_git_head
from services.git_integration_worker.cursor_sdk_manifest import (
    registered_repo_roots,
    resolve_mount_root,
    snapshot_outside_repo_paths,
)
from services.git_integration_worker.cursor_sdk_polarity import (
    ClaimedOp,
    git_concurs_deleted,
    list_git_deleted_paths,
    polarity_deviation_token,
    prove_polarity,
)

logger = get_logger(__name__)

def _parse_porcelain_z(raw: bytes) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not raw:
        return entries
    parts = raw.split(b"\0")
    i = 0
    while i < len(parts):
        chunk = parts[i]
        if not chunk:
            i += 1
            continue
        text = chunk.decode("utf-8", errors="replace")
        if len(text) >= 4 and text[2] == " ":
            status = text[:2]
            path = text[3:]
            if status.startswith("R") and i + 1 < len(parts):
                path = parts[i + 1].decode("utf-8", errors="replace")
                i += 1
            entries[path] = status
        i += 1
    return entries


def _hash_worktree_file(source_repo: Path, path: str) -> str | None:
    try:
        data = (source_repo / path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def capture_wt_baseline(source_repo: Path) -> dict[str, str] | None:
    """Snapshot working-tree paths at admit for later delta isolation."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=all",
            ],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("wt baseline capture failed for repo=%s: %s", source_repo, exc)
        return None
    return _parse_porcelain_z(proc.stdout)


def capture_wt_baseline_with_hashes(
    source_repo: Path,
    *,
    mount_root: Path | None = None,
    repo_roots: list[Path] | tuple[Path, ...] | None = None,
) -> dict[str, Any] | None:
    """Porcelain codes plus content hashes and outside-repo census at admit."""
    codes = capture_wt_baseline(source_repo)
    if codes is None:
        return None
    hashes: dict[str, str] = {}
    for path in codes:
        digest = _hash_worktree_file(source_repo, path)
        if digest is not None:
            hashes[path] = digest
    mount = (mount_root or resolve_mount_root(source_repo)).resolve()
    roots = list(repo_roots) if repo_roots is not None else registered_repo_roots(mount)
    outside = sorted(snapshot_outside_repo_paths(mount, roots))
    return {
        "codes": codes,
        "hashes": hashes,
        "outside_repo": outside,
        "admit_head": resolve_git_head(source_repo),
    }

def _split_baseline(
    baseline: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    return normalize_wt_baseline(baseline)


def changed_paths(
    source_repo: Path, baseline: dict[str, Any] | None
) -> tuple[ChangeSet, tuple[str, ...]]:
    """Derive created/modified/deleted paths vs an admit-time baseline."""
    current = capture_wt_baseline(source_repo)
    if current is None:
        return ChangeSet(created=(), modified=(), deleted=()), ()
    codes, hashes = _split_baseline(baseline)
    admit_head: str | None = None
    if isinstance(baseline, dict):
        raw_head = baseline.get("admit_head")
        if isinstance(raw_head, str) and raw_head.strip():
            admit_head = raw_head.strip()
    git_deleted = list_git_deleted_paths(source_repo)
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    deviations: list[str] = []
    all_paths = set(current) | set(codes) | git_deleted
    for path in sorted(all_paths):
        cur = current.get(path)
        prev = codes.get(path)
        claimed: ClaimedOp | None = None
        current_hash: str | None = None
        repo_path = source_repo / path
        is_gone = cur is None or not repo_path.exists()
        git_concurs_del = git_concurs_deleted(path, current, git_deleted)
        if is_gone and (
            prev is not None or (admit_head is not None and git_concurs_del)
        ):
            claimed = "deleted"
        elif cur is not None and prev is None:
            claimed = "created" if cur.startswith("?") else "modified"
            current_hash = _hash_worktree_file(source_repo, path)
        elif cur is not None and prev is not None and cur != prev:
            claimed = "modified"
            current_hash = _hash_worktree_file(source_repo, path)
        elif cur is not None and prev is not None and cur == prev and path in hashes:
            current_hash = _hash_worktree_file(source_repo, path)
            if current_hash is not None and current_hash != hashes[path]:
                claimed = "created" if prev.startswith("?") else "modified"
        if claimed is None:
            continue
        if prove_polarity(
            claimed=claimed,
            path=path,
            source_repo=source_repo,
            baseline_codes=codes,
            baseline_hashes=hashes,
            current_porcelain=current,
            current_hash=current_hash,
            git_deleted_paths=git_deleted,
            admit_head=admit_head,
        ):
            if claimed == "deleted":
                deleted.append(path)
            elif claimed == "created":
                created.append(path)
            else:
                modified.append(path)
        else:
            deviations.append(polarity_deviation_token(path))
    return (
        ChangeSet(
            created=tuple(created),
            modified=tuple(modified),
            deleted=tuple(deleted),
        ),
        tuple(deviations),
    )


def _baseline_outside_repo_paths(baseline: dict[str, Any] | None) -> frozenset[str]:
    if baseline is None:
        return frozenset()
    raw = baseline.get("outside_repo")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(path) for path in raw)


def reconcile_workspace_changes(
    *,
    source_repo: Path,
    baseline: dict[str, Any] | None,
    manifest: EffectsManifest | None = None,
    mount_root: Path | None = None,
    repo_roots: list[Path] | tuple[Path, ...] | None = None,
) -> tuple[ChangeSet, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Multi-root disk reconciliation: git diff + outside-repo paths + gitignored."""
    from services.git_integration_worker.cursor_sdk_manifest import resolve_mount_root

    mount = (mount_root or resolve_mount_root(source_repo)).resolve()
    repos = list(repo_roots) if repo_roots is not None else registered_repo_roots(mount)
    if baseline is None:
        git_change = ChangeSet(created=(), modified=(), deleted=())
        polarity_deviations: tuple[str, ...] = ()
    else:
        git_change, polarity_deviations = changed_paths(source_repo, baseline)
    git_changed = (
        set(git_change.created) | set(git_change.modified) | set(git_change.deleted)
    )
    gitignored = gitignored_manifest_paths(
        manifest,
        source_repo=source_repo,
        git_changed=git_changed,
    )
    baseline_outside = _baseline_outside_repo_paths(baseline)
    current_outside = snapshot_outside_repo_paths(mount, repos)
    new_outside = tuple(sorted(current_outside - baseline_outside))
    return git_change, gitignored, new_outside, polarity_deviations


def _baseline_dirty_in_expected(
    baseline: dict[str, Any] | None, files_expected: list[str]
) -> bool:
    return baseline_dirty_in_expected(baseline, files_expected)
