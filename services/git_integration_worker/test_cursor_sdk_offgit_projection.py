"""Tests for off-git deliverable projection in cursor-sdk closeout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    resolve_closeout_capture_fields,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
    finalize_closeout_body,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_deliverable_truth import (
    light_bounded_deliverable_reason,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)
from services.git_integration_worker.cursor_sdk_light_bounded_capture import (
    extract_instructed_paths,
    fs_write_landed,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    _entry_from_tool_call,
    build_effects_manifest,
    collect_expected_cortex_deliverable_uris,
    manifest_fs_targets,
    manifest_offgit_deliverable_uris,
    oob_cortex_write_findings,
    repo_change_set_from_manifest,
)

pytestmark = pytest.mark.offline


def _fs_manifest(
    *,
    op: str = "write",
    sandbox: str = "cortex",
    path: str,
    target: str | None = None,
) -> EffectsManifest:
    detail = {"op": op, "sandbox": sandbox, "path": path}
    identity = target or f"{sandbox}:{path}"
    return EffectsManifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        surfaces={
            "fs": SurfaceSection(
                surface="fs",
                source="test",
                entries=[
                    EffectEntry(
                        op="fs",
                        target=identity,
                        identity=identity,
                        detail=detail,
                    )
                ],
            )
        },
    )


def _shell_manifest() -> EffectsManifest:
    return EffectsManifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="test",
                entries=[EffectEntry(op="shell", target="echo hi", identity="echo hi")],
            )
        },
    )


def test_ac1_anchor_class_fixture_complete_with_offgit_projection(
    tmp_path: Path,
) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    deliverable_rel = "notes/system/threads/offgit-anchor.md"
    target = source_repo / deliverable_rel
    target.parent.mkdir(parents=True)
    target.write_text("landed\n", encoding="utf-8")

    packet_text = (
        "Write your analysis to cortex://notes/system/threads/offgit-anchor.md.\n\n"
        "Background: tasks/specs/cursor-sdk-workspaces-full-scope.md"
    )
    expected_paths = extract_instructed_paths(packet_text)
    assert expected_paths == ("notes/system/threads/offgit-anchor.md",)

    manifest = _fs_manifest(path=deliverable_rel)
    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="dispatch-1",
        outcome=SdkRunOutcome(
            body="Wrote the analysis to cortex://notes/system/threads/offgit-anchor.md.",
            status="finished",
            duration_ms=1000,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        thread_id="thread-1",
        work_item_ref="todo:anchor",
        deliverables_expected=True,
        light_bounded_expected_paths=expected_paths,
    )
    payload = json.loads(delivery.body)
    assert payload["status"] == "complete"
    assert payload["capture_status"] == "complete"
    assert "stated_intent_no_write" not in (payload.get("summary") or "")
    assert not any(
        "light_bounded_path_absent" in deviation
        for deviation in payload.get("deviations", [])
    )
    offgit_uri = f"cortex://{deliverable_rel}"
    assert offgit_uri in payload["evidence_uris"]["artifact_paths"]
    assert payload["files_offgit_produced"] == [offgit_uri]
    assert "off-git deliverables: 1" in payload["summary"]


def test_ac2_missing_primary_preserved(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    missing = "notes/system/threads/missing-primary.md"
    packet_text = f"Write the report to cortex://{missing}."
    expected_paths = extract_instructed_paths(packet_text)
    body = f"I will write the report to cortex://{missing} and save it for review."
    degraded = light_bounded_deliverable_reason(
        body=body,
        tool_calls=(),
        contract="light-bounded",
        deliverable_present=False,
    )
    assert degraded == "stated_intent_no_write"

    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="dispatch-2",
        outcome=SdkRunOutcome(
            body=body,
            status="finished",
            duration_ms=1000,
            tool_call_count=0,
            effects_manifest=EffectsManifest(
                dispatch_id="dispatch-2",
                thread_id="thread-1",
            ),
        ),
        degraded_reason=degraded,
        thread_id="thread-1",
        work_item_ref="todo:missing",
        deliverables_expected=True,
        light_bounded_expected_paths=expected_paths,
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "partial"
    assert any(
        f"divergence:light_bounded_path_absent:{missing}" in deviation
        for deviation in payload.get("deviations", [])
    )
    assert "stated_intent_no_write" in payload["summary"]


def test_ac2b_scratch_write_partial_with_suppressed_birth_reason(
    tmp_path: Path,
) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    primary = "notes/system/threads/primary-missing.md"
    scratch = "tmp/summaries/scratch.md"
    scratch_path = cortex_root / scratch
    scratch_path.parent.mkdir(parents=True)
    scratch_path.write_text("scratch\n", encoding="utf-8")

    expected_paths = extract_instructed_paths(
        f"Write the primary deliverable to cortex://{primary}."
    )
    manifest = _fs_manifest(path=scratch)
    degraded = light_bounded_deliverable_reason(
        body=f"Saved output to cortex://{scratch}.",
        tool_calls=(),
        contract="light-bounded",
        deliverable_present=fs_write_landed(
            manifest,
            source_repo=source_repo,
            cortex_root=cortex_root,
        ),
    )
    assert degraded is None

    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="dispatch-3",
        outcome=SdkRunOutcome(
            body=f"Saved output to cortex://{scratch}.",
            status="finished",
            duration_ms=1000,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=degraded,
        thread_id="thread-1",
        work_item_ref="todo:scratch",
        deliverables_expected=True,
        light_bounded_expected_paths=expected_paths,
    )
    payload = json.loads(delivery.body)
    assert payload["status"] == "partial"
    assert any(
        f"divergence:light_bounded_path_absent:{primary}" in deviation
        for deviation in payload.get("deviations", [])
    )
    assert payload["files_offgit_produced"] == [f"cortex://{scratch}"]


def test_ac4_manifest_grounded_suppression(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel = "notes/system/threads/landed.md"
    (cortex_root / rel).parent.mkdir(parents=True)
    (cortex_root / rel).write_text("ok\n", encoding="utf-8")
    manifest = _fs_manifest(path=rel)
    assert fs_write_landed(
        manifest,
        source_repo=source_repo,
        cortex_root=cortex_root,
    )

    missing_manifest = _fs_manifest(path="notes/system/threads/ghost.md")
    assert not fs_write_landed(
        missing_manifest,
        source_repo=source_repo,
        cortex_root=cortex_root,
    )


def test_ac5_compact_fs_detail_no_truncated_key() -> None:
    long_content = "x" * 600
    message = {
        "type": "mcp",
        "args": {
            "toolName": "fs",
            "args": {
                "op": "write",
                "sandbox": "cortex",
                "path": "notes/system/threads/big.md",
                "content": long_content,
            },
        },
    }
    entry = _entry_from_tool_call(message)
    assert entry is not None
    assert entry.detail == {
        "op": "write",
        "sandbox": "cortex",
        "path": "notes/system/threads/big.md",
    }
    assert "truncated" not in (entry.detail or {})
    targets_before = manifest_fs_targets(
        _fs_manifest(path="notes/system/threads/big.md")
    )
    turns = [{"turn": {"steps": [{"type": "toolCall", "message": message}]}}]
    manifest = build_effects_manifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        turns=turns,
    )
    assert manifest_fs_targets(manifest) == targets_before


def test_ac6_projection_hygiene_and_compaction_survival(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel = "notes/system/threads/projection.md"
    closeout_rel = "tmp/reviews/closeouts/dispatch-6.md"
    (cortex_root / rel).parent.mkdir(parents=True)
    (cortex_root / rel).write_text("ok\n", encoding="utf-8")

    manifest = EffectsManifest(
        dispatch_id="dispatch-6",
        thread_id="thread-1",
        surfaces={
            "fs": SurfaceSection(
                surface="fs",
                source="test",
                entries=[
                    EffectEntry(
                        op="fs",
                        target=f"cortex:{rel}",
                        identity=f"cortex:{rel}",
                        detail={"op": "write", "sandbox": "cortex", "path": rel},
                    ),
                    EffectEntry(
                        op="fs",
                        target=f"cortex:{closeout_rel}",
                        identity=f"cortex:{closeout_rel}",
                        detail={
                            "op": "write",
                            "sandbox": "cortex",
                            "path": closeout_rel,
                        },
                    ),
                ],
            )
        },
    )
    sidecar_ref = sidecar_workspaces_ref("dispatch-6")
    pinned = ["cortex://notes/system/pinned.md"]
    uris = manifest_offgit_deliverable_uris(manifest, sidecar_ref=sidecar_ref)
    assert uris == [f"cortex://{rel}"]
    assert sidecar_ref not in uris
    assert not any("tmp/reviews/closeouts/" in uri for uri in uris)

    body = build_implement_closeout_body(
        dispatch_id="dispatch-6",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=1000,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=100,
        thread_id="thread-1",
        work_item_ref="todo:projection",
        cortex_artifact_paths=pinned,
        offgit_deliverable_uris=uris,
        effects_manifest=manifest,
    )
    payload = json.loads(body)
    assert payload["files_offgit_produced"] == [f"cortex://{rel}"]
    assert f"cortex://{rel}" in payload["evidence_uris"]["artifact_paths"]
    assert "off-git deliverables: 1" in payload["summary"]

    large_payload = dict(payload)
    large_payload["deviations"] = [f"padding:{index}" for index in range(800)]
    large_body = json.dumps(large_payload, separators=(",", ":"))
    reduced = json.loads(finalize_closeout_body(large_body))
    assert reduced["files_offgit_produced"] == [f"cortex://{rel}"]
    assert reduced["files_offgit_produced_total"] == 1
    assert reduced["evidence_uris"]["artifact_paths"]
    assert "off-git deliverables: 1" in reduced["summary"]


def test_ac7_shell_parity_on_light_bounded_early_return(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel = "notes/system/threads/shell-parity.md"
    target = source_repo / rel
    target.parent.mkdir(parents=True)
    target.write_text("ok\n", encoding="utf-8")
    manifest = _shell_manifest()
    _, _, deviations, _ = resolve_closeout_capture_fields(
        deliverables_expected=True,
        baseline=None,
        files_expected=[],
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        divergent_rels=(),
        source_repo=source_repo,
        cortex_root=cortex_root,
        manifest=manifest,
        light_bounded_expected_paths=(rel,),
    )
    assert "capture:shell_repo_writes_unverified" in deviations


def test_dropped_non_file_entries_structured(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    (source_repo / "libs").mkdir()
    manifest = EffectsManifest(
        dispatch_id="dispatch-drop",
        thread_id="thread-1",
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="test",
                entries=[
                    EffectEntry(op="write", target="libs", identity="libs"),
                    EffectEntry(
                        op="observed",
                        target="notes/system/read.md",
                        identity="notes/system/read.md",
                    ),
                    EffectEntry(op="write", target="libs", identity="libs"),
                ],
            )
        },
    )
    _, _, dropped = repo_change_set_from_manifest(manifest, source_repo=source_repo)
    assert dropped == [
        {
            "surface": "repo",
            "op": "write",
            "target": "libs",
            "reason": "non_file",
        }
    ]

    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="dispatch-drop",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=1000,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        thread_id="thread-1",
        work_item_ref="todo:dropped",
        baseline={"codes": {}, "hashes": {}, "outside_repo": []},
    )
    payload = json.loads(delivery.body)
    assert payload["dropped_non_file_entries"] == dropped
    assert "capture:non_file_manifest_entry_dropped" in payload["deviations"]


def test_oob_cortex_write_unobserved_partial_without_offgit_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.cortex_files_root",
        lambda: cortex_root,
    )
    oob_rel = "notes/system/threads/source-capture.md"
    inband_rel = "notes/system/threads/pattern-note.md"
    (cortex_root / oob_rel).parent.mkdir(parents=True)
    (cortex_root / oob_rel).write_text("oob landed\n", encoding="utf-8")
    (cortex_root / inband_rel).parent.mkdir(parents=True, exist_ok=True)
    (cortex_root / inband_rel).write_text("in-band\n", encoding="utf-8")

    manifest = _fs_manifest(path=inband_rel)
    sidecar_ref = sidecar_workspaces_ref("dispatch-oob")
    offgit_uris = manifest_offgit_deliverable_uris(manifest, sidecar_ref=sidecar_ref)
    assert offgit_uris == [f"cortex://{inband_rel}"]

    expected = collect_expected_cortex_deliverable_uris(
        files_expected=[f"cortex://{oob_rel}", f"cortex://{inband_rel}"],
        cortex_artifact_paths=[f"cortex://{oob_rel}"],
    )
    assert expected == [f"cortex://{oob_rel}", f"cortex://{inband_rel}"]

    deviations, divergence = oob_cortex_write_findings(
        expected_cortex_uris=expected,
        offgit_uris=offgit_uris,
        cortex_root=cortex_root,
    )
    assert deviations == [f"capture:oob_cortex_write_unobserved:cortex://{oob_rel}"]
    assert divergence == "capture:oob_cortex_write_unobserved"

    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="dispatch-oob",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=1000,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        thread_id="thread-1",
        work_item_ref="todo:oob",
        baseline={"codes": {}, "hashes": {}, "outside_repo": []},
        cortex_artifact_paths=[f"cortex://{oob_rel}"],
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "partial"
    assert any(
        f"capture:oob_cortex_write_unobserved:cortex://{oob_rel}" in deviation
        for deviation in payload.get("deviations", [])
    )
    assert payload["files_offgit_produced"] == [f"cortex://{inband_rel}"]
    assert f"cortex://{oob_rel}" not in payload["files_offgit_produced"]
