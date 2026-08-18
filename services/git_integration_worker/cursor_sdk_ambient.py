"""L5 ambient repo movement cause classification (6341 arc)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from implement_admission.closeout_models import AmbientRepoCause, AmbientRepoMovement

from services.git_integration_worker.cursor_sdk_capture_status import (
    normalize_wt_baseline,
)


def _hash_worktree_file(source_repo: Path, rel_path: str) -> str | None:
    try:
        data = (source_repo / rel_path.lstrip("/")).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _path_is_tracked(source_repo: Path, rel_path: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "ls-files", "--error-unmatch", rel_path],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _path_is_clean(source_repo: Path, rel_path: str, porcelain: dict[str, str]) -> bool:
    code = porcelain.get(rel_path)
    return code is None


def classify_ambient_cause(
    path: str,
    *,
    source_repo: Path,
    baseline: dict[str, Any] | None,
    git_diff_paths: frozenset[str],
    declared_paths: frozenset[str],
    declared_unproved: bool = False,
    current_porcelain: dict[str, str] | None = None,
) -> AmbientRepoCause:
    """Assign an ambient cause label for unattributed repo movement.

    ``declared_unproved`` is caller-authoritative (G1): honor it even when the
    path is absent from label-op ``declared_paths``, because no-label job
    surface is files_expected ∪ observed rather than write/edit/delete.
    ``declared_paths`` remains for callers that still pass the L5 census set.
    """
    _ = declared_paths
    if declared_unproved:
        return "declared_unproved"
    repo_path = source_repo / path
    try:
        exists = repo_path.is_file()
    except OSError:
        exists = False
    if not exists:
        return "ambient:vanished"
    porcelain = current_porcelain or {}
    if (
        path in git_diff_paths
        and _path_is_tracked(source_repo, path)
        and _path_is_clean(source_repo, path, porcelain)
    ):
        return "ambient:concurrent_commit"
    codes, _ = normalize_wt_baseline(baseline)
    admit_code = codes.get(path)
    if (
        admit_code is not None
        and admit_code.startswith("?")
        and _path_is_tracked(source_repo, path)
        and _path_is_clean(source_repo, path, porcelain)
    ):
        return "ambient:concurrent_commit"
    _, admit_hashes = normalize_wt_baseline(baseline)
    current_hash = _hash_worktree_file(source_repo, path)
    admit_hash = admit_hashes.get(path)
    if admit_hash is not None and current_hash is not None and current_hash != admit_hash:
        return "ambient:concurrent_edit"
    if not _path_is_clean(source_repo, path, porcelain):
        return "ambient:concurrent_edit"
    return "ambient:concurrent_commit"


def ambient_movement(
    path: str,
    *,
    source_repo: Path,
    baseline: dict[str, Any] | None,
    git_diff_paths: frozenset[str],
    declared_paths: frozenset[str],
    declared_unproved: bool = False,
    current_porcelain: dict[str, str] | None = None,
) -> AmbientRepoMovement:
    """Build an ambient movement row from L5 cause classification.

    Callers pass ``declared_unproved`` when a residual could belong to this
    dispatch but scoped-lift did not prove it; otherwise L5 labels concurrent
    commit, edit, or vanished from admit baseline versus the closeout tree.
    """
    return AmbientRepoMovement(
        path=path,
        cause=classify_ambient_cause(
            path,
            source_repo=source_repo,
            baseline=baseline,
            git_diff_paths=git_diff_paths,
            declared_paths=declared_paths,
            declared_unproved=declared_unproved,
            current_porcelain=current_porcelain,
        ),
    )


def ambient_deviation_token(movements: list[AmbientRepoMovement]) -> str | None:
    """Digest token for ambient census — pairs with ``files_ambient_repo_movement``."""
    if not movements:
        return None
    paths = sorted({entry.path for entry in movements})
    shown = ",".join(paths[:3])
    if len(paths) > 3:
        shown = f"{shown},+{len(paths) - 3}"
    return f"divergence:repo_diff_paths_unattributed:ambient:{shown}"
