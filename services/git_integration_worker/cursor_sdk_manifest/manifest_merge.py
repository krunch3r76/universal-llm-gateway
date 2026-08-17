"""Folds stream, artifact, and wrapper effects into an existing manifest.

Owns no-code-change detection (empty git + plumbing-only surfaces), repo-path
append, stream tool-call fold, artifact-path fold, wrapper-effect synthesis,
and wrapper merge including AC-9j preserve-on-no-code-change. Ceiling-adjacent
(~235 projected SLOC): land as one file; nested ``manifest_merge/wrapper_fold.py``
only if scan reports >300 (R6). Invariant: when git is genuinely empty, do not
collapse nested/cortex/fs/bus/rag/subagents surfaces
(``_PRESERVE_ON_NO_CODE_CHANGE_SURFACES``). Depends on ``surface_taxonomy``,
``repo_projection``, ``cortex_surface``, and ``manifest_build``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)

from . import cortex_surface, manifest_build, repo_projection, surface_taxonomy


def _git_change_set_empty(change_set: ChangeSet) -> bool:
    return not (change_set.created or change_set.modified or change_set.deleted)


def _manifest_declares_runtime_surface(base: EffectsManifest | None) -> bool:
    if base is None:
        return False
    if repo_projection.manifest_repo_write_paths(base):
        return True
    repo_section = base.surfaces.get("repo")
    if repo_section and any(
        entry.op == surface_taxonomy._REPO_SHELL_OP for entry in repo_section.entries
    ):
        return True
    for name, section in base.surfaces.items():
        if name in surface_taxonomy._PLUMBING_SURFACES or name == "repo":
            continue
        if section.entries:
            return True
    return False


def is_genuinely_no_code_change(
    *,
    git_change_set: ChangeSet,
    base: EffectsManifest | None,
) -> bool:
    """True when baseline diff is empty and effects are cortex/bus/service plumbing only."""
    if not _git_change_set_empty(git_change_set):
        return False
    return not _manifest_declares_runtime_surface(base)
def merge_repo_paths_into_manifest(
    manifest: EffectsManifest | None,
    paths: Iterable[str],
    *,
    source_repo: Path | None = None,
    source_label: str = "stream",
    op: str = "observed",
) -> EffectsManifest | None:
    """Append repo entries for paths not already present in the manifest."""
    normalized: list[str] = []
    existing = repo_projection.manifest_repo_paths(manifest, source_repo=source_repo)
    for raw in paths:
        path = repo_projection._normalize_repo_path(raw, repo_root=source_repo)
        if not path or path in existing:
            continue
        normalized.append(path)
        existing.add(path)
    if not normalized:
        return manifest
    new_entries = [
        EffectEntry(op=op, target=path, identity=path) for path in normalized
    ]
    if manifest is None:
        return EffectsManifest(
            dispatch_id="",
            thread_id="",
            capture_sources=[source_label],
            surfaces={
                "repo": SurfaceSection(
                    surface="repo",
                    source=source_label,
                    entries=new_entries,
                )
            },
            coverage={"repo": "complete"},
        )
    repo_section = manifest.surfaces.get("repo")
    if repo_section is None:
        merged_surfaces = dict(manifest.surfaces)
        merged_surfaces["repo"] = SurfaceSection(
            surface="repo",
            source=source_label,
            entries=new_entries,
        )
    else:
        merged_surfaces = dict(manifest.surfaces)
        merged_surfaces["repo"] = SurfaceSection(
            surface="repo",
            source=repo_section.source,
            entries=[*repo_section.entries, *new_entries],
            cross_check=repo_section.cross_check,
        )
    sources = list(dict.fromkeys([*manifest.capture_sources, source_label]))
    coverage = dict(manifest.coverage)
    coverage["repo"] = coverage.get("repo", "complete")
    return manifest.model_copy(
        update={
            "surfaces": merged_surfaces,
            "capture_sources": sources,
            "coverage": coverage,
        }
    )


def merge_stream_tool_calls(
    manifest: EffectsManifest | None,
    tool_calls: tuple[ToolCallObservation, ...],
    *,
    source_repo: Path | None = None,
) -> EffectsManifest | None:
    """Fold stream-observed write paths missing from the conversation manifest."""
    paths = [tc.target_path for tc in tool_calls if tc.target_path]
    merged = merge_repo_paths_into_manifest(
        manifest,
        paths,
        source_repo=source_repo,
        source_label="stream",
    )
    return cortex_surface.merge_stream_cortex_entries(merged, tool_calls)
def merge_artifact_paths(
    manifest: EffectsManifest | None,
    artifact_paths: Iterable[str],
    *,
    source_repo: Path | None = None,
) -> EffectsManifest | None:
    """Fold ``list_artifacts()`` paths into the repo surface when non-empty."""
    return merge_repo_paths_into_manifest(
        manifest,
        artifact_paths,
        source_repo=source_repo,
        source_label="artifacts",
    )
def wrapper_effects_for_closeout(
    *,
    thread_id: str,
    dispatch_id: str,
    cortex_artifact_paths: list[str],
) -> dict[str, list[EffectEntry]]:
    effects: dict[str, list[EffectEntry]] = {
        "agent_bus": [
            EffectEntry(
                op="agent_bus.reply",
                target=thread_id,
                identity=f"{thread_id}#closeout",
            )
        ],
        "service": [
            EffectEntry(
                op="emit_implement_closeout_trigger",
                target=dispatch_id,
                identity=dispatch_id,
            )
        ],
    }
    if cortex_artifact_paths:
        effects["cortex"] = [
            EffectEntry(op="cortex.write", target=path, identity=path)
            for path in cortex_artifact_paths
        ]
    return effects


def merge_wrapper_manifest(
    *,
    dispatch_id: str,
    thread_id: str,
    base: EffectsManifest | None,
    cortex_artifact_paths: list[str],
    git_change_set: ChangeSet | None = None,
) -> EffectsManifest:
    empty_git = ChangeSet(created=(), modified=(), deleted=())
    resolved_git = git_change_set if git_change_set is not None else empty_git
    if is_genuinely_no_code_change(git_change_set=resolved_git, base=base):
        sources = list(base.capture_sources) if base else []
        sources = list(dict.fromkeys([*sources, "wrapper"]))
        preserved: dict[str, SurfaceSection] = {}
        preserved_coverage: dict[str, str] = {}
        if base is not None:
            for name, section in base.surfaces.items():
                if name in surface_taxonomy._PRESERVE_ON_NO_CODE_CHANGE_SURFACES:
                    preserved[name] = section
                    if name in base.coverage:
                        preserved_coverage[name] = base.coverage[name]
        return EffectsManifest(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            capture_sources=sources,
            surfaces=preserved,
            coverage=preserved_coverage,
            contract=base.contract if base is not None else None,
        )
    wrapper = manifest_build.build_effects_manifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        turns=(),
        capture_branch="B",
        contract=base.contract if base is not None else None,
        wrapper_effects=wrapper_effects_for_closeout(
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            cortex_artifact_paths=cortex_artifact_paths,
        ),
    )
    if base is None:
        return wrapper
    merged_surfaces = dict(base.surfaces)
    for name, section in wrapper.surfaces.items():
        existing = merged_surfaces.get(name)
        if existing is None:
            merged_surfaces[name] = section
            continue
        merged_surfaces[name] = SurfaceSection(
            surface=name,
            source=existing.source,
            entries=[*existing.entries, *section.entries],
            cross_check=existing.cross_check,
        )
    sources = list(dict.fromkeys([*base.capture_sources, *wrapper.capture_sources]))
    return base.model_copy(
        update={"surfaces": merged_surfaces, "capture_sources": sources}
    )
