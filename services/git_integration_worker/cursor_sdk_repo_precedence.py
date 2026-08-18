"""L3 manifest-first precedence + L4 scoped-lift for closeout files_* (6341 arc).

No-label-ops (no write/edit/delete on the repo surface) use the same L4/L5
loop as label-ops: scoped-lift, lane-exclusive, then ambient. The resolver
does not copy the git ChangeSet wholesale into attributed buckets.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from implement_admission.closeout_models import AmbientRepoMovement, EffectsManifest

from services.git_integration_worker.cursor_sdk_ambient import ambient_movement
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    normalize_wt_baseline,
)
from services.git_integration_worker.cursor_sdk_git_head import (
    git_diff_paths_between,
    paths_exclusive_to_lane,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    _path_is_tracked,
    git_manifest_label_divergence,
    manifest_repo_paths,
    repo_change_set_from_manifest,
)
from services.git_integration_worker.cursor_sdk_polarity import (
    ClaimedOp,
    _tracked_at_commit,
    list_git_deleted_paths,
    prove_polarity,
)


def _hash_worktree_file(source_repo: Path, rel_path: str) -> str | None:
    try:
        data = (source_repo / rel_path.lstrip("/")).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _label_for_path(path: str, change_set: ChangeSet) -> ClaimedOp | None:
    if path in change_set.created:
        return "created"
    if path in change_set.modified:
        return "modified"
    if path in change_set.deleted:
        return "deleted"
    return None


def _append_bucket(
    buckets: dict[ClaimedOp, list[str]], op: ClaimedOp, path: str
) -> None:
    bucket = buckets[op]
    if path not in bucket:
        bucket.append(path)


def _path_on_job_surface(path: str, job_surface: set[str]) -> bool:
    return path in job_surface or any(path.endswith(f"/{job}") for job in job_surface)


def _residual_declared_unproved(
    path: str,
    *,
    has_shell: bool,
    job_surface: set[str],
    baseline_codes: dict[str, str],
    porcelain: dict[str, str],
) -> bool:
    """G1 classifier: unproved-us vs concurrent ambient.

    ``declared_unproved`` when a shell dispatch could own a residual that
    scoped-lift did not prove. Admit-dirty paths off the job surface stay
    concurrent even with ``has_shell`` (specimen class). Porcelain-clean
    residuals stay concurrent_commit — passing unproved would short-circuit
    that L5 label.
    """
    if not has_shell:
        return False
    admit_dirty = path in baseline_codes
    if admit_dirty and not _path_on_job_surface(path, job_surface):
        return False
    if porcelain.get(path) is None:
        return False
    return True


def _repo_has_shell_op(manifest: EffectsManifest | None) -> bool:
    section = manifest.surfaces.get("repo") if manifest else None
    if section is None:
        return False
    return any(entry.op == "shell" for entry in section.entries)


def _job_surface_paths(
    files_expected: list[str] | None,
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None,
) -> set[str]:
    surface = {raw.strip().lstrip("/") for raw in (files_expected or []) if raw.strip()}
    surface.update(manifest_repo_paths(manifest, source_repo=source_repo))
    return surface


def _blob_sha256_at_commit(source_repo: Path, commit: str, path: str) -> str | None:
    """Content hash of ``path`` at ``commit`` — admit snapshot for clean-at-admit files."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "show", f"{commit}:{path}"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return hashlib.sha256(proc.stdout).hexdigest()


def _hash_delta_proven(
    path: str,
    *,
    source_repo: Path,
    baseline_hashes: dict[str, str],
    claimed: ClaimedOp,
    admit_head: str | None = None,
) -> bool:
    if claimed == "deleted":
        return not (source_repo / path).exists()
    current_hash = _hash_worktree_file(source_repo, path)
    if current_hash is None:
        return False
    admit_hash = baseline_hashes.get(path)
    if admit_hash is None and admit_head is not None:
        admit_hash = _blob_sha256_at_commit(source_repo, admit_head, path)
    if admit_hash is None:
        return claimed == "created"
    return current_hash != admit_hash


def _scoped_lift_eligible(
    path: str,
    *,
    claimed: ClaimedOp,
    source_repo: Path,
    baseline: dict[str, Any] | None,
    job_surface: set[str],
    has_shell: bool,
    baseline_codes: dict[str, str],
    baseline_hashes: dict[str, str],
    current_porcelain: dict[str, str],
    git_deleted_paths: frozenset[str],
    admit_head: str | None,
) -> bool:
    if not _path_on_job_surface(path, job_surface):
        return False
    if not _hash_delta_proven(
        path,
        source_repo=source_repo,
        baseline_hashes=baseline_hashes,
        claimed=claimed,
        admit_head=admit_head,
    ):
        return False
    if not has_shell:
        return False
    current_hash = _hash_worktree_file(source_repo, path)
    return prove_polarity(
        claimed=claimed,
        path=path,
        source_repo=source_repo,
        baseline_codes=baseline_codes,
        baseline_hashes=baseline_hashes,
        current_porcelain=current_porcelain,
        current_hash=current_hash,
        git_deleted_paths=git_deleted_paths,
        admit_head=admit_head,
    )


def _reclassify_within(
    manifest_op: ClaimedOp,
    git_op: ClaimedOp | None,
) -> tuple[ClaimedOp, bool]:
    if git_op is None or git_op == manifest_op:
        return manifest_op, False
    return git_op, True


def _infer_lane_path_op(
    path: str,
    *,
    source_repo: Path,
    baseline_codes: dict[str, str],
    admit_head: str | None,
) -> ClaimedOp:
    """Polarity for a path whose only repo movement is a lane commit."""
    if not (source_repo / path).exists():
        return "deleted"
    admit_code = baseline_codes.get(path)
    if admit_code is not None and admit_code.startswith("?"):
        return "created"
    if admit_code is not None:
        return "modified"
    if admit_head is not None and not _tracked_at_commit(source_repo, admit_head, path):
        return "created"
    return "modified"


def _lane_exclusive_paths(
    source_repo: Path | None,
    *,
    dispatch_id: str | None,
    admit_head: str | None,
    closeout_head: str | None,
) -> frozenset[str]:
    if source_repo is None or not dispatch_id:
        return frozenset()
    return paths_exclusive_to_lane(
        source_repo,
        dispatch_id=dispatch_id,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )


def resolve_repo_change_set(
    *,
    manifest: EffectsManifest | None,
    git_change_set: ChangeSet,
    source_repo: Path | None = None,
    mount_root: Path | None = None,
    baseline: dict[str, Any] | None = None,
    files_expected: list[str] | None = None,
    current_porcelain: dict[str, str] | None = None,
    admit_head: str | None = None,
    closeout_head: str | None = None,
    dispatch_id: str | None = None,
) -> tuple[ChangeSet, tuple[str, ...], bool, list[AmbientRepoMovement]]:
    """Manifest-first change set with L4 scoped lift and L5 ambient routing.

    Label-ops and no-label-ops share this loop. Empty ``manifest_cs`` (no
    write/edit/delete) still lifts job-surface ∩ hash-delta ∩ shell ∩ polarity
    or lane-exclusive paths; remaining git deltas go ambient.
    """
    manifest_cs, _, _ = repo_change_set_from_manifest(
        manifest,
        source_repo=source_repo,
        mount_root=mount_root,
    )
    if manifest_cs is None:
        manifest_cs = ChangeSet(created=(), modified=(), deleted=())
    baseline_codes, baseline_hashes = normalize_wt_baseline(baseline)
    porcelain = current_porcelain or {}
    if admit_head is None and isinstance(baseline, dict):
        raw_head = baseline.get("admit_head")
        if isinstance(raw_head, str) and raw_head.strip():
            admit_head = raw_head.strip()

    divergence = git_manifest_label_divergence(git_change_set, manifest_cs)
    git_deleted = (
        list_git_deleted_paths(source_repo) if source_repo is not None else frozenset()
    )
    git_diff_paths = (
        git_diff_paths_between(
            source_repo,
            admit_head=admit_head,
            closeout_head=closeout_head,
        )
        if source_repo is not None
        else frozenset()
    )
    lane_exclusive = _lane_exclusive_paths(
        source_repo,
        dispatch_id=dispatch_id,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    declared_paths = frozenset(
        set(manifest_cs.created)
        | set(manifest_cs.modified)
        | set(manifest_cs.deleted)
    )
    job_surface = _job_surface_paths(
        files_expected,
        manifest,
        source_repo=source_repo,
    )
    has_shell = _repo_has_shell_op(manifest)
    buckets: dict[ClaimedOp, list[str]] = {
        "created": [],
        "modified": [],
        "deleted": [],
    }
    ambient: list[AmbientRepoMovement] = []
    attributed: set[str] = set()

    def _attribute_lane_or_ambient(
        path: str,
        *,
        declared_unproved: bool = False,
    ) -> None:
        if path in lane_exclusive and source_repo is not None:
            lane_op = _infer_lane_path_op(
                path,
                source_repo=source_repo,
                baseline_codes=baseline_codes,
                admit_head=admit_head,
            )
            _append_bucket(buckets, lane_op, path)
            attributed.add(path)
            return
        if not declared_unproved:
            declared_unproved = _residual_declared_unproved(
                path,
                has_shell=has_shell,
                job_surface=job_surface,
                baseline_codes=baseline_codes,
                porcelain=porcelain,
            )
        ambient.append(
            ambient_movement(
                path,
                source_repo=source_repo,
                baseline=baseline,
                git_diff_paths=git_diff_paths,
                declared_paths=declared_paths,
                declared_unproved=declared_unproved,
                current_porcelain=porcelain,
            )
        )

    manifest_paths_ordered: list[tuple[str, ClaimedOp]] = [
        *((path, "created") for path in manifest_cs.created),
        *((path, "modified") for path in manifest_cs.modified),
        *((path, "deleted") for path in manifest_cs.deleted),
    ]

    for path, manifest_op in manifest_paths_ordered:
        git_op = _label_for_path(path, git_change_set)
        final_op, relabeled = _reclassify_within(manifest_op, git_op)
        if relabeled:
            divergence = True
        current_hash = _hash_worktree_file(source_repo, path) if source_repo else None
        proved = (
            source_repo is not None
            and prove_polarity(
                claimed=final_op,
                path=path,
                source_repo=source_repo,
                baseline_codes=baseline_codes,
                baseline_hashes=baseline_hashes,
                current_porcelain=porcelain,
                current_hash=current_hash,
                git_deleted_paths=git_deleted,
                admit_head=admit_head,
            )
        )
        if proved:
            _append_bucket(buckets, final_op, path)
            attributed.add(path)
        else:
            _attribute_lane_or_ambient(path, declared_unproved=True)

    git_paths = (
        set(git_change_set.created)
        | set(git_change_set.modified)
        | set(git_change_set.deleted)
    )
    manifest_paths = (
        set(manifest_cs.created) | set(manifest_cs.modified) | set(manifest_cs.deleted)
    )
    extra_untracked: list[str] = []
    for path in sorted(git_paths - manifest_paths):
        if path in attributed:
            continue
        git_op = _label_for_path(path, git_change_set)
        if git_op is None or source_repo is None:
            continue
        if _scoped_lift_eligible(
            path,
            claimed=git_op,
            source_repo=source_repo,
            baseline=baseline,
            job_surface=job_surface,
            has_shell=has_shell,
            baseline_codes=baseline_codes,
            baseline_hashes=baseline_hashes,
            current_porcelain=porcelain,
            git_deleted_paths=git_deleted,
            admit_head=admit_head,
        ):
            _append_bucket(buckets, git_op, path)
            attributed.add(path)
        else:
            _attribute_lane_or_ambient(path)

    observed_paths = git_paths | set(git_diff_paths) | set(manifest_paths)
    if source_repo is not None:
        observed_paths |= {
            path
            for path, code in baseline_codes.items()
            if code.startswith("?")
            and _path_is_tracked(source_repo, path)
            and porcelain.get(path) is None
        }
    for path in sorted(observed_paths - attributed):
        if any(entry.path == path for entry in ambient):
            continue
        if source_repo is None:
            continue
        candidate = source_repo / path
        try:
            on_disk = candidate.is_file()
        except OSError:
            on_disk = False
        if path in manifest_paths and not on_disk:
            continue
        _attribute_lane_or_ambient(path)

    for path in sorted(manifest_paths - git_paths):
        if path in attributed or any(entry.path == path for entry in ambient):
            continue
        if source_repo is None:
            continue
        candidate = source_repo / path
        try:
            if not candidate.is_file():
                continue
        except OSError:
            continue
        if not _path_is_tracked(source_repo, path):
            extra_untracked.append(path)

    return (
        ChangeSet(
            created=tuple(buckets["created"]),
            modified=tuple(buckets["modified"]),
            deleted=tuple(buckets["deleted"]),
        ),
        tuple(extra_untracked),
        divergence,
        ambient,
    )
