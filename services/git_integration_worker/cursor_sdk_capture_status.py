"""Pure capture-status classification and divergence detection for cursor-sdk closeout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from implement_admission.closeout_models import EffectsManifest
from implement_admission.deliverable_verification import _paths_intersect
from implement_admission.spec import CloseoutStatus

CaptureStatus = Literal["complete", "partial", "unavailable"]

_CORTEX_ENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*:[^/]+$")


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
    """Classify capture completeness for implement closeouts.

    Trust boundary: repo change-set attribution covers (a) porcelain-tracked
    ``source_repo`` paths and (b) Composer-native write/edit/delete paths via the
    manifest fold (including gitignored/reset-away). It does NOT byte-attribute
    arbitrary shell side effects, undeclared gitignored files, or writes outside
    ``source_repo``. Shell repo manifest entries force ``capture_status=partial``
    with an explicit deviation rather than a silent COMPLETE.
    """
    if not deliverables_expected:
        return None
    if baseline is None:
        return "unavailable"
    if baseline_dirty_in_expected(baseline, files_expected) and not baseline_has_hashes:
        return "partial"
    if manifest and _repo_has_shell_entry(manifest):
        return "partial"
    if manifest and manifest.coverage:
        values = set(manifest.coverage.values())
        if "unavailable" in values:
            return "partial"
        if "partial" in values:
            return "partial"
    return "complete"


def _changed_path_set(change_set: ChangeSet) -> set[str]:
    paths: set[str] = set()
    for group in (change_set.created, change_set.modified, change_set.deleted):
        for path in group:
            paths.add(path.lstrip("/"))
    return paths


def _path_exists_in_sandboxes(path: str, source_repo: Path, cortex_root: Path) -> bool:
    rel = path.lstrip("/")
    return (source_repo / rel).exists() or (cortex_root / rel).exists()


def _divergence_from_divergent_rel(rel_entry: str) -> str | None:
    if rel_entry.startswith("pinned_deliverable_wrong_sandbox:"):
        rel = rel_entry.split(":", 1)[1]
        return f"divergence:target_unreadable:{rel}"
    if rel_entry.startswith("pinned_deliverable_write_failed:"):
        rel = rel_entry.split(":", 1)[1]
        return f"divergence:target_unreadable:{rel}"
    return None


def _repo_manifest_paths(manifest: EffectsManifest | None) -> set[str]:
    section = manifest.surfaces.get("repo") if manifest else None
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in {"write", "edit", "delete"}:
            continue
        if entry.target:
            paths.add(entry.target.lstrip("/"))
    return paths


def _repo_has_shell_entry(manifest: EffectsManifest | None) -> bool:
    section = manifest.surfaces.get("repo") if manifest else None
    if section is None:
        return False
    return any(entry.op == "shell" for entry in section.entries)


def _repo_diff_mismatch(
    manifest: EffectsManifest | None, git_changed: set[str]
) -> str | None:
    manifest_paths = _repo_manifest_paths(manifest)
    has_shell = _repo_has_shell_entry(manifest)
    if not manifest_paths and not has_shell:
        return None
    missing = sorted(path for path in manifest_paths if path not in git_changed)
    if missing:
        return f"divergence:repo_diff_mismatch:{missing[0]}"
    extra = sorted(path for path in git_changed if path not in manifest_paths)
    if extra and manifest_paths:
        return f"divergence:repo_diff_mismatch:{extra[0]}"
    return None


def _cortex_target_absent(
    manifest: EffectsManifest | None, cortex_root: Path
) -> str | None:
    section = manifest.surfaces.get("cortex") if manifest else None
    if section is None:
        return None
    for entry in section.entries:
        target = entry.identity or entry.target
        if not target:
            continue
        if _CORTEX_ENTITY_ID_RE.match(target):
            continue
        rel = target.lstrip("/")
        if rel and not (cortex_root / rel).exists():
            return f"divergence:cortex_target_absent:{target}"
    return None


def _agent_bus_turn_absent(manifest: EffectsManifest | None) -> str | None:
    section = manifest.surfaces.get("agent_bus") if manifest else None
    if section is None:
        return None
    for entry in section.entries:
        if entry.op in {"agent_bus.reply", "agent_bus.send"}:
            continue
        target = entry.target or entry.identity
        if not target or "#" not in target:
            continue
        _thread, turn = target.split("#", 1)
        if turn.strip() and not turn.strip().isdigit():
            return f"divergence:bus_turn_absent:{target}"
    return None


def apply_surface_cross_checks(
    manifest: EffectsManifest | None,
    *,
    change_set: ChangeSet,
    source_repo: Path,
    cortex_root: Path,
    files_expected: list[str],
    divergent_rels: tuple[str, ...],
    deliverables_expected: bool,
    degraded_reason: str | None,
) -> EffectsManifest | None:
    if manifest is None:
        return None
    git_changed = _changed_path_set(change_set)
    updated: dict[str, object] = {}
    coverage = dict(manifest.coverage)
    for name, section in manifest.surfaces.items():
        cross_check = section.cross_check
        if name == "repo" and deliverables_expected and degraded_reason is None:
            cross_check = cross_check or _repo_surface_cross_check(
                manifest=manifest,
                git_changed=git_changed,
                files_expected=files_expected,
                divergent_rels=divergent_rels,
                source_repo=source_repo,
                cortex_root=cortex_root,
                change_set=change_set,
            )
        elif name == "cortex" and deliverables_expected and degraded_reason is None:
            cross_check = cross_check or _cortex_target_absent(manifest, cortex_root)
        elif name == "agent_bus" and deliverables_expected and degraded_reason is None:
            cross_check = cross_check or _agent_bus_turn_absent(manifest)
        if cross_check:
            coverage[name] = "partial"
        elif name in coverage:
            coverage[name] = "complete"
        updated[name] = section.model_copy(update={"cross_check": cross_check})
    return manifest.model_copy(
        update={
            "surfaces": updated,
            "coverage": coverage,
        }
    )


def _repo_surface_cross_check(
    *,
    manifest: EffectsManifest,
    git_changed: set[str],
    files_expected: list[str],
    divergent_rels: tuple[str, ...],
    source_repo: Path,
    cortex_root: Path,
    change_set: ChangeSet,
) -> str | None:
    if files_expected and not _paths_intersect(files_expected, git_changed):
        return "divergence:no_expected_files_touched"
    for rel_entry in divergent_rels:
        reason = _divergence_from_divergent_rel(rel_entry)
        if reason:
            return reason
    for path in (*change_set.created, *change_set.modified):
        if not _path_exists_in_sandboxes(path, source_repo, cortex_root):
            return f"divergence:emitted_path_absent:{path}"
    return _repo_diff_mismatch(manifest, git_changed)


def closeout_divergence_reason(
    *,
    deliverables_expected: bool,
    degraded_reason: str | None,
    change_set: ChangeSet,
    files_expected: list[str],
    divergent_rels: tuple[str, ...],
    source_repo: Path,
    cortex_root: Path,
    manifest: EffectsManifest | None = None,
) -> str | None:
    if not deliverables_expected or degraded_reason is not None:
        return None
    checked = apply_surface_cross_checks(
        manifest,
        change_set=change_set,
        source_repo=source_repo,
        cortex_root=cortex_root,
        files_expected=files_expected,
        divergent_rels=divergent_rels,
        deliverables_expected=deliverables_expected,
        degraded_reason=degraded_reason,
    )
    if checked is None:
        changed = _changed_path_set(change_set)
        if files_expected and not _paths_intersect(files_expected, changed):
            return "divergence:no_expected_files_touched"
        for rel_entry in divergent_rels:
            reason = _divergence_from_divergent_rel(rel_entry)
            if reason:
                return reason
        for path in (*change_set.created, *change_set.modified):
            if not _path_exists_in_sandboxes(path, source_repo, cortex_root):
                return f"divergence:emitted_path_absent:{path}"
        return None
    for name in ("repo", "cortex", "agent_bus", "fs", "rag", "service"):
        section = checked.surfaces.get(name)
        if section and section.cross_check:
            return section.cross_check
    return None


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
) -> tuple[CaptureStatus | None, str | None, list[str], EffectsManifest | None]:
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
    )
    capture_status = classify_capture_status(
        deliverables_expected=deliverables_expected,
        baseline=baseline,
        files_expected=files_expected,
        manifest=manifest,
        baseline_has_hashes=baseline_has_hashes,
    )
    divergence_reason = closeout_divergence_reason(
        deliverables_expected=deliverables_expected,
        degraded_reason=degraded_reason,
        change_set=change_set,
        files_expected=files_expected,
        divergent_rels=divergent_rels,
        source_repo=source_repo,
        cortex_root=cortex_root,
        manifest=manifest,
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
    if divergence_reason:
        deviations.append(divergence_reason)
    return capture_status, divergence_reason, deviations, manifest
