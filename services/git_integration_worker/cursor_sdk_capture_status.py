"""Pure capture-status classification for cursor-sdk closeout."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from implement_admission.closeout_models import EffectsManifest, Verification
from implement_admission.spec import NO_RUN_DEGRADED_REASONS, CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_sdk_capture_policy import (
    any_hard_fail_deviation,
    deviation_caps_work_at_unverified,
    deviation_degrades_capture_status,
)
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


_PROBEABLE_PREFIXES: tuple[str, ...] = (
    "notes/",
    "tasks/",
    "docs/",
    "libs/",
    "services/",
    "config/",
    "scripts/",
    "pipelines/",
    "tmp/",
)


def is_probeable_expected_path(raw: str) -> bool:
    """Reject malformed extraction tokens that cannot serve as I2 presence probes."""
    path = raw.strip()
    if not path:
        return False
    lower = path.lower().rstrip("/")
    if lower in ("cortex://", "workspaces://", "cortex:", "workspaces:"):
        return False
    if lower.startswith("cortex://"):
        rel = lower[len("cortex://") :].strip("/")
        if not rel:
            return False
    elif lower.startswith("workspaces://"):
        rest = lower[len("workspaces://") :].strip("/")
        if not rest or "/" not in rest:
            return False
    norm = _normalize_expected_path(path)
    if not norm:
        return False
    if "/" not in norm and not any(norm.startswith(prefix) for prefix in _PROBEABLE_PREFIXES):
        return False
    return True


def filter_probeable_expected_paths(paths: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in paths:
        if not is_probeable_expected_path(raw):
            continue
        norm = _normalize_expected_path(raw)
        if norm in seen:
            continue
        seen.add(norm)
        ordered.append(raw.strip())
    return tuple(ordered)


def _expected_paths_all_malformed_token(rejected_paths: tuple[str, ...]) -> str:
    """Census token when every declared expected path fails probeability filtering."""
    preserved = ",".join(rejected_paths)
    return f"capture:expected_paths_all_malformed:{preserved}"


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


def _manifest_coverage_hard_fail(manifest: EffectsManifest) -> bool:
    """Coverage partial/unavailable degrades only on HARD_FAIL cross_checks (a:25136)."""
    if not manifest.coverage:
        return False
    if "unavailable" in manifest.coverage.values():
        return True
    for name, cov in manifest.coverage.items():
        if cov != "partial":
            continue
        section = manifest.surfaces.get(name)
        cross_check = section.cross_check if section else None
        if cross_check is None or deviation_degrades_capture_status(cross_check):
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
    if manifest and _manifest_coverage_hard_fail(manifest):
        return "partial"
    return "complete"


def attribution_effects_paths(
    *,
    created: Iterable[str] = (),
    modified: Iterable[str] = (),
    deleted: Iterable[str] = (),
    files_untracked_or_ignored: Iterable[str] = (),
) -> tuple[str, ...]:
    """Union tracked paths with non-swamp untracked/gitignored writes for ``effects``.

    Semantics (AM-7): paths *touched* by this dispatch — created, modified,
    deleted, or written untracked/gitignored — in-repo only. Offgit deliverables
    remain in ``files_offgit_produced``.

    Trust floor (AM-5): ``effects`` is authoritative only when
    ``capture_status=complete``; under degraded capture, empty or short
    ``effects`` must not authorize "no writes occurred".
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(path: str) -> None:
        norm = path.lstrip("/")
        if not norm or norm in seen:
            return
        seen.add(norm)
        ordered.append(norm)

    for path in created:
        _add(path)
    for path in modified:
        _add(path)
    for path in deleted:
        _add(path)
    for path in files_untracked_or_ignored:
        if is_allowlisted_control_plane_path(path):
            continue
        if is_swamp_excluded_path(path):
            continue
        _add(path)

    return tuple(sorted(ordered))


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


def _repo_manifest_write_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    from services.git_integration_worker.cursor_sdk_manifest import _REPO_WRITE_OPS

    section = manifest.surfaces.get("repo") if manifest else None
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in _REPO_WRITE_OPS:
            continue
        if entry.target:
            paths.add(
                _normalize_repo_path_for_compare(entry.target, source_repo=source_repo)
            )
    return paths


def _repo_manifest_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    from services.git_integration_worker.cursor_sdk_manifest import _REPO_FILE_OPS

    section = manifest.surfaces.get("repo") if manifest else None
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in _REPO_FILE_OPS:
            continue
        if entry.target:
            paths.add(
                _normalize_repo_path_for_compare(entry.target, source_repo=source_repo)
            )
    return paths


def _hash_worktree_file(source_repo: Path, rel_path: str) -> str | None:
    try:
        data = (source_repo / rel_path.lstrip("/")).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def _repo_manifest_evidence_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
    baseline: dict[str, Any] | None = None,
) -> set[str]:
    """Hash-bound manifest write-evidence — not mere path membership (A1)."""
    manifest_paths = _repo_manifest_write_paths(manifest, source_repo=source_repo)
    if not manifest_paths or source_repo is None:
        return set()
    _, admit_hashes = normalize_wt_baseline(baseline)
    evidence: set[str] = set()
    for path in manifest_paths:
        current_hash = _hash_worktree_file(source_repo, path)
        if current_hash is None:
            continue
        admit_hash = admit_hashes.get(path)
        if admit_hash is None or current_hash != admit_hash:
            evidence.add(path)
    return evidence


def _job_surface_paths(
    files_expected: list[str],
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    surface = {
        _normalize_expected_path(raw) for raw in files_expected if raw.strip()
    }
    surface.update(_repo_manifest_paths(manifest, source_repo=source_repo))
    return surface


def _format_unattributed_token(prefix: str, paths: list[str]) -> str | None:
    if not paths:
        return None
    shown = ",".join(paths[:3])
    if len(paths) > 3:
        shown = f"{shown},+{len(paths) - 3}"
    return f"{prefix}{shown}"


def ambient_deviation_from_movements(movements: list[Any]) -> str | None:
    """Structured ``files_ambient_repo_movement`` rows → census digest token (6341 L5)."""
    from implement_admission.closeout_models import AmbientRepoMovement

    from services.git_integration_worker.cursor_sdk_ambient import (
        ambient_deviation_token,
    )

    typed = [entry for entry in movements if isinstance(entry, AmbientRepoMovement)]
    return ambient_deviation_token(typed)


def repo_diff_unattributed_deviation(
    *,
    change_set: ChangeSet,
    manifest: EffectsManifest | None,
    source_repo: Path | None = None,
    files_expected: list[str] | None = None,
    baseline: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Dual-channel unattributed diff: ambient visibility vs scoped hard gate.

    Friction 23015: ambient (non-job-surface) diff stays visible for lead reconcile
    without degrading status. Hard status applies only to job-surface paths
    lacking hash-bound manifest evidence.
    """
    diff_paths = [*change_set.created, *change_set.modified, *change_set.deleted]
    if not diff_paths:
        return None, None
    evidence = _repo_manifest_evidence_paths(
        manifest,
        source_repo=source_repo,
        baseline=baseline,
    )
    job_surface = _job_surface_paths(
        files_expected or [],
        manifest,
        source_repo=source_repo,
    )
    ambient: list[str] = []
    scoped: list[str] = []
    for path in dict.fromkeys(diff_paths):
        norm = _normalize_repo_path_for_compare(path, source_repo=source_repo)
        if norm in evidence:
            continue
        on_job_surface = norm in job_surface or any(
            norm.endswith(f"/{job}") for job in job_surface
        )
        if on_job_surface:
            scoped.append(norm or path)
        else:
            ambient.append(norm or path)
    ambient_token = _format_unattributed_token(
        "divergence:repo_diff_paths_unattributed:ambient:",
        sorted(ambient),
    )
    scoped_token = _format_unattributed_token(
        "divergence:repo_diff_paths_unattributed:",
        sorted(scoped),
    )
    return ambient_token, scoped_token


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


def verification_all_pass(verification: list[Verification] | None) -> bool:
    """True when every verification row exited 0 — I2 positive probe."""
    if not verification:
        return False
    return all(item.exit_code == 0 for item in verification)


def _is_closeout_receipt_path(path: str) -> bool:
    """True for worker control-plane closeout receipts — never intended artifacts."""
    norm = _normalize_expected_path(path).lower().replace("\\", "/")
    if "tmp/reviews/closeouts/" in norm:
        return True
    return is_allowlisted_control_plane_path(norm)


def _filter_intended_artifact_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Drop worker closeout receipts from the G₁ intended-artifact probe union."""
    return tuple(
        path
        for path in filter_probeable_expected_paths(paths)
        if not _is_closeout_receipt_path(path)
    )


def positive_deliverable_evidence(
    *,
    files_offgit_produced: Iterable[str] = (),
    artifact_paths: Iterable[str] = (),
    light_bounded_expected_paths: Iterable[str] = (),
    files_expected: list[str] | None = None,
    manifest: EffectsManifest | None,
    source_repo: Path,
    cortex_root: Path,
    baseline: dict[str, Any] | None = None,
) -> bool:
    """I2 — capture-independent positive probe for work_outcome=shipped.

    Intended-artifact evidence excludes worker closeout receipts under
    ``**/tmp/reviews/closeouts/**`` (todo:success-shaped-silence G₁).
    """
    from services.git_integration_worker.cursor_sdk_capture_divergence import (
        expected_deliverables_present,
    )

    probe_union: list[str] = []
    for seq in (
        files_offgit_produced,
        artifact_paths,
        light_bounded_expected_paths,
        files_expected or [],
    ):
        probe_union.extend(_filter_intended_artifact_paths(seq))
    if probe_union and expected_deliverables_present(
        probe_union,
        manifest,
        source_repo=source_repo,
        cortex_root=cortex_root,
    ):
        return True
    manifest_evidence = _repo_manifest_evidence_paths(
        manifest,
        source_repo=source_repo,
        baseline=baseline,
    )
    if any(not _is_closeout_receipt_path(path) for path in manifest_evidence):
        return True
    return False


def _has_work_cap_deviation(
    divergence_reason: str | None,
    deviations: Iterable[str] | None,
) -> bool:
    tokens = [divergence_reason, *(deviations or [])]
    return any(token and deviation_caps_work_at_unverified(token) for token in tokens)


def resolve_work_outcome(
    *,
    degraded_reason: str | None,
    verification: list[Verification] | None,
    files_offgit_produced: Iterable[str] = (),
    artifact_paths: Iterable[str] = (),
    light_bounded_expected_paths: Iterable[str] = (),
    files_expected: list[str] | None = None,
    manifest: EffectsManifest | None,
    source_repo: Path,
    cortex_root: Path,
    baseline: dict[str, Any] | None = None,
    divergence_reason: str | None = None,
    deviations: Iterable[str] | None = None,
    deliverables_expected: bool = False,
) -> WorkOutcome:
    """Grade work truth independently from capture_status (Fork A refined).

    G₁ (todo:success-shaped-silence): no-write intent conjuncts evaluate *before*
    the positive→SHIPPED short-circuit; refusal projects UNVERIFIED (I3 middle),
    not NOT_SHIPPED (reserved for terminal no-run / run_status tokens).
    """
    if degraded_reason and degraded_reason.startswith("run_status="):
        return WorkOutcome.NOT_SHIPPED

    if _has_work_cap_deviation(divergence_reason, deviations):
        return WorkOutcome.UNVERIFIED

    # G₁ conjuncts 1–2 — before positive short-circuit (specimen i launder fix).
    if is_no_write_intent_reason(degraded_reason):
        return WorkOutcome.UNVERIFIED

    positive = positive_deliverable_evidence(
        files_offgit_produced=files_offgit_produced,
        artifact_paths=artifact_paths,
        light_bounded_expected_paths=light_bounded_expected_paths,
        files_expected=files_expected,
        manifest=manifest,
        source_repo=source_repo,
        cortex_root=cortex_root,
        baseline=baseline,
    )
    if verification_all_pass(verification) or positive:
        return WorkOutcome.SHIPPED

    if degraded_reason in NO_RUN_DEGRADED_REASONS:
        return WorkOutcome.NOT_SHIPPED
    if degraded_reason and degraded_reason.startswith("pinned_deliverable_write_failed"):
        return WorkOutcome.UNVERIFIED
    if degraded_reason:
        return WorkOutcome.UNVERIFIED
    if not deliverables_expected:
        return WorkOutcome.SHIPPED
    return WorkOutcome.UNVERIFIED


def project_status_from_work_outcome(
    work_outcome: WorkOutcome,
    degraded_reason: str | None,
) -> CloseoutStatus:
    """Pure status projection from work_outcome — no capture coupling."""
    if degraded_reason and degraded_reason.startswith("run_status="):
        return CloseoutStatus.FAILED
    if degraded_reason in NO_RUN_DEGRADED_REASONS:
        return CloseoutStatus.FAILED
    if work_outcome == WorkOutcome.SHIPPED:
        return CloseoutStatus.COMPLETE
    return CloseoutStatus.PARTIAL


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
    read_only: bool = False,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
    lane: str | None = None,
) -> tuple[CaptureStatus | None, str | None, list[str], EffectsManifest | None]:
    from services.git_integration_worker.cursor_sdk_capture_divergence import (
        apply_surface_cross_checks,
        closeout_divergence_reason,
        expected_deliverables_present,
        observe_read_only_repo_diff_violation,
    )

    if baseline is None and light_bounded_expected_paths:
        probeable_paths = filter_probeable_expected_paths(light_bounded_expected_paths)
        if not probeable_paths:
            all_malformed = _expected_paths_all_malformed_token(
                light_bounded_expected_paths
            )
            deviations: list[str] = [all_malformed]
            if deliverables_expected and manifest and _repo_has_shell_entry(manifest):
                deviations.append("capture:shell_repo_writes_unverified")
            return "unavailable", all_malformed, deviations, manifest
        capture_status, divergence_reason = light_bounded_capture_status(
            probeable_paths,
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
        read_only=read_only,
        lane=lane,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    if read_only and divergence_reason and dispatch_id and thread_id:
        observe_read_only_repo_diff_violation(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            deviation=divergence_reason,
        )
        if capture_status in (None, "complete"):
            capture_status = "partial"
    if (
        divergence_reason
        and capture_status == "complete"
        and deviation_degrades_capture_status(divergence_reason)
    ):
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
        shell_deviation = "capture:shell_repo_writes_unverified"
        deviations.append(shell_deviation)
        if not expected_deliverables_present(
            files_expected,
            manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
        ):
            if divergence_reason is None:
                divergence_reason = shell_deviation
            if capture_status == "complete":
                capture_status = "partial"
    ambient_unattributed, scoped_unattributed = repo_diff_unattributed_deviation(
        change_set=change_set,
        manifest=manifest,
        source_repo=source_repo,
        files_expected=files_expected,
        baseline=baseline,
    )
    if ambient_unattributed:
        deviations.append(ambient_unattributed)
    if scoped_unattributed:
        deviations.append(scoped_unattributed)
        if divergence_reason is None:
            divergence_reason = scoped_unattributed
        if capture_status == "complete":
            capture_status = "partial"
    if outside_repo_paths and not worktree_isolated:
        deviations.append("capture:outside_repo_paths_present")
    if files_untracked_or_ignored:
        deviations.append("capture:gitignored_present_unattributed")
    if divergence_reason:
        deviations.append(divergence_reason)
    if intent_violation:
        deviations.append(intent_violation)
    if (
        deliverables_expected
        and capture_status == "partial"
        and expected_deliverables_present(
            files_expected,
            manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
        )
        and not any_hard_fail_deviation(divergence_reason, *deviations)
    ):
        capture_status = "complete"
    return capture_status, divergence_reason, deviations, manifest
