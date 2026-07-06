"""Manifest-first divergence detection for cursor-sdk closeout capture."""

from __future__ import annotations

import re
from pathlib import Path

from implement_admission.closeout_models import EffectsManifest, SurfaceSection
from implement_admission.deliverable_verification import _paths_intersect

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    _normalize_expected_path,
    _normalize_repo_path_for_compare,
    _path_exists_in_sandboxes,
    _repo_manifest_paths,
    canonicalize_capture_path,
    is_no_write_intent_reason,
)

_CORTEX_ENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*:[^/]+$")


def _is_cortex_expected_path(raw: str) -> bool:
    path = raw.strip().lower()
    return path.startswith("cortex://") or path.startswith("cortex:")


def _cortex_expected_rel(raw: str) -> str:
    norm = _normalize_expected_path(raw)
    for prefix in ("cortex://", "cortex:"):
        if norm.lower().startswith(prefix):
            return norm[len(prefix) :].lstrip("/")
    return norm


def expected_deliverables_present(
    files_expected: list[str],
    manifest: EffectsManifest | None,
    *,
    source_repo: Path,
    cortex_root: Path,
) -> bool:
    """Class-D: at least one promised deliverable is present on manifest or disk."""
    if not files_expected:
        return True
    from services.git_integration_worker.cursor_sdk_manifest import manifest_repo_paths

    manifest_paths = manifest_repo_paths(manifest, source_repo=source_repo)
    for raw in files_expected:
        if _is_cortex_expected_path(raw):
            rel = _cortex_expected_rel(raw)
            if rel and (cortex_root / rel).exists():
                return True
            continue
        norm = _normalize_expected_path(raw)
        if norm in manifest_paths or any(
            path == norm or path.endswith(f"/{norm}") for path in manifest_paths
        ):
            return True
        if _path_exists_in_sandboxes(norm, source_repo, cortex_root):
            return True
    return False


def _expected_files_acknowledged(
    files_expected: list[str],
    manifest: EffectsManifest | None,
    *,
    source_repo: Path,
    cortex_root: Path,
) -> bool:
    if not files_expected:
        return True
    from services.git_integration_worker.cursor_sdk_manifest import manifest_repo_paths

    touched: set[str] = set(manifest_repo_paths(manifest, source_repo=source_repo))
    for raw in files_expected:
        if _is_cortex_expected_path(raw):
            rel = _cortex_expected_rel(raw)
            if rel and (cortex_root / rel).exists():
                touched.add(rel)
            continue
        norm = _normalize_expected_path(raw)
        if _path_exists_in_sandboxes(norm, source_repo, cortex_root):
            touched.add(norm)
    return _paths_intersect(files_expected, touched)


def _changed_path_set(
    change_set: ChangeSet,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    paths: set[str] = set()
    for group in (change_set.created, change_set.modified, change_set.deleted):
        for path in group:
            paths.add(_normalize_repo_path_for_compare(path, source_repo=source_repo))
    return paths


def _divergence_from_divergent_rel(rel_entry: str) -> str | None:
    if rel_entry.startswith("pinned_deliverable_wrong_sandbox:"):
        rel = rel_entry.split(":", 1)[1]
        return f"divergence:target_unreadable:{rel}"
    if rel_entry.startswith("pinned_deliverable_write_failed:"):
        rel = rel_entry.split(":", 1)[1]
        return f"divergence:target_unreadable:{rel}"
    return None


def repo_diff_mismatch(
    manifest: EffectsManifest | None,
    git_changed: set[str],
    *,
    source_repo: Path | None = None,
) -> str | None:
    """Legacy git-vs-manifest helper — advisory only; not a capture-status gate."""
    from services.git_integration_worker.cursor_sdk_capture_status import (
        _path_gitignored,
        _repo_has_shell_entry,
    )

    manifest_paths = _repo_manifest_paths(manifest, source_repo=source_repo)
    has_shell = _repo_has_shell_entry(manifest)
    if not manifest_paths and not has_shell:
        return None
    missing = sorted(path for path in manifest_paths if path not in git_changed)
    if not missing:
        return None
    if source_repo is not None:
        for path in missing:
            if (source_repo / path).is_file() and _path_gitignored(source_repo, path):
                continue
            return f"divergence:repo_diff_mismatch:{path}"
        return None
    return f"divergence:repo_diff_mismatch:{missing[0]}"


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


def _fs_surface_cross_check(
    manifest: EffectsManifest,
    *,
    source_repo: Path,
    cortex_root: Path,
    mount_root: Path,
) -> str | None:
    from services.git_integration_worker.cursor_sdk_manifest import (
        classify_mount_path,
        manifest_fs_targets,
        mount_relative_path,
        registered_repo_roots,
        resolve_fs_target_absolute,
    )

    repo_roots = registered_repo_roots(mount_root)
    for target in manifest_fs_targets(manifest):
        abs_path = resolve_fs_target_absolute(
            target,
            mount_root=mount_root,
            cortex_root=cortex_root,
        )
        if abs_path is None:
            continue
        kind = classify_mount_path(
            abs_path,
            source_repo=source_repo,
            mount_root=mount_root,
            repo_roots=repo_roots,
        )
        if kind == "source_repo":
            continue
        if kind == "other_repo":
            rel = mount_relative_path(mount_root, abs_path) or target
            return f"divergence:other_repo_root:{rel}"
        if kind == "shared_cursor":
            rel = mount_relative_path(mount_root, abs_path) or target
            return f"divergence:shared_cursor_parent:{rel}"
        rel = mount_relative_path(mount_root, abs_path) or target
        return f"divergence:unknown_root_child:{rel}"
    return None


def _repo_surface_cross_check(
    *,
    manifest: EffectsManifest,
    git_changed: set[str],
    files_expected: list[str],
    divergent_rels: tuple[str, ...],
    source_repo: Path,
    cortex_root: Path,
    change_set: ChangeSet,
    outside_repo_paths: tuple[str, ...] = (),
    worktree_isolated: bool = False,
    degraded_reason: str | None = None,
) -> str | None:
    del git_changed, change_set
    if worktree_isolated and outside_repo_paths:
        return f"divergence:unknown_root_child:{outside_repo_paths[0]}"
    if files_expected and not _expected_files_acknowledged(
        files_expected,
        manifest,
        source_repo=source_repo,
        cortex_root=cortex_root,
    ):
        return "divergence:no_expected_files_touched"
    for rel_entry in divergent_rels:
        reason = _divergence_from_divergent_rel(rel_entry)
        if reason:
            return reason
    for raw_path in sorted(_repo_manifest_paths(manifest, source_repo=source_repo)):
        if _is_cortex_expected_path(raw_path):
            cortex_rel = _cortex_expected_rel(raw_path)
            if (cortex_root / cortex_rel).exists():
                continue
            return f"divergence:cortex_target_absent:{raw_path}"
        canon = canonicalize_capture_path(raw_path, source_repo=source_repo)
        if canon.scope == "control_plane":
            continue
        if canon.scope == "external_or_unknown":
            if is_no_write_intent_reason(degraded_reason):
                return (
                    "capture:stated_intent_no_write_violation:"
                    f"{canon.original_path}"
                )
            continue
        if not _path_exists_in_sandboxes(
            canon.canonical_path, source_repo, cortex_root
        ):
            return f"divergence:emitted_path_absent:{canon.original_path}"
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
    mount_root: Path | None = None,
    outside_repo_paths: tuple[str, ...] = (),
    worktree_isolated: bool = False,
) -> EffectsManifest | None:
    if manifest is None:
        return None
    from services.git_integration_worker.cursor_sdk_manifest import resolve_mount_root

    mount = (mount_root or resolve_mount_root(source_repo)).resolve()
    git_changed = _changed_path_set(change_set, source_repo=source_repo)
    repo_cross_check: str | None = None
    if deliverables_expected:
        repo_cross_check = _repo_surface_cross_check(
            manifest=manifest,
            git_changed=git_changed,
            files_expected=files_expected,
            divergent_rels=divergent_rels,
            source_repo=source_repo,
            cortex_root=cortex_root,
            change_set=change_set,
            outside_repo_paths=outside_repo_paths,
            worktree_isolated=worktree_isolated,
            degraded_reason=degraded_reason,
        )
    updated: dict[str, object] = {}
    coverage = dict(manifest.coverage)
    for name, section in manifest.surfaces.items():
        cross_check = section.cross_check
        if name == "repo" and repo_cross_check is not None:
            cross_check = cross_check or repo_cross_check
        elif name == "fs" and deliverables_expected and degraded_reason is None:
            cross_check = cross_check or _fs_surface_cross_check(
                manifest,
                source_repo=source_repo,
                cortex_root=cortex_root,
                mount_root=mount,
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
    if repo_cross_check and "repo" not in updated:
        updated["repo"] = SurfaceSection(
            surface="repo",
            source="cross_check",
            entries=[],
            cross_check=repo_cross_check,
        )
        coverage["repo"] = "partial" if repo_cross_check else "complete"
    return manifest.model_copy(
        update={
            "surfaces": updated,
            "coverage": coverage,
        }
    )


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
    mount_root: Path | None = None,
    outside_repo_paths: tuple[str, ...] = (),
    files_untracked_or_ignored: tuple[str, ...] = (),
    worktree_isolated: bool = False,
) -> str | None:
    if not deliverables_expected or degraded_reason is not None:
        return None
    if files_untracked_or_ignored:
        return "divergence:repo_diff_gitignored_present"
    checked = apply_surface_cross_checks(
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
    if checked is None:
        if worktree_isolated and outside_repo_paths:
            return f"divergence:unknown_root_child:{outside_repo_paths[0]}"
        if files_expected and not _expected_files_acknowledged(
            files_expected,
            manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
        ):
            return "divergence:no_expected_files_touched"
        for rel_entry in divergent_rels:
            reason = _divergence_from_divergent_rel(rel_entry)
            if reason:
                return reason
        if manifest is not None:
            for raw_path in sorted(
                _repo_manifest_paths(manifest, source_repo=source_repo)
            ):
                canon = canonicalize_capture_path(raw_path, source_repo=source_repo)
                if canon.scope == "control_plane":
                    continue
                if not _path_exists_in_sandboxes(
                    canon.canonical_path, source_repo, cortex_root
                ):
                    return f"divergence:emitted_path_absent:{canon.original_path}"
        return None
    for name in ("repo", "cortex", "agent_bus", "fs", "rag", "service"):
        section = checked.surfaces.get(name)
        if section and section.cross_check:
            return section.cross_check
    return None
