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
from typing import Any, Literal

from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_closeout_lint_unestablished,
)

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
    land_text: str | None = None,
    files_expected: list[str] | None = None,
    cortex_artifact_paths: list[str],
    gate_d_created_rels: tuple[str, ...],
    deliverables_expected: bool = False,
    divergent_rels: tuple[str, ...] = (),
    residual_expected_paths: tuple[str, ...] = (),
    execution_id: str = "test-execution",
    extra_deviations: tuple[str, ...] = (),
    post_closeout_sidecar_fn: Callable[..., dict[str, Any] | None] | None = None,
    finalize_oversize: bool = True,
    worktree_isolated: bool = False,
    resolved_model: str | None = None,
    sdk_transcript_ref: str | None = None,
    sdk_transcript_sha256: str | None = None,
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
        write_tree,
        receipt_tree,
        mount,
        repo_roots,
        text,
        sidecar_path,
        archived_closeout_body,
        sidecar_ref,
        result_bytes,
        files_expected,
        git_change_set,
        files_untracked_or_ignored,
        all_outside_repo,
        baseline_deviations,
        manifest,
        boundary_deviations,
        offgit_uris,
        dropped_non_file_entries,
        repo_change_set,
        manifest_git_divergence,
        ambient_movements,
        verification_cs,
        range_corroboration,
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
    verification, baseline_deviations = (
        verification_harvest.harvest_closeout_verification(
            baseline=baseline,
            verification_cs=verification_cs,
            outcome=outcome,
            sidecar_path=sidecar_path,
            files_expected=files_expected,
            write_tree=write_tree,
            repo_change_set=repo_change_set,
            baseline_deviations=baseline_deviations,
            text=text,
            range_corroboration=range_corroboration,
        )
    )
    if "verification:lint_set_unestablished" in baseline_deviations:
        emit_sdk_closeout_lint_unestablished(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            authority="repo_change_set",
            projection=range_corroboration,
            recovery="unobserved",
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
            residual_expected_paths=residual_expected_paths,
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
        lane_b_lane,
        lane_b_branch,
        lane_b_branch_point,
        capture_head_sha,
        capture_commits_ahead,
        capture_commits_ahead_unfiltered,
        capture_landed,
        reported_lane,
        isolation_mat,
        escalation_harvest,
        cortex_authoritative,
        closeout_head,
        deviations,
        divergence_reason,
        landed_resolution_reason,
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
        files_outside_repo=all_outside_repo,
        packet_text=packet_text,
        land_text=land_text,
        closeout_text=text,
    )
    # Receipt prose is not the settlement input. Project land claims onto the
    # sidecar after the ancestry probe, and leave ``text`` for discharge so a
    # published line cannot change the branch outcome.
    if lane_b_lane == "B":
        from services.git_integration_worker.cursor_sdk_deliverables_expected import (
            git_land_plane_uncomputable,
        )
        from services.git_integration_worker.relay.closeout_plane_probe import (
            project_receipt_land_claims,
            rewrite_receipt_preamble_file,
        )

        meter_presence: Literal["absent", "present"] = (
            "present" if capture_commits_ahead is not None else "absent"
        )
        plane_uncomputable = git_land_plane_uncomputable(
            created=repo_change_set.created,
            modified=repo_change_set.modified,
            deleted=repo_change_set.deleted,
            untracked=files_untracked_or_ignored,
            offgit=offgit_uris,
        )
        rewrite_receipt_preamble_file(
            sidecar_path,
            repo=receipt_tree,
            sha=capture_head_sha,
            branch=lane_b_branch,
            commits_ahead=capture_commits_ahead,
            commits_ahead_presence=meter_presence,
            git_land_plane_uncomputable=plane_uncomputable,
        )
        if archived_closeout_body is not None:
            archived_closeout_body = project_receipt_land_claims(
                archived_closeout_body,
                repo=receipt_tree,
                sha=capture_head_sha,
                branch=lane_b_branch,
                commits_ahead=capture_commits_ahead,
                commits_ahead_presence=meter_presence,
                git_land_plane_uncomputable=plane_uncomputable,
            )
    import json

    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_mode import sdk_mode_for_dispatch
    from services.git_integration_worker.cursor_sdk_native_helper_policy import (
        emit_native_helper_breaches_for_harvest,
        harvest_native_helper_attempts,
        load_native_helper_policy_from_record,
    )
    from services.git_integration_worker.cursor_sdk_run_budget_meter import (
        read_run_budget_meter,
    )
    from services.git_integration_worker.cursor_sdk_run_limits import (
        classify_stop_cause,
        read_run_limits,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        ledger_row = conn.execute(
            "SELECT record_json, status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    record_json = str(ledger_row["record_json"] or "") if ledger_row else ""
    park_blob: dict[str, Any] = {}
    try:
        rec_data = json.loads(record_json) if record_json else {}
        if isinstance(rec_data, dict):
            raw_park = rec_data.get("park")
            if isinstance(raw_park, dict):
                park_blob = raw_park
    except json.JSONDecodeError:
        park_blob = {}
    budget_reading = read_run_budget_meter(record_json)
    park_reason = str(park_blob.get("reason") or "") if isinstance(park_blob, dict) else None
    stop_cause = classify_stop_cause(
        park_reason=park_reason or None,
        run_status=str(outcome.status or ""),
        budget_exhausted=budget_reading.budget_exhausted,
        executor_voluntary=not budget_reading.budget_exhausted and not park_reason,
    )
    limits = read_run_limits(record_json)
    run_limits_echo = limits.to_record() if limits is not None else None
    native_helper_enforcement = limits.native_helper_enforcement if limits else None
    native_helper_attempts = harvest_native_helper_attempts(dispatch_id)
    native_helper_policy = load_native_helper_policy_from_record(
        record_json,
        packet_text=packet_text or "",
    )
    emit_native_helper_breaches_for_harvest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        policy=native_helper_policy,
        attempts=native_helper_attempts,
        manifest=manifest,
    )
    resolved_sdk_mode = sdk_mode_for_dispatch(dispatch_id)
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
        residual_expected_paths=residual_expected_paths,
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
        landed_resolution_reason=landed_resolution_reason,
        isolation_materialized=isolation_mat,
        escalation_harvest=escalation_harvest,
        resolved_model=resolved_model,
        sdk_mode=resolved_sdk_mode,
        packet_text=packet_text,
        hub_repo=binding.receipt_tree if binding is not None else source_repo,
        sdk_transcript_ref=sdk_transcript_ref,
        sdk_transcript_sha256=sdk_transcript_sha256,
        stop_cause=stop_cause,
        run_limits=run_limits_echo,
        native_helper_enforcement=native_helper_enforcement,
        native_helper_attempts=native_helper_attempts,
    )
    return receipt_finalization.finalize_closeout_receipt(
        source_repo=write_tree,
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
        archived_closeout_body=archived_closeout_body,
        result_bytes=result_bytes,
        body=body,
        sidecar_ref=sidecar_ref,
        execution_id=execution_id,
        finalize_oversize=finalize_oversize,
        post_closeout_sidecar_fn=post_closeout_sidecar_fn,
        packet_text=packet_text,
        land_text=land_text,
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
    land_text: str | None = None,
    files_expected: list[str] | None = None,
    cortex_artifact_paths: list[str],
    gate_d_created_rels: tuple[str, ...],
    deliverables_expected: bool = False,
    divergent_rels: tuple[str, ...] = (),
    residual_expected_paths: tuple[str, ...] = (),
    execution_id: str,
    extra_deviations: tuple[str, ...] = (),
    post_closeout_sidecar_fn: Callable[..., Any] | None = None,
    worktree_isolated: bool = False,
    resolved_model: str | None = None,
    sdk_transcript_ref: str | None = None,
    sdk_transcript_sha256: str | None = None,
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
        land_text=land_text,
        files_expected=files_expected,
        cortex_artifact_paths=cortex_artifact_paths,
        gate_d_created_rels=gate_d_created_rels,
        deliverables_expected=deliverables_expected,
        divergent_rels=divergent_rels,
        residual_expected_paths=residual_expected_paths,
        execution_id=execution_id,
        extra_deviations=extra_deviations,
        finalize_oversize=False,
        worktree_isolated=worktree_isolated,
        resolved_model=resolved_model,
        sdk_transcript_ref=sdk_transcript_ref,
        sdk_transcript_sha256=sdk_transcript_sha256,
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
