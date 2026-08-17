"""Linear closeout assembly sequencers; owns local state threaded between stages.

``_assemble_closeout_delivery`` and ``_assemble_closeout_delivery_async`` keep
their current signatures and docstrings. Bodies become: create
``sidecar_appendix: list[str] = []``, call stage functions in unchanged order,
call ``build_implement_closeout_body`` with that same list, then
``finalize_closeout_receipt``. No import of names from package ``__init__``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding

from .. import cortex_body_sources, implement_body
from ..bus_body_budget import MAX_TURN_BODY_CHARS
from ..closeout_records import CloseoutDelivery, SdkRunOutcome
from . import (
    change_set_resolution,
    deviation_folding,
    lane_settlement,
    receipt_finalization,
    verification_harvest,
)


def _assemble_closeout_delivery(
    *,
    source_repo: Path,
    binding: CaptureBinding | None = None,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
    baseline: dict[str, Any] | None,
    packet_text: str | None,
    files_expected: list[str] | None = None,
    cortex_artifact_paths: list[str],
    gate_d_created_rels: tuple[str, ...],
    deliverables_expected: bool = False,
    divergent_rels: tuple[str, ...] = (),
    light_bounded_expected_paths: tuple[str, ...] = (),
    execution_id: str = "test-execution",
    extra_deviations: tuple[str, ...] = (),
    post_closeout_sidecar_fn: Callable[..., dict[str, Any] | None] | None = None,
    finalize_oversize: bool = True,
    worktree_isolated: bool = False,
    resolved_model: str | None = None,
) -> CloseoutDelivery:
    """Assemble implement closeout delivery.

    Lane-A contract (a:25024): ``worktree_isolated`` defaults False on sole shared
    master. Ambient git/worktree census is visibility-only; never pass
    ``worktree_isolated=True`` here to tolerate parallel WIP — that poisons
    Lane-B isolation semantics. Isolated hard-fail paths activate only when a
    future Lane-B caller explicitly sets ``worktree_isolated=True``.
    """
    sidecar_appendix: list[str] = []
    (
    write_tree, receipt_tree, mount, repo_roots, text, sidecar_path, sidecar_ref,
    result_bytes, files_expected, git_change_set, files_untracked_or_ignored,
    all_outside_repo, baseline_deviations, manifest, boundary_deviations,
    offgit_uris, dropped_non_file_entries, repo_change_set,
    manifest_git_divergence, ambient_movements, verification_cs,
    ) = change_set_resolution.resolve_closeout_change_set(
    source_repo=source_repo,
    binding=binding,
    dispatch_id=dispatch_id,
    outcome=outcome,
    degraded_reason=degraded_reason,
    thread_id=thread_id,
    packet_text=packet_text,
    files_expected=files_expected,
    cortex_artifact_paths=cortex_artifact_paths,
    baseline=baseline,
    gate_d_created_rels=gate_d_created_rels,
    )
    verification, baseline_deviations = verification_harvest.harvest_closeout_verification(
    baseline=baseline,
    verification_cs=verification_cs,
    outcome=outcome,
    sidecar_path=sidecar_path,
    files_expected=files_expected,
    write_tree=write_tree,
    repo_change_set=repo_change_set,
    baseline_deviations=baseline_deviations,
    text=text,
    )
    capture_status, divergence_reason, deviations, manifest = (
    deviation_folding.fold_closeout_deviations(
        deliverables_expected=deliverables_expected,
        baseline=baseline,
        files_expected=files_expected,
        degraded_reason=degraded_reason,
        git_change_set=git_change_set,
        divergent_rels=divergent_rels,
        write_tree=write_tree,
        manifest=manifest,
        all_outside_repo=all_outside_repo,
        files_untracked_or_ignored=files_untracked_or_ignored,
        mount=mount,
        light_bounded_expected_paths=light_bounded_expected_paths,
        worktree_isolated=worktree_isolated,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        binding=binding,
        extra_deviations=extra_deviations,
        baseline_deviations=baseline_deviations,
        outcome=outcome,
        boundary_deviations=boundary_deviations,
        dropped_non_file_entries=dropped_non_file_entries,
        cortex_artifact_paths=cortex_artifact_paths,
        offgit_uris=offgit_uris,
        manifest_git_divergence=manifest_git_divergence,
        ambient_movements=ambient_movements,
    )
    )
    (
    lane_b_lane, lane_b_branch, lane_b_branch_point, capture_head_sha,
    capture_commits_ahead, capture_commits_ahead_unfiltered, capture_landed,
    reported_lane, isolation_mat, escalation_harvest, cortex_authoritative,
    closeout_head, deviations, divergence_reason,
    ) = lane_settlement.settle_lane_and_dispatch_fields(
    binding=binding,
    dispatch_id=dispatch_id,
    write_tree=write_tree,
    receipt_tree=receipt_tree,
    repo_change_set=repo_change_set,
    outcome=outcome,
    deviations=deviations,
    divergence_reason=divergence_reason,
    baseline=baseline,
    files_untracked_or_ignored=files_untracked_or_ignored,
    offgit_uris=offgit_uris,
    thread_id=thread_id,
    gate_d_created_rels=gate_d_created_rels,
    )
    body = implement_body.build_implement_closeout_body(
    dispatch_id=dispatch_id,
    outcome=outcome,
    degraded_reason=degraded_reason,
    sidecar_ref=sidecar_ref,
    result_bytes=result_bytes,
    thread_id=thread_id,
    work_item_ref=work_item_ref,
    change_set=repo_change_set,
    verification=verification,
    cortex_artifact_paths=cortex_artifact_paths,
    capture_status=capture_status,
    divergence_reason=divergence_reason,
    deviations=deviations,
    effects_manifest=manifest,
    sidecar_appendix=sidecar_appendix,
    cortex_first=cortex_authoritative,
    files_untracked_or_ignored=list(files_untracked_or_ignored),
    files_outside_repo=list(all_outside_repo),
    offgit_deliverable_uris=offgit_uris,
    dropped_non_file_entries=dropped_non_file_entries,
    sidecar_markdown=text,
    extra_markdown_sources=cortex_body_sources._markdown_from_cortex_uris(
        list({*(cortex_artifact_paths or []), *offgit_uris})
    ),
    closeout_head=closeout_head,
    files_ambient_repo_movement=ambient_movements,
    source_repo=write_tree,
    cortex_root=cortex_body_sources.cortex_files_root(),
    light_bounded_expected_paths=light_bounded_expected_paths,
    files_expected=files_expected,
    baseline=baseline,
    deliverables_expected=deliverables_expected,
    lane=lane_b_lane if lane_b_lane is not None else reported_lane,
    branch=lane_b_branch,
    branch_point=lane_b_branch_point,
    head_sha=capture_head_sha,
    commits_ahead=capture_commits_ahead,
    commits_ahead_unfiltered=capture_commits_ahead_unfiltered,
    landed=capture_landed,
    isolation_materialized=isolation_mat,
    escalation_harvest=escalation_harvest,
    resolved_model=resolved_model,
    )
    return receipt_finalization.finalize_closeout_receipt(
    source_repo=source_repo,
    lane_b_branch=lane_b_branch,
    thread_id=thread_id,
    dispatch_id=dispatch_id,
    text=text,
    capture_commits_ahead=capture_commits_ahead,
    capture_landed=capture_landed,
    capture_head_sha=capture_head_sha,
    repo_change_set=repo_change_set,
    outcome=outcome,
    resolved_model=resolved_model,
    sidecar_appendix=sidecar_appendix,
    sidecar_path=sidecar_path,
    result_bytes=result_bytes,
    body=body,
    sidecar_ref=sidecar_ref,
    execution_id=execution_id,
    finalize_oversize=finalize_oversize,
    post_closeout_sidecar_fn=post_closeout_sidecar_fn,
    )


async def _assemble_closeout_delivery_async(
    *,
    source_repo: Path,
    binding: CaptureBinding | None = None,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
    baseline: dict[str, Any] | None,
    packet_text: str | None,
    files_expected: list[str] | None = None,
    cortex_artifact_paths: list[str],
    gate_d_created_rels: tuple[str, ...],
    deliverables_expected: bool = False,
    divergent_rels: tuple[str, ...] = (),
    light_bounded_expected_paths: tuple[str, ...] = (),
    execution_id: str,
    extra_deviations: tuple[str, ...] = (),
    post_closeout_sidecar_fn: Callable[..., Any] | None = None,
    worktree_isolated: bool = False,
    resolved_model: str | None = None,
) -> CloseoutDelivery:
    delivery = await asyncio.to_thread(
        _assemble_closeout_delivery,
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        thread_id=thread_id,
        work_item_ref=work_item_ref,
        baseline=baseline,
        packet_text=packet_text,
        files_expected=files_expected,
        cortex_artifact_paths=cortex_artifact_paths,
        gate_d_created_rels=gate_d_created_rels,
        deliverables_expected=deliverables_expected,
        divergent_rels=divergent_rels,
        light_bounded_expected_paths=light_bounded_expected_paths,
        execution_id=execution_id,
        extra_deviations=extra_deviations,
        finalize_oversize=False,
        worktree_isolated=worktree_isolated,
        resolved_model=resolved_model,
    )
    full_body = delivery.body
    if len(full_body) <= MAX_TURN_BODY_CHARS:
        return delivery
    return await receipt_finalization.relocate_oversize_delivery_async(
        delivery,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        execution_id=execution_id,
        post_closeout_sidecar_fn=post_closeout_sidecar_fn,
    )
