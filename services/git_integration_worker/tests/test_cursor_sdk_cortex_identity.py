"""Item 18 — cortex identity harvest from boundary responses (AC-18a/b/c)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_boundary_finalize import (
    finalize_boundary_manifest,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_cortex_identity import (
    enrich_cortex_identities_from_stream,
    merge_stream_cortex_entries,
    surfaces_with_request_response_identity_gap,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    build_effects_manifest,
    harvest_cortex_assertion_ids,
)
from services.git_integration_worker.cursor_sdk_nested_attribution import (
    fold_nested_boundary_effects,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_surface_authority import (
    label_manifest_authority,
)

pytestmark = pytest.mark.offline

_LIVE_FALSIFIER_ENTITY = "todo:ac9g-live-falsifier"
_ASSERTION_ID = 27483


def _conversation_cortex_manifest(*, entity: str = _LIVE_FALSIFIER_ENTITY) -> EffectsManifest:
    """Production failure shape: conversation args only, entity_id identity."""
    return EffectsManifest(
        dispatch_id="child-dc37f2f8c11f",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                authority_class="self_reported",
                absence_semantics="absence=unknown",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target=entity,
                        identity=entity,
                        detail={
                            "toolName": "cortex",
                            "args": {
                                "tool": "assert",
                                "entity_id": entity,
                                "claim": "live falsifier",
                                "confidence": "confirmed",
                                "derivation_type": "inference",
                            },
                        },
                    )
                ],
            )
        },
        coverage={"cortex": "complete"},
    )


def _stream_assert_obs(*, entity: str = _LIVE_FALSIFIER_ENTITY) -> ToolCallObservation:
    return ToolCallObservation(
        call_id="stream-cortex-live",
        tool_name="cortex",
        status="completed",
        arg_bytes=100,
        result_bytes=80,
        truncated_fields=(),
        args={
            "toolName": "cortex",
            "args": {
                "tool": "assert",
                "entity_id": entity,
                "claim": "live falsifier",
            },
        },
        result={"status": "success", "value": {"item": {"id": _ASSERTION_ID}}},
    )


def test_ac18a_enrich_patches_entity_identity_to_assertion() -> None:
    manifest = _conversation_cortex_manifest()
    enriched = enrich_cortex_identities_from_stream(manifest, (_stream_assert_obs(),))
    assert enriched is not None
    entry = enriched.surfaces["cortex"].entries[0]
    assert entry.identity == f"assertion:{_ASSERTION_ID}"
    assert entry.detail["identity_harvest_source"] == "boundary_response"
    assert entry.detail["prior_identity"] == _LIVE_FALSIFIER_ENTITY
    assert harvest_cortex_assertion_ids(enriched) == [str(_ASSERTION_ID)]


def test_ac18a_child_closeout_parent_envelope_carries_assertion_id() -> None:
    manifest = _conversation_cortex_manifest()
    merged = merge_stream_cortex_entries(manifest, (_stream_assert_obs(),))
    body = build_implement_closeout_body(
        dispatch_id="child-dc37f2f8c11f",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=1,
            effects_manifest=merged,
            tool_calls=(_stream_assert_obs(),),
        ),
        degraded_reason=None,
        sidecar_ref="workspaces://repo/sidecar.md",
        result_bytes=10,
        thread_id="t1",
        work_item_ref="todo:x",
        effects_manifest=merged,
        capture_status="complete",
    )
    payload = json.loads(body)
    assert payload["evidence_uris"]["cortex_assertions"] == [str(_ASSERTION_ID)]


def test_ac18a_nested_parent_folds_enriched_child_assertion(tmp_path: Path) -> None:
    parent_id = "parent-dispatch"
    child_id = "child-dc37f2f8c11f"
    child_manifest = _conversation_cortex_manifest()
    enriched = merge_stream_cortex_entries(child_manifest, (_stream_assert_obs(),))
    assert enriched is not None
    appendix = json.dumps(enriched.model_dump(mode="json"), indent=2)
    sidecar_dir = tmp_path / "tmp/reviews/closeouts"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / f"{child_id}.md").write_text(
        f"## status\n\ncomplete\n\n## effects_manifest\n\n{appendix}",
        encoding="utf-8",
    )
    parent = EffectsManifest(dispatch_id=parent_id, thread_id="t1")

    class _Ledger:
        @staticmethod
        def list_nested_children(*, parent_dispatch_id: str) -> list[str]:
            assert parent_dispatch_id == parent_id
            return [child_id]

    finalized, _ = finalize_boundary_manifest(
        parent,
        tool_calls=(),
        source_repo=tmp_path,
        ledger=_Ledger(),
        parent_dispatch_id=parent_id,
    )
    assert finalized is not None
    assert harvest_cortex_assertion_ids(finalized) == [str(_ASSERTION_ID)]
    body = build_implement_closeout_body(
        dispatch_id=parent_id,
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=0,
            effects_manifest=finalized,
        ),
        degraded_reason=None,
        sidecar_ref="workspaces://repo/sidecar.md",
        result_bytes=10,
        thread_id="t1",
        work_item_ref="todo:x",
        effects_manifest=finalized,
        capture_status="complete",
    )
    payload = json.loads(body)
    assert payload["evidence_uris"]["cortex_assertions"] == [str(_ASSERTION_ID)]


def test_ac18b_authority_stays_self_reported_after_identity_harvest() -> None:
    manifest = _conversation_cortex_manifest()
    enriched = enrich_cortex_identities_from_stream(manifest, (_stream_assert_obs(),))
    labeled = label_manifest_authority(enriched)
    cortex = labeled.surfaces["cortex"]
    assert cortex.authority_class == "self_reported"
    assert cortex.source == "conversation"
    assert cortex.cross_check == "identity_harvest:boundary_response"


def test_ac18a_finalize_re_applies_stream_merge(tmp_path: Path) -> None:
    """Belt: finalize_boundary_manifest enriches when tool_calls supplied."""
    manifest = build_effects_manifest(
        dispatch_id="parent-dispatch",
        thread_id="t1",
        turns=[
            {
                "turn": {
                    "steps": [
                        {
                            "type": "toolCall",
                            "message": {
                                "type": "mcp",
                                "args": {
                                    "toolName": "cortex",
                                    "args": {
                                        "tool": "assert",
                                        "entity_id": _LIVE_FALSIFIER_ENTITY,
                                    },
                                },
                            },
                        }
                    ]
                }
            }
        ],
    )
    finalized, _ = finalize_boundary_manifest(
        manifest,
        tool_calls=(_stream_assert_obs(),),
        parent_dispatch_id="parent-dispatch",
    )
    assert finalized is not None
    assert harvest_cortex_assertion_ids(finalized) == [str(_ASSERTION_ID)]


def test_ac18c_documents_other_surface_gaps() -> None:
    gaps = surfaces_with_request_response_identity_gap()
    assert "cortex" in gaps
    assert "fs" in gaps
    assert "agent_bus" in gaps
    assert gaps["repo"].startswith("File paths")


def test_ac18a_ac9m_repeat_without_stream_still_unattributed() -> None:
    """Falsifier: conversation-only child sidecar without enrich stays unattributed."""
    manifest = _conversation_cortex_manifest()
    body = build_implement_closeout_body(
        dispatch_id="child-dc37f2f8c11f",
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
    assert payload["evidence_uris"]["cortex_assertions"] is None
    assert "capture:cortex_writes_unattributed" in payload.get("deviations", [])


def test_fold_nested_child_without_assertion_identity(tmp_path: Path) -> None:
    """Pre-fix child sidecar shape — parent cannot harvest without item-18 enrich."""
    parent_id = "parent-dispatch"
    child_id = "child-dc37f2f8c11f"
    child_manifest = _conversation_cortex_manifest()
    appendix = json.dumps(child_manifest.model_dump(mode="json"), indent=2)
    sidecar_dir = tmp_path / "tmp/reviews/closeouts"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / f"{child_id}.md").write_text(
        f"## status\n\ncomplete\n\n## effects_manifest\n\n{appendix}",
        encoding="utf-8",
    )
    folded = fold_nested_boundary_effects(
        EffectsManifest(dispatch_id=parent_id, thread_id="t1"),
        parent_dispatch_id=parent_id,
        source_repo=tmp_path,
        child_dispatch_ids=[child_id],
    )
    assert folded is not None
    assert harvest_cortex_assertion_ids(folded) == []
