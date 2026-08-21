"""Capture-field resolution and deviation merge: caller-token-first, stream/boundary/degraded, OOB cortex, manifest/git, ambient.

``cortex_files_root`` is reached only via ``cortex_body_sources.cortex_files_root()``
(two call sites in this stage). Caller-supplied tokens lead the deviations list
so oversize truncation cannot drop a gate-bypass finding (order is correctness).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_ambient import ambient_deviation_token
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    resolve_closeout_capture_fields,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    collect_cortex_impersonation_scan_paths,
    collect_expected_cortex_deliverable_uris,
    oob_cortex_write_findings,
)
from services.git_integration_worker.cursor_sdk_manifest.cortex_uri_salvage import (
    CORTEX_URI_SALVAGED_DEVIATION,
    salvage_cortex_host_path_impersonations,
)

from .. import cortex_body_sources
from ..closeout_records import SdkRunOutcome


def fold_closeout_deviations(
    *,
    deliverables_expected: bool,
    baseline: dict[str, Any] | None,
    files_expected: list[str],
    degraded_reason: str | None,
    git_change_set: ChangeSet,
    divergent_rels: tuple[str, ...],
    write_tree: Path,
    manifest: EffectsManifest | None,
    all_outside_repo: tuple[str, ...],
    files_untracked_or_ignored: tuple[str, ...],
    mount: Path,
    light_bounded_expected_paths: tuple[str, ...],
    worktree_isolated: bool,
    dispatch_id: str,
    thread_id: str,
    binding: CaptureBinding | None,
    extra_deviations: tuple[str, ...],
    baseline_deviations: list[str],
    outcome: SdkRunOutcome,
    boundary_deviations: object,
    dropped_non_file_entries: object,
    cortex_artifact_paths: list[str],
    offgit_uris: object,
    manifest_git_divergence: object,
    ambient_movements: object,
) -> tuple[str | None, str | None, list[str], EffectsManifest | None]:
    """Return (capture_status, divergence_reason, deviations, manifest)."""
    capture_status, divergence_reason, deviations, manifest = (
        resolve_closeout_capture_fields(
            deliverables_expected=deliverables_expected,
            baseline=baseline,
            files_expected=files_expected,
            degraded_reason=degraded_reason,
            change_set=git_change_set,
            divergent_rels=divergent_rels,
            source_repo=write_tree,
            cortex_root=cortex_body_sources.cortex_files_root(),
            manifest=manifest,
            outside_repo_paths=all_outside_repo,
            files_untracked_or_ignored=files_untracked_or_ignored,
            mount_root=mount,
            light_bounded_expected_paths=light_bounded_expected_paths,
            worktree_isolated=worktree_isolated,
            read_only=CursorDispatchLedger.instance().read_read_only(
                dispatch_id=dispatch_id
            ),
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            lane=binding.lane if binding is not None else None,
        )
    )
    # Caller-supplied tokens lead: oversize bodies keep only the first few
    # deviations, and a gate-bypass finding must not be the entry that is dropped.
    deviations = [
        *extra_deviations,
        *baseline_deviations,
        *(
            d
            for d in (deviations or [])
            if d not in extra_deviations
            and not str(d).startswith(
                "divergence:repo_diff_paths_unattributed:ambient:"
            )
        ),
    ]
    if outcome.stream_only_deviations:
        deviations = [
            *deviations,
            *(d for d in outcome.stream_only_deviations if d not in deviations),
        ]
    if boundary_deviations:
        deviations = [
            *deviations,
            *(d for d in boundary_deviations if d not in deviations),
        ]
    for reason in outcome.degraded_reasons:
        token = f"degraded:{reason}"
        if token not in deviations and reason not in deviations:
            deviations.append(token)
    if dropped_non_file_entries:
        deviations = [*(deviations or []), "capture:non_file_manifest_entry_dropped"]
    expected_cortex_uris = collect_expected_cortex_deliverable_uris(
        light_bounded_expected_paths=light_bounded_expected_paths,
        files_expected=files_expected,
        cortex_artifact_paths=cortex_artifact_paths,
    )
    oob_deviations, oob_divergence = oob_cortex_write_findings(
        expected_cortex_uris=expected_cortex_uris,
        offgit_uris=offgit_uris,
        cortex_root=cortex_body_sources.cortex_files_root(),
    )
    impersonation_paths = collect_cortex_impersonation_scan_paths(
        all_outside_repo,
        files_expected,
        dropped_non_file_entries,
        offgit_uris,
    )
    salvaged_uris, _remaining = salvage_cortex_host_path_impersonations(
        impersonation_paths,
        cortex_root=cortex_body_sources.cortex_files_root(),
        mount_root=mount,
        write_tree=write_tree,
    )
    if salvaged_uris and CORTEX_URI_SALVAGED_DEVIATION not in deviations:
        deviations = [*deviations, CORTEX_URI_SALVAGED_DEVIATION]
    if oob_deviations:
        deviations = [*deviations, *oob_deviations]
        if capture_status == "complete":
            capture_status = "partial"
        if divergence_reason is None:
            divergence_reason = oob_divergence
    if (
        manifest_git_divergence
        and "divergence:manifest_vs_git_labels" not in deviations
    ):
        deviations = [*(deviations or []), "divergence:manifest_vs_git_labels"]
        if divergence_reason is None:
            divergence_reason = "divergence:manifest_vs_git_labels"
    ambient_token = ambient_deviation_token(ambient_movements)
    if ambient_token and ambient_token not in deviations:
        deviations = [*(deviations or []), ambient_token]
    return capture_status, divergence_reason, deviations, manifest
