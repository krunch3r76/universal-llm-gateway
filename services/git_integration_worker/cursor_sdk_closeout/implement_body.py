"""ImplementCloseout JSON turn-body construction: status gate chain plus payload render.

``build_implement_closeout_body`` owns the status/work_outcome gate chain
(capture incompleteness, escalation harvest, authored reconcile, lane-B land
incompleteness) and the nested ``_render_body`` closure that dumps the
pydantic payload. ``sidecar_appendix`` is mutated in place on the oversize
manifest path (``list.append`` of the full manifest JSON) — callers must pass
the same list object they later write to the sidecar suffix. Function-local
imports for land-discipline, status polarity, and disagreement refresh stay
function-local. SLOC above the 300 new-file ceiling is an accepted waiver.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from implement_admission.closeout_models import (
    AmbientRepoMovement,
    EffectsManifest,
    EvidenceUris,
    ImplementCloseout,
    Verification,
)
from implement_admission.evidence_verify import hash_artifact_path
from implement_admission.propagation_row import (
    land_paths_for_propagation,
    resolve_code_ref,
)
from implement_admission.spec import WorkOutcome

from services.git_integration_worker.cursor_auto.episode_residue import (
    residue_actions,
    resolve_propagation_for_finalize,
)
from services.git_integration_worker.cursor_sdk_authored_status_reconcile import (
    reconcile_structured_with_authored,
)
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    apply_capture_incompleteness_gate,
    apply_escalation_harvest_gate,
    attribution_effects_paths,
    positive_deliverable_evidence,
    project_status_from_work_outcome,
    resolve_work_outcome,
    verification_has_failure,
)
from services.git_integration_worker.cursor_sdk_closeout_seal import (
    seal_closeout_payload,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    artifact_paths_for_closeout,
)
from services.git_integration_worker.cursor_sdk_git_head import (
    observed_lane_git_refs,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    compact_manifest_for_body,
    cortex_surface_has_write_op,
    harvest_cortex_assertion_ids,
    serialize_effects_manifest_for_body,
)
from services.git_integration_worker.cursor_sdk_subagent_capture import (
    ensure_subagents_surface,
)
from services.git_integration_worker.cursor_sdk_usage_sidecar import (
    stamp_usage_model_label,
)

from .bus_body_budget import MAX_TURN_BODY_CHARS
from .closeout_records import SdkRunOutcome
from .degraded_reasons import _map_closeout_status


def build_implement_closeout_body(
    *,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    sidecar_ref: str,
    result_bytes: int,
    thread_id: str,
    work_item_ref: str | None,
    change_set: ChangeSet | None = None,
    verification: list[Verification] | None = None,
    cortex_artifact_paths: list[str] | None = None,
    capture_status: str | None = None,
    divergence_reason: str | None = None,
    deviations: list[str] | None = None,
    effects_manifest: EffectsManifest | None = None,
    sidecar_appendix: list[str] | None = None,
    cortex_first: bool = False,
    files_untracked_or_ignored: list[str] | None = None,
    files_outside_repo: list[str] | None = None,
    offgit_deliverable_uris: list[str] | None = None,
    dropped_non_file_entries: list[dict[str, str]] | None = None,
    sidecar_markdown: str | None = None,
    extra_markdown_sources: list[str] | None = None,
    closeout_head: str | None = None,
    files_ambient_repo_movement: list[AmbientRepoMovement] | None = None,
    work_outcome: WorkOutcome | None = None,
    source_repo: Path | None = None,
    cortex_root: Path | None = None,
    light_bounded_expected_paths: tuple[str, ...] = (),
    files_expected: list[str] | None = None,
    baseline: dict[str, Any] | None = None,
    deliverables_expected: bool = False,
    lane: str | None = None,
    branch: str | None = None,
    branch_point: str | None = None,
    head_sha: str | None = None,
    commits_ahead: int | None = None,
    commits_ahead_unfiltered: int | None = None,
    landed: bool | None = None,
    isolation_materialized: bool | None = None,
    escalation_harvest: str | None = "none",
    resolved_model: str | None = None,
) -> str:
    """Build a compact, valid ImplementCloseout JSON turn body.

    The full Composer result lives in the sidecar; this body carries only the
    minimal closeout fields Stargate's trigger_closeout_from_turn requires plus a
    one-line metadata summary. Always small (< 500 chars).

    ``source_ref`` is the canonical work-item ref (the lifecycle target); the
    sidecar is evidence, not identity, so it lands in ``evidence_uris``. When no
    work-item ref exists (message dispatch) ``sidecar_ref`` is the required-field
    fallback — Stargate /closeout then rejects it as unresolvable (a deliberate
    no-op; nothing to reconcile).
    """
    duration_s = outcome.duration_ms / 1000.0
    summary = (
        f"dispatch {dispatch_id}: {outcome.tool_call_count} tool calls, "
        f"{duration_s:.1f}s, {result_bytes}B -> sidecar"
    )
    if degraded_reason:
        summary = f"{summary} (degraded: {degraded_reason})"
    if offgit_deliverable_uris:
        summary = (
            f"{summary}; off-git deliverables: {len(offgit_deliverable_uris)} "
            f"({offgit_deliverable_uris[0]})"
        )
    status = _map_closeout_status(degraded_reason)
    artifact_paths = artifact_paths_for_closeout(
        sidecar_ref,
        cortex_artifact_paths or [],
        cortex_first=cortex_first,
        offgit_deliverable_uris=offgit_deliverable_uris or [],
    )
    resolved_work_outcome = work_outcome
    if (
        resolved_work_outcome is None
        and source_repo is not None
        and cortex_root is not None
    ):
        resolved_work_outcome = resolve_work_outcome(
            degraded_reason=degraded_reason,
            verification=verification,
            files_offgit_produced=offgit_deliverable_uris or [],
            artifact_paths=artifact_paths,
            light_bounded_expected_paths=light_bounded_expected_paths,
            files_expected=files_expected,
            manifest=effects_manifest or outcome.effects_manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
            baseline=baseline,
            divergence_reason=divergence_reason,
            deviations=deviations,
            deliverables_expected=deliverables_expected,
        )
    elif (
        resolved_work_outcome is None
        and verification
        and verification_has_failure(verification)
    ):
        resolved_work_outcome = WorkOutcome.CHECKS_FAILED
        status = project_status_from_work_outcome(
            resolved_work_outcome, degraded_reason
        )
    if resolved_work_outcome is not None:
        status = project_status_from_work_outcome(
            resolved_work_outcome, degraded_reason
        )
    positive_evidence = False
    if (
        source_repo is not None
        and cortex_root is not None
        and deliverables_expected
    ):
        positive_evidence = positive_deliverable_evidence(
            files_offgit_produced=offgit_deliverable_uris or [],
            artifact_paths=artifact_paths,
            light_bounded_expected_paths=light_bounded_expected_paths,
            files_expected=files_expected or [],
            manifest=effects_manifest or outcome.effects_manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
            baseline=baseline,
        )
    status, resolved_work_outcome = apply_capture_incompleteness_gate(
        status=status,
        work_outcome=resolved_work_outcome,
        deliverables_expected=deliverables_expected,
        capture_status=capture_status,
        positive_deliverable_evidence=positive_evidence,
    )
    status, resolved_work_outcome = apply_escalation_harvest_gate(
        status=status,
        work_outcome=resolved_work_outcome,
        escalation_harvest=escalation_harvest,
    )
    status, resolved_work_outcome, status_authority_disagreement, deviations = (
        reconcile_structured_with_authored(
            status=status,
            work_outcome=resolved_work_outcome,
            sidecar_markdown=sidecar_markdown,
            deviations=deviations,
            deliverables_expected=deliverables_expected,
        )
    )
    from services.git_integration_worker.cursor_sdk_land_discipline import (
        apply_lane_b_land_incompleteness,
    )

    pre_lane_b_status = status
    status, deviations = apply_lane_b_land_incompleteness(
        status,
        lane=lane,
        landed=landed,
        commits_ahead=commits_ahead,
        deviations=deviations,
    )
    from services.git_integration_worker.cursor_auto.closeout_status_polarity import (
        classify_status_incomplete_class,
    )

    status_incomplete_class = classify_status_incomplete_class(
        status=status,
        work_outcome=resolved_work_outcome,
        capture_status=capture_status,
        escalation_harvest=escalation_harvest,
        deviations=deviations,
        degraded_reason=degraded_reason,
    )
    if status_authority_disagreement is not None and status != pre_lane_b_status:
        from services.git_integration_worker.cursor_sdk_authored_status_reconcile import (
            refresh_disagreement_after_machine_gate,
        )

        status_authority_disagreement = refresh_disagreement_after_machine_gate(
            disagreement=status_authority_disagreement,
            post_gate_status=status,
            post_gate_work_outcome=resolved_work_outcome,
            pre_gate_status=pre_lane_b_status,
            status_incomplete_class=status_incomplete_class,
        )
    manifest_source = ensure_subagents_surface(
        effects_manifest or outcome.effects_manifest
    )
    manifest_payload = serialize_effects_manifest_for_body(
        manifest_source,
        sidecar_appendix=sidecar_appendix,
    )
    cortex_assertions = harvest_cortex_assertion_ids(manifest_source)
    cortex_section = manifest_source.surfaces.get("cortex") if manifest_source else None
    cortex_self_reported = (
        cortex_section is not None and cortex_section.authority_class == "self_reported"
    )
    cortex_writes_unattributed = (
        cortex_self_reported
        and not cortex_assertions
        and cortex_surface_has_write_op(manifest_source)
    )
    if cortex_writes_unattributed:
        cortex_assertions = None
        deviations = [*(deviations or []), "capture:cortex_writes_unattributed"]
    repo_files = change_set or ChangeSet(created=(), modified=(), deleted=())

    land_paths = land_paths_for_propagation(
        created=repo_files.created,
        modified=repo_files.modified,
        untracked=files_untracked_or_ignored or (),
    )
    propagation_residue = list(residue_actions(land_paths))
    markdown_sources = [*(extra_markdown_sources or [])]
    if sidecar_markdown and sidecar_markdown.strip():
        markdown_sources.append(sidecar_markdown)
    code_probe: dict[str, Any] = {}
    lane_git_refs: list[str] = []
    if source_repo is not None:
        admit_head = None
        if isinstance(baseline, dict):
            raw_admit = baseline.get("admit_head")
            if isinstance(raw_admit, str) and raw_admit.strip():
                admit_head = raw_admit.strip()
        lane_git_refs = observed_lane_git_refs(
            source_repo,
            dispatch_id=dispatch_id,
            admit_head=admit_head,
            closeout_head=closeout_head,
        )
    if lane_git_refs:
        code_probe = {"evidence_uris": {"git_refs": lane_git_refs}}
    elif outcome.sdk_git and isinstance(outcome.sdk_git, dict):
        head = outcome.sdk_git.get("HEAD") or outcome.sdk_git.get("head")
        if isinstance(head, str) and head.strip():
            code_probe = {"evidence_uris": {"git_refs": [head.strip()]}}
    if closeout_head:
        code_probe = {**code_probe, "closeout_head": closeout_head}
    propagation_rows = resolve_propagation_for_finalize(
        residue_paths=land_paths,
        markdown_sources=markdown_sources,
        code_ref=resolve_code_ref(code_probe),
    )

    def _render_body(
        manifest_value: EffectsManifest | dict[str, Any] | None,
    ) -> str:
        closeout = ImplementCloseout(
            status=status,
            work_outcome=resolved_work_outcome,
            escalation_harvest=escalation_harvest or "none",
            summary=summary,
            source_ref=work_item_ref or sidecar_ref,
            files_created=list(repo_files.created),
            files_modified=list(repo_files.modified),
            files_deleted=list(repo_files.deleted),
            files_ambient_repo_movement=list(files_ambient_repo_movement or []),
            capture_status=capture_status,
            effects_manifest=manifest_value
            if isinstance(manifest_value, EffectsManifest)
            else None,
            deviations=deviations or [],
            verification=verification or [],
            evidence_uris=EvidenceUris(
                artifact_paths=artifact_paths,
                bus_threads=[thread_id],
                dispatch_ids=[dispatch_id],
                cortex_assertions=cortex_assertions,
                git_refs=lane_git_refs
                or (
                    code_probe.get("evidence_uris", {}).get("git_refs", [])
                    if isinstance(code_probe.get("evidence_uris"), dict)
                    else []
                ),
            ),
            propagation_residue=propagation_residue,
            propagation=list(propagation_rows),
            usage=stamp_usage_model_label(outcome.usage, resolved_model),
            usage_capture_status=outcome.usage_capture_status,  # type: ignore[arg-type]
        )
        payload = closeout.model_dump(mode="json")
        if degraded_reason:
            payload["degraded_reason"] = degraded_reason
        payload["tool_call_count"] = outcome.tool_call_count
        if status_authority_disagreement is not None:
            payload["status_authority_disagreement"] = status_authority_disagreement
        if status_incomplete_class is not None:
            payload["status_incomplete_class"] = status_incomplete_class
        if files_untracked_or_ignored:
            payload["files_untracked_or_ignored"] = files_untracked_or_ignored
        if files_outside_repo:
            payload["files_outside_repo"] = files_outside_repo
        if offgit_deliverable_uris:
            payload["files_offgit_produced"] = offgit_deliverable_uris
        if dropped_non_file_entries:
            payload["dropped_non_file_entries"] = dropped_non_file_entries
        if manifest_value is not None and not isinstance(
            manifest_value, EffectsManifest
        ):
            payload["effects_manifest"] = manifest_value
        if lane is not None:
            payload["lane"] = lane
        if isolation_materialized is not None:
            payload["isolation_materialized"] = isolation_materialized
        if branch is not None:
            payload["branch"] = branch
        if branch_point is not None:
            payload["branch_point"] = branch_point
        if head_sha is not None:
            payload["head_sha"] = head_sha
        if commits_ahead is not None:
            payload["commits_ahead"] = commits_ahead
        else:
            # Schema model_dump may emit null; absence must travel as a missing
            # key so the plane classifier does not see a present measurement.
            payload.pop("commits_ahead", None)
        if commits_ahead_unfiltered is not None:
            payload["commits_ahead_unfiltered"] = commits_ahead_unfiltered
        else:
            payload.pop("commits_ahead_unfiltered", None)
        if landed is not None:
            payload["landed"] = landed
        else:
            payload.pop("landed", None)
        effects = attribution_effects_paths(
            created=repo_files.created,
            modified=repo_files.modified,
            deleted=repo_files.deleted,
            files_untracked_or_ignored=files_untracked_or_ignored or [],
        )
        # §4.7 / a:26354 — off-git cortex URIs belong in effects so operator
        # disposition via closeout schema sees durable writes even when the repo ChangeSet is empty.
        merged_effects: list[str] = list(effects)
        seen_effects = set(merged_effects)
        for uri in offgit_deliverable_uris or []:
            if uri and uri not in seen_effects:
                seen_effects.add(uri)
                merged_effects.append(uri)
        payload["effects"] = merged_effects
        tracked_empty = not (
            repo_files.created or repo_files.modified or repo_files.deleted
        )
        if tracked_empty and merged_effects:
            payload["summary"] = (
                f"{payload['summary']}; {len(merged_effects)} path(s) touched "
                "(untracked/gitignored/off-git)"
            )

        evidence = payload.get("evidence_uris")
        if isinstance(evidence, dict):
            paths = evidence.get("artifact_paths") or []
            published = dict(evidence.get("artifact_digests") or {})
            for path in paths:
                if not isinstance(path, str) or path in published:
                    continue
                digest = hash_artifact_path(path)
                if digest is not None:
                    published[path] = digest
            evidence["artifact_digests"] = published
        seal_closeout_payload(payload)
        return json.dumps(payload, separators=(",", ":"))

    body = _render_body(manifest_payload)
    if len(body) > MAX_TURN_BODY_CHARS and manifest_source is not None:
        compact_payload = compact_manifest_for_body(manifest_source)
        if sidecar_appendix is not None and not any(
            line.startswith("{") for line in sidecar_appendix
        ):
            sidecar_appendix.append(
                json.dumps(manifest_source.model_dump(mode="json"), indent=2)
            )
        body = _render_body(compact_payload)
    return body
