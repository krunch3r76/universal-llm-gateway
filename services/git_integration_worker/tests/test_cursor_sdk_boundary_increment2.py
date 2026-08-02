"""Increment 2 tests — authority labels, reconciliation, nested attribution (item 9)."""

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
from services.git_integration_worker.cursor_sdk_manifest import build_effects_manifest
from services.git_integration_worker.cursor_sdk_nested_attribution import (
    fold_nested_boundary_effects,
)
from services.git_integration_worker.cursor_sdk_observed_reconcile import (
    reconcile_observed_vs_committed,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_subagent_capture import (
    SUBAGENTS_SURFACE,
    ensure_subagents_surface,
)
from services.git_integration_worker.cursor_sdk_surface_authority import (
    label_manifest_authority,
)

pytestmark = pytest.mark.offline


def test_ac9e_subagents_empty_carries_observed_zero_semantics() -> None:
    manifest = build_effects_manifest(
        dispatch_id="parent-1",
        thread_id="thread-1",
        turns=[],
    )
    manifest = ensure_subagents_surface(manifest)
    labeled = label_manifest_authority(manifest)
    section = labeled.surfaces[SUBAGENTS_SURFACE]
    assert section.authority_class == "observed"
    assert section.absence_semantics == "absence=zero"
    assert section.entries == []


def test_ac9e_conversation_repo_is_self_reported_unknown() -> None:
    turns = [
        {
            "turn": {
                "steps": [
                    {
                        "type": "toolCall",
                        "message": {
                            "type": "write",
                            "args": {"path": "services/x.py"},
                        },
                    }
                ]
            }
        }
    ]
    manifest = build_effects_manifest(
        dispatch_id="d1",
        thread_id="t1",
        turns=turns,
        capture_branch="B",
    )
    labeled = label_manifest_authority(manifest)
    repo = labeled.surfaces["repo"]
    assert repo.authority_class == "self_reported"
    assert repo.absence_semantics == "absence=unknown"


def test_ac9f_emits_bidirectional_divergence() -> None:
    manifest = EffectsManifest(
        dispatch_id="d1",
        thread_id="t1",
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(op="cortex", target="todo:x", identity="todo:x"),
                    EffectEntry(
                        op="cortex",
                        target="assertion:99",
                        identity="assertion:99",
                    ),
                ],
            )
        },
    )
    tool_calls = (
        ToolCallObservation(
            call_id="stream-cortex-1",
            tool_name="cortex",
            status="completed",
            arg_bytes=10,
            result_bytes=10,
            truncated_fields=(),
        ),
        ToolCallObservation(
            call_id="stream-cortex-2",
            tool_name="cortex",
            status="completed",
            arg_bytes=10,
            result_bytes=10,
            truncated_fields=(),
        ),
    )
    updated, divergences = reconcile_observed_vs_committed(manifest, tool_calls)
    assert updated is not None
    assert updated.reconciliation
    recon = updated.reconciliation[0]
    assert "todo:x" in recon.seat_claimed_unobserved
    assert any("stream-cortex" in item for item in recon.observed_unclaimed)
    assert any("seat_claimed_unobserved" in d for d in divergences)
    assert any("observed_unclaimed" in d for d in divergences)


def test_ac9g_folds_child_cortex_under_parent_dispatch_id(tmp_path: Path) -> None:
    parent_id = "parent-dispatch"
    child_id = "child-dispatch"
    child_payload = {
        "effects_manifest": {
            "schema_version": 1,
            "dispatch_id": child_id,
            "thread_id": "t1",
            "capture_sources": ["conversation"],
            "surfaces": {
                "cortex": {
                    "surface": "cortex",
                    "source": "conversation",
                    "entries": [
                        {
                            "op": "cortex",
                            "target": "todo:fold-test",
                            "identity": "assertion:4242",
                        }
                    ],
                }
            },
            "coverage": {},
            "external_effects": "scoped_out",
            "reconciliation": [],
        }
    }
    sidecar_dir = tmp_path / "tmp/reviews/closeouts"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / f"{child_id}.md").write_text(
        json.dumps(child_payload), encoding="utf-8"
    )
    parent = EffectsManifest(dispatch_id=parent_id, thread_id="t1")
    folded = fold_nested_boundary_effects(
        parent,
        parent_dispatch_id=parent_id,
        source_repo=tmp_path,
        child_dispatch_ids=[child_id],
    )
    assert folded is not None
    assert folded.dispatch_id == parent_id
    entries = folded.surfaces["cortex"].entries
    assert len(entries) == 1
    assert entries[0].identity == "assertion:4242"
    assert entries[0].detail["attributed_dispatch_id"] == parent_id
    assert entries[0].detail["origin_dispatch_id"] == child_id


def test_ac9c_anti_fabrication_only_on_self_reported_surface() -> None:
    """Ledger-attested empty cortex list is not a fabrication defect."""
    manifest = EffectsManifest(
        dispatch_id="d1",
        thread_id="t1",
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="ledger",
                entries=[],
                authority_class="ledger_attested",
                absence_semantics="absence=zero",
            )
        },
    )
    body = build_implement_closeout_body(
        dispatch_id="d1",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=0,
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
    assert payload["evidence_uris"]["cortex_assertions"] == []
    assert "capture:cortex_writes_unattributed" not in payload.get("deviations", [])


def test_finalize_boundary_manifest_composes_all_passes() -> None:
    manifest = build_effects_manifest(
        dispatch_id="parent-final",
        thread_id="t1",
        turns=[],
    )
    finalized, _divs = finalize_boundary_manifest(
        manifest,
        tool_calls=(),
        parent_dispatch_id="parent-final",
    )
    assert finalized is not None
    assert SUBAGENTS_SURFACE in finalized.surfaces
    assert finalized.surfaces[SUBAGENTS_SURFACE].authority_class is not None
    assert finalized.surfaces[SUBAGENTS_SURFACE].absence_semantics is not None
