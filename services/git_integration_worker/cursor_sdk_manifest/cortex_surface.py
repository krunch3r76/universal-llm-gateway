"""Cortex-surface identity harvest, write-op detection, and stream fold.

Owns assertion-id harvest from manifest identities, write-family detection
used by closeout to distinguish empty-correct from unattributed, and the
stream-observation → ``EffectEntry`` helper that ``cursor_sdk_cortex_identity``
imports by the underscore name. Invariant: ``merge_stream_cortex_entries``
must keep its function-local import of ``cursor_sdk_cortex_identity`` — both
legs of that cycle are lazy today; hoisting either side is an ImportError.
Do not move ``_cortex_entry_from_stream_observation`` out of this module;
``__init__`` re-exports it because ``cortex_identity.py:262`` imports it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from implement_admission.closeout_models import EffectEntry, EffectsManifest

from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_tool_result import (
    assertion_id_from_payload as _assertion_id_from_payload,
)
from services.git_integration_worker.cursor_sdk_tool_result import (
    unwrap_tool_result as _unwrap_tool_result,
)
from services.git_integration_worker.cursor_sdk_toolcall_retention import (
    harvest_result_from_observation,
)

from . import mcp_arguments
from . import surface_taxonomy

def merge_stream_cortex_entries(
    manifest: EffectsManifest | None,
    tool_calls: tuple[ToolCallObservation, ...],
) -> EffectsManifest | None:
    """Fold stream-observed cortex write acks when conversation omitted results (AC-9j/18)."""
    # Cycle-breaker: manifest ⇄ cursor_sdk_cortex_identity; both legs lazy (R1). Do not hoist.
    from services.git_integration_worker.cursor_sdk_cortex_identity import (
        merge_stream_cortex_entries as _merge_stream_cortex_entries,
    )

    return _merge_stream_cortex_entries(manifest, tool_calls)


def _cortex_entry_from_stream_observation(
    obs: ToolCallObservation,
) -> EffectEntry | None:
    if obs.tool_name.lower() not in surface_taxonomy._CORTEX_TOOLS:
        return None
    raw_args = obs.args if isinstance(obs.args, Mapping) else {}
    nested = (
        raw_args.get("args") if isinstance(raw_args.get("args"), Mapping) else raw_args
    )
    effective = mcp_arguments._effective_mcp_args(nested) if nested else {}
    op = _cortex_op_from_args(effective)
    if op not in surface_taxonomy._CORTEX_WRITE_OPS:
        return None
    detail = mcp_arguments._bounded_detail(effective) if effective else None
    assertion_id = _cortex_result_assertion_id(
        obs.tool_name, effective, harvest_result_from_observation(obs)
    )
    identity = (
        f"assertion:{assertion_id}"
        if assertion_id is not None
        else mcp_arguments._mcp_identity(obs.tool_name, effective)
    )
    target = mcp_arguments._mcp_target(obs.tool_name, effective)
    return EffectEntry(
        op=obs.tool_name,
        target=target,
        detail=detail,
        identity=identity,
    )
def harvest_cortex_assertion_ids(manifest: EffectsManifest | None) -> list[str]:
    """Collect deduped assertion ids from cortex-surface manifest entry identities."""
    if manifest is None:
        return []
    section = manifest.surfaces.get("cortex")
    if section is None:
        return []
    ids: set[int] = set()
    for entry in section.entries:
        ident = entry.identity
        if not ident:
            continue
        match = surface_taxonomy._ASSERTION_IDENTITY_RE.match(ident)
        if match:
            ids.add(int(match.group(1)))
    return [str(i) for i in sorted(ids)]


def cortex_surface_has_write_op(manifest: EffectsManifest | None) -> bool:
    """True iff the cortex surface contains >=1 write-family op (assert/supersede/observe/friction).

    Used by the closeout builder to distinguish 'no cortex writes happened'
    (empty list is correct) from 'writes happened but no id was harvestable'
    (None + capture:cortex_writes_unattributed deviation). See
    todo:cursor-sdk-closeout-cortex-assertions-harvest.
    """
    if manifest is None:
        return False
    section = manifest.surfaces.get("cortex")
    if section is None:
        return False
    for entry in section.entries:
        detail = entry.detail or {}
        args = detail.get("args") if isinstance(detail, Mapping) else None
        op = _cortex_op_from_args(args) if isinstance(args, Mapping) else None
        if op in surface_taxonomy._CORTEX_WRITE_OPS:
            return True
    return False


def _cortex_op_from_args(args: Mapping[str, Any]) -> str | None:
    return mcp_arguments._string_arg(args, "tool", "op")


def _cortex_result_assertion_id(
    tool_name: str,
    args: Mapping[str, Any],
    result: object,
) -> int | None:
    if tool_name not in surface_taxonomy._CORTEX_TOOLS:
        return None
    op = _cortex_op_from_args(args)
    if op not in surface_taxonomy._CORTEX_WRITE_OPS:
        return None
    try:
        payload = _unwrap_tool_result(result)
        if payload is None:
            return None
        return _assertion_id_from_payload(payload)
    except (TypeError, ValueError, AttributeError):
        return None
