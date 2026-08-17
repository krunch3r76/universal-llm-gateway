"""Pinned-cortex-deliverable resolution, pin-miss degrade, and sync/async closeout entry points.

``prepare_closeout_delivery`` is the test/sync facade;
``prepare_closeout_delivery_async`` resolves pinned cortex deliverables then
delegates to assembly. ``resolve_cortex_pinned_deliverables`` is imported at
module top so tests can patch ``delivery_prep.resolve_cortex_pinned_deliverables``
(today they patch the monolith module attribute). Pin-miss promotion
(``degraded_reason or pin_reason``) stays exactly as written.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_deliverables import (
    cortex_expected_rels,
    full_result_text,
    resolve_cortex_pinned_deliverables,
)

from .closeout_records import CloseoutDelivery, SdkRunOutcome
from .deliverable_probe import _files_expected_for_pinning
from .delivery_assembly.change_set_resolution import _capture_trees
from .delivery_assembly.orchestration import (
    _assemble_closeout_delivery,
    _assemble_closeout_delivery_async,
)


def prepare_closeout_delivery(
    *,
    source_repo: Path,
    binding: CaptureBinding | None = None,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
    baseline: dict[str, Any] | None = None,
    packet_text: str | None = None,
    cortex_artifact_paths: list[str] | None = None,
    gate_d_created_rels: tuple[str, ...] = (),
    deliverables_expected: bool = False,
    divergent_rels: tuple[str, ...] = (),
    light_bounded_expected_paths: tuple[str, ...] = (),
    execution_id: str = "test-execution",
    post_closeout_sidecar_fn: Callable[..., dict[str, Any] | None] | None = None,
    resolved_model: str | None = None,
) -> CloseoutDelivery:
    """Sync closeout assembly (tests). Production uses ``prepare_closeout_delivery_async``."""
    return _assemble_closeout_delivery(
        source_repo=source_repo,
        binding=binding,
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        thread_id=thread_id,
        work_item_ref=work_item_ref,
        baseline=baseline,
        packet_text=packet_text,
        cortex_artifact_paths=cortex_artifact_paths or [],
        gate_d_created_rels=gate_d_created_rels,
        deliverables_expected=deliverables_expected,
        divergent_rels=divergent_rels,
        light_bounded_expected_paths=light_bounded_expected_paths,
        execution_id=execution_id,
        post_closeout_sidecar_fn=post_closeout_sidecar_fn,
        resolved_model=resolved_model,
    )


async def prepare_closeout_delivery_async(
    *,
    source_repo: Path,
    binding: CaptureBinding | None = None,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
    baseline: dict[str, Any] | None = None,
    packet_text: str | None = None,
    deliverables_expected: bool = False,
    light_bounded_expected_paths: tuple[str, ...] = (),
    execution_id: str,
    extra_deviations: tuple[str, ...] = (),
    post_closeout_sidecar_fn: Callable[..., Any] | None = None,
    worktree_isolated: bool = False,
    resolved_model: str | None = None,
) -> CloseoutDelivery:
    """Write sidecar, resolve pinned cortex deliverables, build closeout JSON."""
    write_tree, _, _ = _capture_trees(source_repo, binding)
    files_expected = _files_expected_for_pinning(
        packet_text,
        deliverables_expected,
        light_bounded_expected_paths,
    )
    text = full_result_text(outcome.body, degraded_reason)
    pinned = await resolve_cortex_pinned_deliverables(
        files_expected=files_expected,
        full_text=text,
        source_repo=write_tree,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    expected_rels = cortex_expected_rels(files_expected)
    gate_d_created = pinned.satisfied_rels
    if len(gate_d_created) < len(expected_rels):
        missing = [r for r in expected_rels if r not in gate_d_created]
        shown = ",".join(missing[:3])
        if len(missing) > 3:
            shown = f"{shown},+{len(missing) - 3}"
        pin_reason = f"pinned_deliverable_write_failed:{shown}"
        degraded_reason = degraded_reason or pin_reason
    return await _assemble_closeout_delivery_async(
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
        cortex_artifact_paths=pinned.uris,
        gate_d_created_rels=gate_d_created,
        deliverables_expected=deliverables_expected,
        divergent_rels=pinned.divergent_rels,
        light_bounded_expected_paths=light_bounded_expected_paths,
        execution_id=execution_id,
        extra_deviations=extra_deviations,
        post_closeout_sidecar_fn=post_closeout_sidecar_fn,
        worktree_isolated=worktree_isolated,
        resolved_model=resolved_model,
    )
