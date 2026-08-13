"""Regression tests for todo:closeout-deviation-label-truth (AC1–AC8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)
from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_sdk_capture_divergence import (
    closeout_divergence_reason,
)
from services.git_integration_worker.cursor_sdk_capture_policy import (
    DeviationDisposition,
    disposition_for_deviation,
)
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    apply_capture_incompleteness_gate,
    positive_deliverable_evidence,
    resolve_work_outcome,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
    prepare_closeout_delivery,
    stream_only_effect_deviations,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)
from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    extract_instructed_paths,
)
from services.git_integration_worker.cursor_sdk_observed_reconcile import (
    reconcile_observed_vs_committed,
)
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)

pytestmark = pytest.mark.offline


def _cortex_manifest(*, tool: str, identity: str = "todo:probe") -> EffectsManifest:
    return EffectsManifest(
        dispatch_id="7704df",
        thread_id="6655",
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target=identity,
                        identity=identity,
                        detail={"args": {"tool": tool, "entity_id": identity}},
                    )
                ],
            )
        },
    )


def test_ac2_cortex_search_emits_no_reconcile_divergence() -> None:
    manifest = _cortex_manifest(tool="search")
    updated, divergences = reconcile_observed_vs_committed(manifest, ())
    assert divergences == []
    assert updated is not None
    assert not updated.reconciliation


def test_ac2_cortex_entity_get_emits_no_reconcile_divergence() -> None:
    manifest = _cortex_manifest(tool="entity_get")
    _, divergences = reconcile_observed_vs_committed(manifest, ())
    assert divergences == []


def test_ac2_mirror_cortex_assert_still_reconciles() -> None:
    manifest = EffectsManifest(
        dispatch_id="d1",
        thread_id="t1",
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="cortex",
                        target="todo:x",
                        identity="todo:x",
                        detail={"args": {"tool": "assert", "entity_id": "todo:x"}},
                    )
                ],
            )
        },
    )
    _, divergences = reconcile_observed_vs_committed(
        manifest,
        (
            ToolCallObservation(
                call_id="stream-1",
                tool_name="cortex",
                status="completed",
                arg_bytes=10,
                result_bytes=10,
                truncated_fields=(),
            ),
        ),
    )
    assert any("observed_unclaimed" in token for token in divergences)


def test_ac3_bus_turn_absent_skips_latest_symbol() -> None:
    manifest = EffectsManifest(
        dispatch_id="a54cad",
        thread_id="6655",
        surfaces={
            "agent_bus": SurfaceSection(
                surface="agent_bus",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="agent_bus",
                        target="6655#latest",
                        identity="6655#latest",
                        detail={"args": {"op": "get", "turn_number": "latest"}},
                    )
                ],
            )
        },
    )
    reason = closeout_divergence_reason(
        deliverables_expected=True,
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        files_expected=[],
        divergent_rels=(),
        source_repo=Path("/tmp/unused"),
        cortex_root=Path("/tmp/unused-cortex"),
        manifest=manifest,
    )
    assert reason is None or "bus_turn_absent" not in reason


def test_ac3_mirror_numeric_turn_absent_still_emits_when_unconfirmed() -> None:
    """Mirror — numeric turn tokens are not auto-flagged without fetch proof."""
    manifest = EffectsManifest(
        dispatch_id="numeric",
        thread_id="6655",
        surfaces={
            "agent_bus": SurfaceSection(
                surface="agent_bus",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="agent_bus",
                        target="6655#42",
                        identity="6655#42",
                    )
                ],
            )
        },
    )
    reason = closeout_divergence_reason(
        deliverables_expected=True,
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        files_expected=[],
        divergent_rels=(),
        source_repo=Path("/tmp/unused"),
        cortex_root=Path("/tmp/unused-cortex"),
        manifest=manifest,
    )
    assert reason is None


def test_ac4_sdk_git_probe_absent_is_annotate() -> None:
    assert (
        disposition_for_deviation("degraded:sdk_git_probe_absent")
        == DeviationDisposition.ANNOTATE
    )


def test_ac5_stream_only_effect_is_annotate() -> None:
    assert disposition_for_deviation("stream_only_effect") == DeviationDisposition.ANNOTATE
    obs = ToolCallObservation(
        call_id="stream-1",
        tool_name="write",
        status="completed",
        arg_bytes=10,
        result_bytes=10,
        truncated_fields=(),
    )
    assert stream_only_effect_deviations(
        stream_tool_calls=(obs,),
        conversation_tool_call_count=0,
    ) == ("stream_only_effect",)


def test_ac6_positive_offgit_under_unavailable_keeps_shipped(tmp_path: Path) -> None:
    """875e-class — landed cortex artifacts must not demote work_outcome."""
    repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    repo.mkdir()
    rel = "notes/system/reviews/investigate-out.md"
    target = cortex_root / rel
    target.parent.mkdir(parents=True)
    target.write_text("landed\n", encoding="utf-8")
    offgit = [f"cortex://{rel}"]
    work_outcome = resolve_work_outcome(
        degraded_reason=None,
        verification=[],
        files_offgit_produced=offgit,
        artifact_paths=offgit,
        light_bounded_expected_paths=(rel,),
        files_expected=[f"cortex://{rel}"],
        manifest=None,
        source_repo=repo,
        cortex_root=cortex_root,
        deliverables_expected=True,
    )
    assert work_outcome == WorkOutcome.UNVERIFIED
    status, capped = apply_capture_incompleteness_gate(
        status=CloseoutStatus.COMPLETE,
        work_outcome=work_outcome,
        deliverables_expected=True,
        capture_status="unavailable",
        positive_deliverable_evidence=positive_deliverable_evidence(
            files_offgit_produced=offgit,
            artifact_paths=offgit,
            light_bounded_expected_paths=(rel,),
            files_expected=[f"cortex://{rel}"],
            manifest=None,
            source_repo=repo,
            cortex_root=cortex_root,
        ),
    )
    assert capped == WorkOutcome.UNVERIFIED
    assert status == CloseoutStatus.PARTIAL


def test_ac6_mirror_no_positive_evidence_still_caps_shipped() -> None:
    status, capped = apply_capture_incompleteness_gate(
        status=CloseoutStatus.COMPLETE,
        work_outcome=WorkOutcome.SHIPPED,
        deliverables_expected=True,
        capture_status="unavailable",
        positive_deliverable_evidence=False,
    )
    assert capped == WorkOutcome.UNVERIFIED
    assert status == CloseoutStatus.PARTIAL


def test_ac7_emitted_path_absent_skips_observed_read(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    repo.mkdir()
    cortex_root.mkdir()
    read_path = "services/git_integration_worker/cursor_sdk_capture_status.py"
    (repo / read_path).parent.mkdir(parents=True, exist_ok=True)
    (repo / read_path).write_text("# existing\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="read-op",
        thread_id="t1",
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="observed",
                        target=read_path,
                        identity=read_path,
                    )
                ],
            )
        },
    )
    reason = closeout_divergence_reason(
        deliverables_expected=True,
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        files_expected=[],
        divergent_rels=(),
        source_repo=repo,
        cortex_root=cortex_root,
        manifest=manifest,
    )
    assert reason is None


def test_ac7_mirror_write_missing_still_emits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    repo.mkdir()
    cortex_root.mkdir()
    missing = "services/git_integration_worker/missing_module.py"
    manifest = EffectsManifest(
        dispatch_id="write-op",
        thread_id="t1",
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="write",
                        target=missing,
                        identity=missing,
                    )
                ],
            )
        },
    )
    reason = closeout_divergence_reason(
        deliverables_expected=True,
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        files_expected=[],
        divergent_rels=(),
        source_repo=repo,
        cortex_root=cortex_root,
        manifest=manifest,
    )
    assert reason == f"divergence:emitted_path_absent:{missing}"


def test_ac8_prose_citation_not_light_bounded_expected(tmp_path: Path) -> None:
    packet = (
        "TYPE: DIRECTIVE\n"
        "contract: light-bounded\n"
        "scope: inspect routes/cursor_sdk.py:2107-2110\n"
        "out-of-scope: routes/cursor_sdk.py\n"
        "files_expected: cortex://notes/system/reviews/challenge-r2.md\n"
    )
    expected = extract_instructed_paths(packet)
    assert expected == ("notes/system/reviews/challenge-r2.md",)
    repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    repo.mkdir()
    cortex_root.mkdir()
    rel = "notes/system/reviews/challenge-r2.md"
    (cortex_root / rel).parent.mkdir(parents=True)
    (cortex_root / rel).write_text("done\n", encoding="utf-8")
    delivery = prepare_closeout_delivery(
        source_repo=repo,
        dispatch_id="877fe5",
        outcome=SdkRunOutcome(
            body="analysis complete",
            status="finished",
            duration_ms=500,
            tool_call_count=1,
        ),
        degraded_reason=None,
        thread_id="6655",
        work_item_ref="todo:closeout-deviation-label-truth",
        deliverables_expected=True,
        light_bounded_expected_paths=expected,
    )
    payload = json.loads(delivery.body)
    assert not any(
        "light_bounded_path_absent:routes/cursor_sdk.py" in deviation
        for deviation in payload.get("deviations", [])
    )


def test_ac8_mirror_missing_declared_deliverable_still_flags(tmp_path: Path) -> None:
    packet = "files_expected: cortex://notes/system/specs/missing.md\n"
    expected = extract_instructed_paths(packet)
    repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    repo.mkdir()
    cortex_root.mkdir()
    delivery = prepare_closeout_delivery(
        source_repo=repo,
        dispatch_id="missing-deliverable",
        outcome=SdkRunOutcome(
            body="no write",
            status="finished",
            duration_ms=100,
            tool_call_count=0,
        ),
        degraded_reason=None,
        thread_id="6655",
        work_item_ref="todo:closeout-deviation-label-truth",
        deliverables_expected=True,
        light_bounded_expected_paths=expected,
    )
    payload = json.loads(delivery.body)
    assert any(
        "light_bounded_path_absent:notes/system/specs/missing.md" in deviation
        for deviation in payload.get("deviations", [])
    )


def test_ac1_closeout_shipped_with_unavailable_capture_and_offgit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    repo.mkdir()
    rel = "notes/system/specs/truth-fixture.md"
    (cortex_root / rel).parent.mkdir(parents=True)
    (cortex_root / rel).write_text("artifact\n", encoding="utf-8")
    offgit = [f"cortex://{rel}"]
    body = build_implement_closeout_body(
        dispatch_id="ac1-fixture",
        outcome=SdkRunOutcome(
            body="shipped",
            status="finished",
            duration_ms=400,
            tool_call_count=2,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("ac1-fixture"),
        result_bytes=50,
        thread_id="6655",
        work_item_ref="todo:closeout-deviation-label-truth",
        capture_status="unavailable",
        offgit_deliverable_uris=offgit,
        source_repo=repo,
        cortex_root=cortex_root,
        light_bounded_expected_paths=(rel,),
        files_expected=[f"cortex://{rel}"],
        deliverables_expected=True,
    )
    payload = json.loads(body)
    assert payload["work_outcome"] == "unverified"
    assert payload["capture_status"] == "unavailable"
