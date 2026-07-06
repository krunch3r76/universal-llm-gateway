"""Pure capture-status classification for cursor-sdk closeout."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from implement_admission.closeout_models import EffectsManifest
from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    light_bounded_capture_status,
)

CaptureStatus = Literal["complete", "partial", "unavailable"]


@dataclass(frozen=True)
class ChangeSet:
    created: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]


def _normalize_expected_path(raw: str) -> str:
    path = raw.strip()
    if " (" in path:
        path = path.split(" (", 1)[0].strip()
    return path.lstrip("/")


def normalize_wt_baseline(
    baseline: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Normalize legacy flat `{path: code}` or `{codes, hashes}` admit snapshots."""
    if baseline is None:
        return {}, {}
    if "codes" in baseline:
        codes = baseline.get("codes", {})
        hashes = baseline.get("hashes", {})
        return (
            codes if isinstance(codes, dict) else {},
            hashes if isinstance(hashes, dict) else {},
        )
    return baseline, {}


def dirty_expected_hashes_available(
    baseline: dict[str, Any] | None, files_expected: list[str]
) -> bool:
    """True when every dirty expected path has an admit-time content hash."""
    codes, hashes = normalize_wt_baseline(baseline)
    if not codes or not files_expected:
        return True
    expected = {_normalize_expected_path(p) for p in files_expected}
    for path in codes:
        norm = path.lstrip("/")
        if norm in expected or any(norm.endswith(f"/{exp}") for exp in expected):
            if path not in hashes:
                return False
    return True


def baseline_dirty_in_expected(
    baseline: dict[str, Any] | None, files_expected: list[str]
) -> bool:
    codes, _ = normalize_wt_baseline(baseline)
    if not codes or not files_expected:
        return False
    expected = {_normalize_expected_path(p) for p in files_expected}
    for path in codes:
        norm = path.lstrip("/")
        if norm in expected or any(norm.endswith(f"/{exp}") for exp in expected):
            return True
    return False


def classify_capture_status(
    *,
    deliverables_expected: bool,
    baseline: dict[str, Any] | None,
    files_expected: list[str],
    manifest: EffectsManifest | None = None,
    baseline_has_hashes: bool = False,
) -> CaptureStatus | None:
    """Classify capture completeness for implement closeouts."""
    if not deliverables_expected:
        return None
    if baseline is None:
        return "unavailable"
    if baseline_dirty_in_expected(baseline, files_expected) and not baseline_has_hashes:
        return "partial"
    if manifest and manifest.coverage:
        values = set(manifest.coverage.values())
        if "unavailable" in values:
            return "partial"
        if "partial" in values:
            return "partial"
    return "complete"


def _normalize_repo_path_for_compare(
    raw: str,
    *,
    source_repo: Path | None,
) -> str:
    from services.git_integration_worker.cursor_sdk_manifest import _normalize_repo_path

    normalized = _normalize_repo_path(raw, repo_root=source_repo)
    return normalized or raw.lstrip("/")


def _path_gitignored(source_repo: Path, rel_path: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "check-ignore", "-q", rel_path],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _repo_manifest_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    section = manifest.surfaces.get("repo") if manifest else None
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in {"write", "edit", "delete"}:
            continue
        if entry.target:
            paths.add(
                _normalize_repo_path_for_compare(entry.target, source_repo=source_repo)
            )
    return paths


def gitignored_manifest_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path,
    git_changed: set[str],
) -> tuple[str, ...]:
    """Manifest repo paths present on disk but ignored by git."""
    manifest_paths = _repo_manifest_paths(manifest, source_repo=source_repo)
    ignored: list[str] = []
    for path in sorted(manifest_paths):
        if path in git_changed:
            continue
        if not (source_repo / path).is_file():
            continue
        if _path_gitignored(source_repo, path):
            ignored.append(path)
    return tuple(ignored)


def _path_exists_in_sandboxes(path: str, source_repo: Path, cortex_root: Path) -> bool:
    rel = path.lstrip("/")
    return (source_repo / rel).exists() or (cortex_root / rel).exists()


def _repo_has_shell_entry(manifest: EffectsManifest | None) -> bool:
    section = manifest.surfaces.get("repo") if manifest else None
    if section is None:
        return False
    return any(entry.op == "shell" for entry in section.entries)


def degrade_status_for_capture(
    status: CloseoutStatus,
    capture_status: CaptureStatus | None,
    divergence_reason: str | None,
) -> CloseoutStatus:
    if status != CloseoutStatus.COMPLETE:
        return status
    if capture_status in {"partial", "unavailable"} or divergence_reason:
        return CloseoutStatus.PARTIAL
    return status


def resolve_closeout_capture_fields(
    *,
    deliverables_expected: bool,
    baseline: dict[str, Any] | None,
    files_expected: list[str],
    degraded_reason: str | None,
    change_set: ChangeSet,
    divergent_rels: tuple[str, ...],
    source_repo: Path,
    cortex_root: Path,
    manifest: EffectsManifest | None = None,
    mount_root: Path | None = None,
    outside_repo_paths: tuple[str, ...] = (),
    files_untracked_or_ignored: tuple[str, ...] = (),
    light_bounded_expected_paths: tuple[str, ...] = (),
    worktree_isolated: bool = False,
) -> tuple[CaptureStatus | None, str | None, list[str], EffectsManifest | None]:
    from services.git_integration_worker.cursor_sdk_capture_divergence import (
        apply_surface_cross_checks,
        closeout_divergence_reason,
        expected_deliverables_present,
    )

    if baseline is None and light_bounded_expected_paths:
        capture_status, divergence_reason = light_bounded_capture_status(
            light_bounded_expected_paths,
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
        deviations = [divergence_reason] if divergence_reason else []
        return capture_status, divergence_reason, deviations, manifest
    baseline_has_hashes = dirty_expected_hashes_available(baseline, files_expected)
    manifest = apply_surface_cross_checks(
        manifest,
        change_set=change_set,
        source_repo=source_repo,
        cortex_root=cortex_root,
        files_expected=files_expected,
        divergent_rels=divergent_rels,
        deliverables_expected=deliverables_expected,
        degraded_reason=degraded_reason,
        mount_root=mount_root,
        outside_repo_paths=outside_repo_paths,
        worktree_isolated=worktree_isolated,
    )
    capture_status = classify_capture_status(
        deliverables_expected=deliverables_expected,
        baseline=baseline,
        files_expected=files_expected,
        manifest=manifest,
        baseline_has_hashes=baseline_has_hashes,
    )
    if (
        deliverables_expected
        and files_expected
        and capture_status == "complete"
        and not expected_deliverables_present(
            files_expected,
            manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
    ):
        capture_status = "partial"
    divergence_reason = closeout_divergence_reason(
        deliverables_expected=deliverables_expected,
        degraded_reason=degraded_reason,
        change_set=change_set,
        files_expected=files_expected,
        divergent_rels=divergent_rels,
        source_repo=source_repo,
        cortex_root=cortex_root,
        manifest=manifest,
        mount_root=mount_root,
        outside_repo_paths=outside_repo_paths,
        files_untracked_or_ignored=files_untracked_or_ignored,
        worktree_isolated=worktree_isolated,
    )
    if divergence_reason and capture_status == "complete":
        capture_status = "partial"
    deviations: list[str] = []
    if (
        capture_status == "partial"
        and baseline
        and baseline_dirty_in_expected(baseline, files_expected)
        and not baseline_has_hashes
    ):
        deviations.append("capture:dirty_baseline_under_capture")
    if deliverables_expected and manifest and _repo_has_shell_entry(manifest):
        deviations.append("capture:shell_repo_writes_unverified")
    if outside_repo_paths and not worktree_isolated:
        deviations.append("capture:outside_repo_paths_present")
    if files_untracked_or_ignored:
        deviations.append("capture:gitignored_present_unattributed")
    if divergence_reason:
        deviations.append(divergence_reason)
    return capture_status, divergence_reason, deviations, manifest
