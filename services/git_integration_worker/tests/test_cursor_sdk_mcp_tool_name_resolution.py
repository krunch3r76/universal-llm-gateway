"""Item 18 / AC-N4 — production stream shape uses wire name ``mcp``, not ``cortex``."""

from __future__ import annotations

import json

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_cortex_identity import (
    assertion_id_from_cortex_observation,
    merge_stream_cortex_entries,
)
from services.git_integration_worker.cursor_sdk_manifest import harvest_cortex_assertion_ids
from services.git_integration_worker.cursor_sdk_observed_reconcile import (
    reconcile_observed_vs_committed,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
    resolve_stream_tool_name,
)

pytestmark = pytest.mark.offline

_LIVE_ENTITY = "todo:ac9g-live-falsifier"
_ASSERTION_ID = 27486


def _production_mcp_cortex_stream_obs(*, call_id: str = "stream-mcp-cortex-1") -> ToolCallObservation:
    """Captured production shape: ``message.name=mcp``, logical tool in ``args.toolName``."""
    return ToolCallObservation(
        call_id=call_id,
        tool_name=resolve_stream_tool_name(
            "mcp",
            {
                "providerIdentifier": "user-vortex",
                "toolName": "cortex",
                "args": {
                    "tool": "assert",
                    "arguments": json.dumps(
                        {
                            "entity_id": _LIVE_ENTITY,
                            "claim": "AC-18a-LIVE-6 nested child assertion item-18 attempt 6",
                            "confidence": "confirmed",
                            "derivation_type": "inference",
                        }
                    ),
                },
            },
        ),
        status="completed",
        arg_bytes=500,
        result_bytes=3208,
        truncated_fields=(),
        args={
            "providerIdentifier": "user-vortex",
            "toolName": "cortex",
            "args": {
                "tool": "assert",
                "arguments": json.dumps(
                    {
                        "entity_id": _LIVE_ENTITY,
                        "claim": "AC-18a-LIVE-6 nested child assertion item-18 attempt 6",
                        "confidence": "confirmed",
                        "derivation_type": "inference",
                    }
                ),
            },
        },
        result={"status": "success", "value": {"item": {"id": _ASSERTION_ID}}},
    )


def test_resolve_stream_tool_name_unwraps_mcp_wire_name() -> None:
    assert (
        resolve_stream_tool_name(
            "mcp",
            {"toolName": "cortex", "args": {"tool": "assert"}},
        )
        == "cortex"
    )


def test_resolve_stream_tool_name_passthrough_non_mcp() -> None:
    assert resolve_stream_tool_name("Shell", {"command": "ls"}) == "Shell"
    assert resolve_stream_tool_name("fs", {"op": "read"}) == "fs"


def test_ac_n4_production_shape_enriches_assertion_id() -> None:
    obs = _production_mcp_cortex_stream_obs()
    assert obs.tool_name == "cortex"
    assert assertion_id_from_cortex_observation(obs) == _ASSERTION_ID


def test_ac_n4_production_shape_merges_stream_cortex_entry() -> None:
    manifest = EffectsManifest(
        dispatch_id="child-live-6",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target=_LIVE_ENTITY,
                        identity=_LIVE_ENTITY,
                    )
                ],
            )
        },
    )
    merged = merge_stream_cortex_entries(manifest, (_production_mcp_cortex_stream_obs(),))
    assert merged is not None
    assert harvest_cortex_assertion_ids(merged) == [str(_ASSERTION_ID)]
    entry = merged.surfaces["cortex"].entries[0]
    assert entry.identity == f"assertion:{_ASSERTION_ID}"


def test_ac_j3_production_shape_clears_both_divergence_lines() -> None:
    """After enrich+reconcile join, seat and boundary keys must clear together (AC-J3)."""
    manifest = EffectsManifest(
        dispatch_id="child-live-7",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target=_LIVE_ENTITY,
                        identity=_LIVE_ENTITY,
                    )
                ],
            )
        },
    )
    call_id = "tool_a57a9066-82f0-43d1-b626-bdc3452edc6"
    obs = _production_mcp_cortex_stream_obs(call_id=call_id)
    obs = obs.__class__(
        **{
            **obs.__dict__,
            "result": {"status": "success", "value": {"item": {"id": 27487}}},
        }
    )
    merged = merge_stream_cortex_entries(manifest, (obs,))
    assert merged is not None
    entry = merged.surfaces["cortex"].entries[0]
    assert entry.identity == "assertion:27487"
    _, divs = reconcile_observed_vs_committed(merged, (obs,))
    assert divs == []


def test_ac_j2_child_closeout_envelope_lists_assertion_id() -> None:
    from services.git_integration_worker.cursor_sdk_closeout import (
        SdkRunOutcome,
        build_implement_closeout_body,
    )

    manifest = EffectsManifest(
        dispatch_id="child-live-7",
        thread_id="t1",
        capture_sources=["conversation", "stream"],
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target=_LIVE_ENTITY,
                        identity="assertion:27487",
                    )
                ],
            )
        },
    )
    body = build_implement_closeout_body(
        dispatch_id="child-live-7",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        sidecar_ref="workspaces://repo/sidecar.md",
        result_bytes=10,
        thread_id="t1",
        work_item_ref="todo:x",
        effects_manifest=manifest,
        capture_status="complete",
    )
    payload = json.loads(body)
    assert payload["evidence_uris"]["cortex_assertions"] == ["27487"]


def test_ac_n3_reconcile_retires_phantom_seat_claimed_unobserved() -> None:
    """Mis-keyed ``tool_name=mcp`` caused phantom ``seat_claimed_unobserved`` divergences."""
    manifest = EffectsManifest(
        dispatch_id="child-live-6",
        thread_id="t1",
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target=_LIVE_ENTITY,
                        identity=_LIVE_ENTITY,
                    )
                ],
            )
        },
    )
    pre_fix_obs = ToolCallObservation(
        call_id="stream-mcp-cortex-1",
        tool_name="mcp",
        status="completed",
        arg_bytes=500,
        result_bytes=3208,
        truncated_fields=(),
        args={"toolName": "cortex"},
        result={"status": "success", "value": {"item": {"id": _ASSERTION_ID}}},
    )
    _, pre_divs = reconcile_observed_vs_committed(manifest, (pre_fix_obs,))
    assert any("seat_claimed_unobserved" in d for d in pre_divs)

    post_fix_obs = _production_mcp_cortex_stream_obs()
    merged = merge_stream_cortex_entries(manifest, (post_fix_obs,))
    assert merged is not None
    _, post_divs = reconcile_observed_vs_committed(merged, (post_fix_obs,))
    assert post_divs == []
