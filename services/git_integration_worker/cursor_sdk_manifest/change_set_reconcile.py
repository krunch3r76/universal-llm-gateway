"""Git ↔ manifest change-set reconciliation and verification-set assembly.

``resolve_repo_change_set`` is a thin delegate to ``cursor_sdk_repo_precedence``
and **must** keep that import function-local: ``repo_precedence`` imports four
names from this package at module top level, so hoisting deadlocks GIW startup
(R1, highest-risk edge). ``_path_is_tracked`` is re-exported because
``repo_precedence.py:21`` imports it. This module has no intra-package
imports. Nested ``_label`` inside ``git_manifest_label_divergence`` stays
nested.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet


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
) -> tuple[ChangeSet, tuple[str, ...], bool]:
    """Manifest-first change set — thin delegate to ``cursor_sdk_repo_precedence``."""
    # Cycle-breaker: manifest ⇄ cursor_sdk_repo_precedence; other leg is top-level (R1). Hoist deadlocks GIW startup. Do not hoist.
    from services.git_integration_worker.cursor_sdk_repo_precedence import (
        resolve_repo_change_set as _resolve_precedence,
    )

    change_set, extra_untracked, divergence, _ambient = _resolve_precedence(
        manifest=manifest,
        git_change_set=git_change_set,
        source_repo=source_repo,
        mount_root=mount_root,
        baseline=baseline,
        files_expected=files_expected,
        current_porcelain=current_porcelain,
        admit_head=admit_head,
        closeout_head=closeout_head,
    )
    return change_set, extra_untracked, divergence


def git_manifest_label_divergence(
    git_change_set: ChangeSet,
    manifest_change_set: ChangeSet,
) -> bool:
    """True when manifest op-intent labels disagree with git XY labels."""

    def _label(path: str, change_set: ChangeSet) -> str | None:
        if path in change_set.created:
            return "created"
        if path in change_set.modified:
            return "modified"
        if path in change_set.deleted:
            return "deleted"
        return None

    git_paths = (
        set(git_change_set.created)
        | set(git_change_set.modified)
        | set(git_change_set.deleted)
    )
    manifest_paths = (
        set(manifest_change_set.created)
        | set(manifest_change_set.modified)
        | set(manifest_change_set.deleted)
    )
    for path in git_paths | manifest_paths:
        git_label = _label(path, git_change_set)
        manifest_label = _label(path, manifest_change_set)
        if manifest_label is None:
            continue
        if git_label != manifest_label:
            return True
    return False


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


def verification_change_set(
    repo_change_set: ChangeSet, gate_d_created_rels: tuple[str, ...]
) -> ChangeSet:
    if not gate_d_created_rels:
        return repo_change_set
    return ChangeSet(
        created=repo_change_set.created + gate_d_created_rels,
        modified=repo_change_set.modified,
        deleted=repo_change_set.deleted,
    )
