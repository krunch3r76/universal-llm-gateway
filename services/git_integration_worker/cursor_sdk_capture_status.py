"""Pure capture-status classification for cursor-sdk closeout."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from implement_admission.closeout_models import EffectsManifest
from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    light_bounded_capture_status,
)

CaptureStatus = Literal["complete", "partial", "unavailable"]
CapturePathScope = Literal["control_plane", "user_workspace", "external_or_unknown"]

_CONTROL_PLANE_PREFIXES: tuple[str, ...] = (
    "tmp/reviews/closeouts/",
    "tmp/reviews/",
)

_SWAMP_EXCLUDE_PREFIXES: tuple[str, ...] = (
    ".cursor/",
    ".pytest_cache/",
    "__pycache__/",
)


def is_swamp_excluded_path(path: str) -> bool:
    """True when *path* must not appear in closeout files_* or outside-repo census."""
    norm = path.lstrip("/")
    for prefix in _SWAMP_EXCLUDE_PREFIXES:
        bare = prefix.rstrip("/")
        if norm == bare or norm.startswith(prefix):
            return True
    return False


def filter_manifest_swamp(paths: Iterable[str]) -> tuple[str, ...]:
    """Drop `.cursor/`, cache dirs, and gitignored-adjacent swamp from files_* labeling."""
    return tuple(path for path in paths if not is_swamp_excluded_path(path))


@dataclass(frozen=True)
class ChangeSet:
    created: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]


@dataclass(frozen=True)
class CanonicalCapturePath:
    original_path: str
    canonical_path: str
    scope: CapturePathScope
    canonicalization_reason: str


def _git_repo_binding(source_repo: Path) -> tuple[Path, str, str]:
    repo = source_repo.resolve()
    try:
        top_proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        prefix_proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-prefix"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return repo, "", repo.name
    toplevel = Path(top_proc.stdout.strip() or str(repo)).resolve()
    show_prefix = prefix_proc.stdout.strip().strip("/")
    return toplevel, show_prefix, toplevel.name


def _collapse_duplicate_repo_prefix(path: str, repo_name: str) -> tuple[str, bool]:
    collapsed = False
    stripped = path.lstrip("/")
    prefix = f"{repo_name}/"
    while stripped.startswith(prefix):
        stripped = stripped[len(prefix) :]
        collapsed = True
    return stripped, collapsed


def is_allowlisted_control_plane_path(canonical_path: str) -> bool:
    """Exact segment-boundary prefix match — classifier and allowlist share this form."""
    norm = canonical_path.lstrip("/")
    for prefix in _CONTROL_PLANE_PREFIXES:
        bare = prefix.rstrip("/")
        if norm == bare or norm.startswith(prefix):
            return True
    return False


def _classify_capture_path_scope(
    canonical_path: str,
    *,
    external: bool,
) -> CapturePathScope:
    if external or not canonical_path:
        return "external_or_unknown"
    if is_allowlisted_control_plane_path(canonical_path):
        return "control_plane"
    return "user_workspace"


def canonicalize_capture_path(raw: str, *, source_repo: Path) -> CanonicalCapturePath:
    """Idempotent repo-relative normal form for capture classification."""
    original = raw.strip()
    if not original:
        return CanonicalCapturePath(
            original_path=original,
            canonical_path="",
            scope="external_or_unknown",
            canonicalization_reason="empty_path",
        )

    toplevel, show_prefix, repo_name = _git_repo_binding(source_repo)
    path = original.replace("\\", "/")
    reasons: list[str] = []
    external = False

    candidate = Path(path)
    if candidate.is_absolute():
        try:
            resolved = candidate.resolve()
            try:
                rel = resolved.relative_to(toplevel)
                path = rel.as_posix()
                reasons.append("absolute_inside_repo")
            except ValueError:
                external = True
                reasons.append("absolute_outside_repo")
        except OSError:
            external = True
            reasons.append("absolute_unresolvable")

    if not external:
        top_text = toplevel.as_posix().rstrip("/")
        for prefix in (f"{top_text}/", f"{top_text.lstrip('/')}/"):
            if path.startswith(prefix):
                path = path[len(prefix) :]
                reasons.append("toplevel_prefix_stripped")
                break

        path, collapsed = _collapse_duplicate_repo_prefix(path, repo_name)
        if collapsed:
            reasons.append("duplicate_repo_prefix_collapsed")

        if show_prefix:
            worktree_prefix = f"{show_prefix}/"
            if path.startswith(worktree_prefix):
                path = path[len(worktree_prefix) :]
                reasons.append("worktree_prefix_stripped")
            elif path == show_prefix:
                path = ""
                reasons.append("worktree_prefix_stripped")

        path = path.lstrip("/")

        candidate = toplevel / path if path else toplevel
        try:
            resolved = candidate.resolve()
            resolved.relative_to(toplevel.resolve())
        except ValueError:
            external = True
            reasons.append("symlink_outside_repo")
        except OSError:
            pass

    scope = _classify_capture_path_scope(path, external=external)
    reason = "+".join(reasons) if reasons else "repo_relative"
    return CanonicalCapturePath(
        original_path=original,
        canonical_path=path,
        scope=scope,
        canonicalization_reason=reason,
    )


def is_no_write_intent_reason(degraded_reason: str | None) -> bool:
    return degraded_reason == "stated_intent_no_write"


def iter_capture_paths(
    change_set: ChangeSet,
    manifest: EffectsManifest | None,
    *,
    source_repo: Path,
) -> tuple[CanonicalCapturePath, ...]:
    raw_paths: list[str] = []
    raw_paths.extend(change_set.created)
    raw_paths.extend(change_set.modified)
    raw_paths.extend(change_set.deleted)
    if manifest is not None:
        section = manifest.surfaces.get("repo")
        if section is not None:
            for entry in section.entries:
                if entry.op in {"write", "edit", "delete"} and entry.target:
                    raw_paths.append(entry.target)
    seen: set[str] = set()
    ordered: list[CanonicalCapturePath] = []
    for raw in raw_paths:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        ordered.append(canonicalize_capture_path(raw, source_repo=source_repo))
    return tuple(ordered)


def stated_intent_no_write_capture_violation(
    *,
    change_set: ChangeSet,
    manifest: EffectsManifest | None,
    source_repo: Path,
    degraded_reason: str | None,
) -> str | None:
    """Hard final gate — manifest suppression cannot bypass no-write contract."""
    if not is_no_write_intent_reason(degraded_reason):
        return None
    violations: list[str] = []
    for canon in iter_capture_paths(change_set, manifest, source_repo=source_repo):
        if canon.scope == "user_workspace":
            violations.append(canon.canonical_path)
        elif canon.scope == "external_or_unknown":
            violations.append(canon.original_path)
    if not violations:
        return None
    return f"capture:stated_intent_no_write_violation:{violations[0]}"


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


def partition_gitignored_from_change_set(
    change_set: ChangeSet,
    *,
    source_repo: Path,
    existing_untracked: tuple[str, ...] = (),
) -> tuple[ChangeSet, tuple[str, ...]]:
    """Move gitignored, swamp, and control-plane paths out of files_* buckets."""
    untracked = list(existing_untracked)

    def _partition(paths: tuple[str, ...]) -> tuple[str, ...]:
        kept: list[str] = []
        for path in paths:
            if is_allowlisted_control_plane_path(path):
                continue
            if is_swamp_excluded_path(path) or _path_gitignored(source_repo, path):
                if path not in untracked:
                    untracked.append(path)
                continue
            kept.append(path)
        return tuple(kept)

    return (
        ChangeSet(
            created=_partition(change_set.created),
            modified=_partition(change_set.modified),
            deleted=_partition(change_set.deleted),
        ),
        tuple(sorted(set(untracked))),
    )


def _normalize_repo_path_for_compare(
    raw: str,
    *,
    source_repo: Path | None,
) -> str:
    if source_repo is None:
        return raw.strip().lstrip("/")
    canon = canonicalize_capture_path(raw, source_repo=source_repo)
    return canon.canonical_path or raw.lstrip("/")


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
        if entry.op not in {"write", "edit", "delete", "observed"}:
            continue
        if entry.target:
            paths.add(
                _normalize_repo_path_for_compare(entry.target, source_repo=source_repo)
            )
    return paths


def repo_diff_unattributed_deviation(
    *,
    change_set: ChangeSet,
    manifest: EffectsManifest | None,
    source_repo: Path | None = None,
) -> str | None:
    """Deviation when baseline-diff paths carry no write-evidence in the dispatch's own capture.

    Friction 23015: a stale/ambient worktree diff attributed another session's
    edits (and their failing lint) to a verification-only dispatch. The files_*
    buckets stay git-authoritative (shell writes are legitimately invisible to
    the manifest — see capture:shell_repo_writes_unverified), but the
    manifest/diff disagreement must be machine-visible so a lead reconciles the
    bus-turn manifest against the closeout instead of trusting phantom writes.
    """
    diff_paths = [*change_set.created, *change_set.modified, *change_set.deleted]
    if not diff_paths:
        return None
    evidence = _repo_manifest_paths(manifest, source_repo=source_repo)
    unattributed = sorted(
        path
        for path in dict.fromkeys(diff_paths)
        if _normalize_repo_path_for_compare(path, source_repo=source_repo)
        not in evidence
    )
    if not unattributed:
        return None
    shown = ",".join(unattributed[:3])
    if len(unattributed) > 3:
        shown = f"{shown},+{len(unattributed) - 3}"
    return f"divergence:repo_diff_paths_unattributed:{shown}"


def gitignored_manifest_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path,
    git_changed: set[str],
) -> tuple[str, ...]:
    """Manifest repo paths on disk that are gitignored or swamp-excluded."""
    manifest_paths = _repo_manifest_paths(manifest, source_repo=source_repo)
    ignored: list[str] = []
    for path in sorted(manifest_paths):
        if path in git_changed:
            continue
        if not (source_repo / path).is_file():
            continue
        if _path_gitignored(source_repo, path) or is_swamp_excluded_path(path):
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
        if deliverables_expected and manifest and _repo_has_shell_entry(manifest):
            deviations.append("capture:shell_repo_writes_unverified")
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
    intent_violation = stated_intent_no_write_capture_violation(
        change_set=change_set,
        manifest=manifest,
        source_repo=source_repo,
        degraded_reason=degraded_reason,
    )
    if intent_violation:
        if capture_status == "complete":
            capture_status = "partial"
        if divergence_reason is None:
            divergence_reason = intent_violation
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
    unattributed_deviation = repo_diff_unattributed_deviation(
        change_set=change_set,
        manifest=manifest,
        source_repo=source_repo,
    )
    if unattributed_deviation:
        deviations.append(unattributed_deviation)
    if outside_repo_paths and not worktree_isolated:
        deviations.append("capture:outside_repo_paths_present")
    if files_untracked_or_ignored:
        deviations.append("capture:gitignored_present_unattributed")
    if divergence_reason:
        deviations.append(divergence_reason)
    if intent_violation:
        deviations.append(intent_violation)
    return capture_status, divergence_reason, deviations, manifest
