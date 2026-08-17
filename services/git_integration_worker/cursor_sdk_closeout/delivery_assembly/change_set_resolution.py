"""Capture-tree resolution through seat-write registration for one closeout assembly.

Owns ``_capture_trees``, sidecar write, files_expected defaulting, baseline
reconcile, wrapper/boundary manifest, off-git URI harvest, manifest→repo
change-set, gitignored partition, swamp filter, and
``_register_cursor_sdk_seat_writes``. ``load_config`` and ``SeatWriteLedger``
are module globals — tests patch them on this module after the split.
``capture_wt_baseline`` is called via ``worktree_baseline.capture_wt_baseline``
(module-attribute) so the worktree_baseline monkeypatch seam still fires from
assembly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_boundary_finalize import (
    finalize_boundary_manifest,
)
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    filter_manifest_swamp,
    partition_gitignored_from_change_set,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    full_result_text,
    sidecar_workspaces_ref,
    write_repo_sidecar,
)
from services.git_integration_worker.cursor_sdk_git_head import resolve_git_head
from services.git_integration_worker.cursor_sdk_manifest import (
    manifest_offgit_deliverable_uris,
    merge_wrapper_manifest,
    repo_change_set_from_manifest,
    resolve_mount_root,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    verification_change_set as build_verification_change_set,
)
from services.git_integration_worker.cursor_sdk_repo_precedence import (
    resolve_repo_change_set,
)
from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

from .. import worktree_baseline
from ..closeout_records import SdkRunOutcome
from ..deliverable_probe import _files_expected_from_packet


def _capture_trees(
    source_repo: Path,
    binding: CaptureBinding | None,
) -> tuple[Path, Path, Path]:
    if binding is None:
        return source_repo, source_repo, resolve_mount_root(source_repo)
    return binding.write_tree, binding.receipt_tree, binding.mount_root


def _register_cursor_sdk_seat_writes(
    *,
    dispatch_id: str,
    baseline: dict[str, Any] | None,
    repo_change_set: ChangeSet,
) -> None:
    """Register attributed closeout paths for lane-A cursor-sdk Rank-2 authorship.

    Attach at closeout (not admit) because authored paths are unknown until
    ``resolve_repo_change_set`` completes. Register the attributed set only —
    ambient/parallel-WIP diverted by resolve must not seed the ledger. Arc stays
    open (never ``close_arc`` here) so lane-B quiescent sweep does not commit
    cursor-sdk rows. ``source_repo`` uses the consumer key from ``load_config``
    (matches ``nested_outcome`` relay); Lane-B/worktree binding divergence can
    leave rows unread at a different resolved path.
    """
    if baseline is None:
        return
    paths = (
        *repo_change_set.created,
        *repo_change_set.modified,
        *repo_change_set.deleted,
    )
    if not paths:
        return
    source_repo = str(Path(load_config().source_repo).resolve())
    SeatWriteLedger.instance().register_paths(
        arc_id=dispatch_id,
        seat_id="cursor-sdk",
        source_repo=source_repo,
        paths=paths,
    )


def resolve_closeout_change_set(
    *,
    source_repo: Path,
    binding: CaptureBinding | None,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    packet_text: str | None,
    files_expected: list[str] | None,
    cortex_artifact_paths: list[str],
    baseline: dict[str, Any] | None,
    gate_d_created_rels: tuple[str, ...],
) -> tuple[
    Path,
    Path,
    Path,
    list[Path] | None,
    str,
    Path,
    str,
    int,
    list[str],
    ChangeSet,
    tuple[str, ...],
    tuple[str, ...],
    list[str],
    EffectsManifest | None,
    tuple[str, ...] | list[str],
    list[str],
    list[dict[str, str]] | tuple,
    ChangeSet,
    bool | str | None,
    list | tuple,
    ChangeSet,
]:
    write_tree, receipt_tree, mount = _capture_trees(source_repo, binding)
    repo_roots = list(binding.repo_roots) if binding is not None else None
    text = full_result_text(outcome.body, degraded_reason)
    sidecar_path = write_repo_sidecar(receipt_tree, dispatch_id, text)
    sidecar_ref = sidecar_workspaces_ref(dispatch_id)
    result_bytes = len(text.encode("utf-8"))
    files_expected = (
        files_expected
        if files_expected is not None
        else _files_expected_from_packet(packet_text)
    )
    if baseline is None:
        git_change_set = ChangeSet(created=(), modified=(), deleted=())
        files_untracked_or_ignored: tuple[str, ...] = ()
        outside_repo_paths: tuple[str, ...] = ()
        baseline_deviations: list[str] = []
    else:
        (
            git_change_set,
            files_untracked_or_ignored,
            outside_repo_paths,
            polarity_deviations,
        ) = worktree_baseline.reconcile_workspace_changes(
            source_repo=write_tree,
            baseline=baseline,
            manifest=outcome.effects_manifest,
            mount_root=mount,
            repo_roots=repo_roots,
        )
        baseline_deviations = list(polarity_deviations)
        if "outside_repo" not in baseline:
            baseline_deviations.append("capture:outside_repo_baseline_missing")
    manifest = merge_wrapper_manifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        base=outcome.effects_manifest,
        cortex_artifact_paths=cortex_artifact_paths,
        git_change_set=git_change_set,
    )
    manifest, boundary_deviations = finalize_boundary_manifest(
        manifest,
        tool_calls=outcome.tool_calls,
        source_repo=receipt_tree,
        ledger=CursorDispatchLedger.instance(),
        parent_dispatch_id=dispatch_id,
    )
    offgit_uris = manifest_offgit_deliverable_uris(manifest, sidecar_ref=sidecar_ref)
    manifest_cs, manifest_outside, dropped_non_file_entries = (
        repo_change_set_from_manifest(
            manifest,
            source_repo=write_tree,
            mount_root=mount,
            repo_roots=repo_roots,
        )
    )
    if manifest_cs is None:
        manifest_cs = ChangeSet(created=(), modified=(), deleted=())
    (
        repo_change_set,
        manifest_extra_untracked,
        manifest_git_divergence,
        ambient_movements,
    ) = resolve_repo_change_set(
        manifest=manifest,
        git_change_set=git_change_set,
        source_repo=write_tree,
        mount_root=mount,
        baseline=baseline,
        files_expected=files_expected,
        current_porcelain=worktree_baseline.capture_wt_baseline(write_tree),
        admit_head=(
            baseline.get("admit_head")
            if isinstance(baseline, dict)
            and isinstance(baseline.get("admit_head"), str)
            else None
        ),
        closeout_head=resolve_git_head(write_tree),
        dispatch_id=dispatch_id,
    )
    repo_change_set, files_untracked_or_ignored = partition_gitignored_from_change_set(
        repo_change_set,
        source_repo=write_tree,
        existing_untracked=(*files_untracked_or_ignored, *manifest_extra_untracked),
    )
    repo_change_set = ChangeSet(
        created=tuple(filter_manifest_swamp(repo_change_set.created)),
        modified=tuple(filter_manifest_swamp(repo_change_set.modified)),
        deleted=tuple(filter_manifest_swamp(repo_change_set.deleted)),
    )
    _register_cursor_sdk_seat_writes(
        dispatch_id=dispatch_id,
        baseline=baseline,
        repo_change_set=repo_change_set,
    )
    all_outside_repo = tuple(dict.fromkeys([*outside_repo_paths, *manifest_outside]))
    verification_cs = build_verification_change_set(
        repo_change_set, gate_d_created_rels
    )
    return (
        write_tree, receipt_tree, mount, repo_roots, text, sidecar_path, sidecar_ref,
        result_bytes, files_expected, git_change_set, files_untracked_or_ignored,
        all_outside_repo, baseline_deviations, manifest, boundary_deviations,
        offgit_uris, dropped_non_file_entries, repo_change_set,
        manifest_git_divergence, ambient_movements, verification_cs,
    )
