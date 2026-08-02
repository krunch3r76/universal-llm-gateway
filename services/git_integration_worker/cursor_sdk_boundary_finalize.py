"""Finalize effects_manifest with authority, reconciliation, nested attribution (item 9 inc 2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.cursor_sdk_cortex_identity import (
    merge_stream_cortex_entries,
)
from services.git_integration_worker.cursor_sdk_nested_attribution import (
    fold_nested_boundary_effects,
)
from services.git_integration_worker.cursor_sdk_observed_reconcile import (
    reconcile_observed_vs_committed,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_surface_authority import (
    label_manifest_authority,
)
from services.git_integration_worker.cursor_sdk_toolcall_retention import (
    hydrate_tool_calls_for_boundary_harvest,
)


def finalize_boundary_manifest(
    manifest: EffectsManifest | None,
    *,
    tool_calls: tuple[ToolCallObservation, ...] | None = None,
    source_repo: Path | None = None,
    ledger: Any | None = None,
    parent_dispatch_id: str | None = None,
) -> tuple[EffectsManifest | None, list[str]]:
    """Apply AC-9e/f/g post-capture passes; return manifest + deviation tokens."""
    if manifest is None:
        return None, []
    hydrated_calls = (
        hydrate_tool_calls_for_boundary_harvest(tool_calls) if tool_calls else tool_calls
    )
    if hydrated_calls:
        manifest = merge_stream_cortex_entries(manifest, hydrated_calls) or manifest
    folded = fold_nested_boundary_effects(
        manifest,
        parent_dispatch_id=parent_dispatch_id or manifest.dispatch_id,
        source_repo=source_repo,
        ledger=ledger,
    )
    reconciled, divergences = reconcile_observed_vs_committed(folded, hydrated_calls)
    labeled = label_manifest_authority(reconciled)
    return labeled, divergences


__all__ = ["finalize_boundary_manifest"]
