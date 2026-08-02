"""Item 9 nested attribution fixes — AC-9j/k/l/n production-shape coverage."""

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
from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    build_effects_manifest,
    harvest_cortex_assertion_ids,
    merge_stream_cortex_entries,
    merge_wrapper_manifest,
    serialize_effects_manifest_for_body,
)
from services.git_integration_worker.cursor_sdk_nested_attribution import (
    _child_manifest_from_sidecar,
    fold_nested_boundary_effects,
)
from services.git_integration_worker.cursor_sdk_stream_capture import ToolCallObservation

pytestmark = pytest.mark.offline


def _cortex_only_manifest(*, assertion_id: int = 4242) -> EffectsManifest:
    return EffectsManifest(
        dispatch_id="child-dispatch",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target="todo:fold-test",
                        identity=f"assertion:{assertion_id}",
                    )
                ],
            )
        },
        coverage={"cortex": "complete"},
    )


def test_ac9j_merge_wrapper_preserves_cortex_on_no_code_change() -> None:
    """Production orphan: cortex-only child lost manifest in merge_wrapper collapse."""
    base = _cortex_only_manifest(assertion_id=27469)
    merged = merge_wrapper_manifest(
        dispatch_id="child-dispatch",
        thread_id="t1",
        base=base,
        cortex_artifact_paths=[],
        git_change_set=ChangeSet(created=(), modified=(), deleted=()),
    )
    assert "cortex" in merged.surfaces
    assert harvest_cortex_assertion_ids(merged) == ["27469"]


def test_ac9j_closeout_child_envelope_lists_cortex_assertions() -> None:
    manifest = _cortex_only_manifest(assertion_id=27469)
    body = build_implement_closeout_body(
        dispatch_id="child-dispatch",
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
    assert payload["evidence_uris"]["cortex_assertions"] == ["27469"]


def test_ac9j_stream_cortex_merge_when_conversation_omits_result() -> None:
    manifest = EffectsManifest(dispatch_id="d1", thread_id="t1")
    tool_calls = (
        ToolCallObservation(
            call_id="stream-cortex-1",
            tool_name="cortex",
            status="completed",
            arg_bytes=10,
            result_bytes=50,
            truncated_fields=(),
            args={
                "toolName": "cortex",
                "args": {"tool": "assert", "entity_id": "todo:foo"},
            },
            result={"status": "success", "value": {"item": {"id": 27469}}},
        ),
    )
    merged = merge_stream_cortex_entries(manifest, tool_calls)
    assert merged is not None
    assert harvest_cortex_assertion_ids(merged) == ["27469"]


def test_ac9k_sidecar_appendix_always_emitted() -> None:
    manifest = _cortex_only_manifest()
    appendix: list[str] = []
    serialize_effects_manifest_for_body(manifest, sidecar_appendix=appendix)
    assert len(appendix) == 1
    parsed = json.loads(appendix[0])
    assert parsed["surfaces"]["cortex"]["entries"][0]["identity"] == "assertion:4242"


def test_ac9k_production_markdown_sidecar_manifest_parse(tmp_path: Path) -> None:
    """Fixture uses raw JSON; production emits §2 prose + ## effects_manifest appendix."""
    child_id = "auto-child-prod-shape"
    manifest = _cortex_only_manifest()
    appendix = json.dumps(manifest.model_dump(mode="json"), indent=2)
    sidecar_text = (
        "## status\n\ncomplete\n\n"
        "## ac_verdict\n\nAC pass.\n\n"
        f"## effects_manifest\n\n{appendix}"
    )
    sidecar_dir = tmp_path / "tmp/reviews/closeouts"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / f"{child_id}.md").write_text(sidecar_text, encoding="utf-8")
    child_manifest = _child_manifest_from_sidecar(tmp_path, child_id)
    assert child_manifest is not None
    assert harvest_cortex_assertion_ids(child_manifest) == ["4242"]


def test_ac9l_old_like_pattern_misses_compact_json() -> None:
    parent = "parent-dispatch"
    compact = json.dumps({"nest_under": parent}, separators=(",", ":"))
    assert compact == f'{{"nest_under":"{parent}"}}'
    assert f'"nest_under": "{parent}"' not in compact


def test_ac9m_parent_envelope_folds_child_assertion_production_sidecar(
    tmp_path: Path,
) -> None:
    parent_id = "parent-dispatch"
    child_id = "child-dispatch"
    child_manifest = _cortex_only_manifest(assertion_id=27469)
    appendix = json.dumps(child_manifest.model_dump(mode="json"), indent=2)
    sidecar_text = f"## status\n\ncomplete\n\n## effects_manifest\n\n{appendix}"
    sidecar_dir = tmp_path / "tmp/reviews/closeouts"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / f"{child_id}.md").write_text(sidecar_text, encoding="utf-8")

    parent = EffectsManifest(dispatch_id=parent_id, thread_id="t1")
    folded = fold_nested_boundary_effects(
        parent,
        parent_dispatch_id=parent_id,
        source_repo=tmp_path,
        child_dispatch_ids=[child_id],
    )
    assert folded is not None
    entries = folded.surfaces["cortex"].entries
    assert len(entries) == 1
    assert entries[0].identity == "assertion:27469"
    assert entries[0].detail["attributed_dispatch_id"] == parent_id
    assert entries[0].detail["origin_dispatch_id"] == child_id

    body = build_implement_closeout_body(
        dispatch_id=parent_id,
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=0,
            effects_manifest=folded,
        ),
        degraded_reason=None,
        sidecar_ref="workspaces://repo/sidecar.md",
        result_bytes=10,
        thread_id="t1",
        work_item_ref="todo:x",
        effects_manifest=folded,
        capture_status="complete",
    )
    payload = json.loads(body)
    assert payload["evidence_uris"]["cortex_assertions"] == ["27469"]


def test_ac9n_fixture_vs_production_sidecar_shape_gap(tmp_path: Path) -> None:
    """AC-9n: unit test used raw JSON sidecar; production uses markdown + appendix."""
    child_id = "child-dispatch"
    child_manifest = _cortex_only_manifest()
    fixture_shape = json.dumps({"effects_manifest": child_manifest.model_dump(mode="json")})
    production_shape = (
        "## status\n\ncomplete\n\n## effects_manifest\n\n"
        + json.dumps(child_manifest.model_dump(mode="json"), indent=2)
    )
    sidecar_dir = tmp_path / "tmp/reviews/closeouts"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / f"{child_id}-fixture.md").write_text(fixture_shape, encoding="utf-8")
    (sidecar_dir / f"{child_id}-prod.md").write_text(production_shape, encoding="utf-8")

    class _OldParser:
        @staticmethod
        def parse(text: str) -> EffectsManifest | None:
            start = text.find("{")
            if start < 0:
                return None
            payload = json.loads(text[start:])
            raw = payload.get("effects_manifest")
            if not isinstance(raw, dict):
                return None
            return EffectsManifest.model_validate(raw)

    assert _OldParser.parse(fixture_shape) is not None
    assert _OldParser.parse(production_shape) is None
    assert _child_manifest_from_sidecar(tmp_path, f"{child_id}-prod") is not None


def test_finalize_boundary_manifest_end_to_end_production_shape(tmp_path: Path) -> None:
    parent_id = "parent-final"
    child_id = "child-final"
    child_manifest = _cortex_only_manifest(assertion_id=99)
    sidecar_dir = tmp_path / "tmp/reviews/closeouts"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / f"{child_id}.md").write_text(
        f"## status\n\ncomplete\n\n## effects_manifest\n\n"
        + json.dumps(child_manifest.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    parent = build_effects_manifest(
        dispatch_id=parent_id,
        thread_id="t1",
        turns=[],
    )
    folded = fold_nested_boundary_effects(
        parent,
        parent_dispatch_id=parent_id,
        source_repo=tmp_path,
        child_dispatch_ids=[child_id],
    )
    finalized, _ = finalize_boundary_manifest(
        folded,
        tool_calls=(),
        source_repo=tmp_path,
        parent_dispatch_id=parent_id,
    )
    assert finalized is not None
    cortex = finalized.surfaces.get("cortex")
    assert cortex is not None
    assert any(e.identity == "assertion:99" for e in cortex.entries)
