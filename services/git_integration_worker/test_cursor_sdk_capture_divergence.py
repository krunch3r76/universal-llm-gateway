"""Tests for cortex-scheme repo cross-check and cortex_assertions harvesting (22764)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
)

from services.git_integration_worker.cursor_sdk_capture_divergence import (
    closeout_divergence_reason,
)
from services.git_integration_worker.cursor_sdk_capture_policy import (
    DegradeTarget,
    DeviationDisposition,
    degrade_target_for_deviation,
    deviation_degrades_capture_status,
    disposition_for_deviation,
)
from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    build_effects_manifest,
    cortex_surface_has_write_op,
    harvest_cortex_assertion_ids,
    normalize_expected_cortex_deliverable_uri,
    repo_change_set_from_manifest,
)

pytestmark = pytest.mark.offline


def _repo_manifest(path: str) -> EffectsManifest:
    return EffectsManifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="test",
                entries=[EffectEntry(op="write", target=path, identity=path)],
            )
        },
    )


def _divergence(
    manifest: EffectsManifest,
    *,
    source_repo: Path,
    cortex_root: Path,
) -> str | None:
    return closeout_divergence_reason(
        deliverables_expected=True,
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        files_expected=[],
        divergent_rels=(),
        source_repo=source_repo,
        cortex_root=cortex_root,
        manifest=manifest,
    )


def test_cortex_repo_path_present_skips_emitted_path_absent(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel = "notes/x.md"
    (cortex_root / rel).parent.mkdir(parents=True)
    (cortex_root / rel).write_text("ok", encoding="utf-8")
    manifest = _repo_manifest(f"cortex://{rel}")
    assert (
        _divergence(manifest, source_repo=source_repo, cortex_root=cortex_root) is None
    )


def test_cortex_repo_path_absent_yields_cortex_target_absent(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    target = "cortex://notes/x.md"
    manifest = _repo_manifest(target)
    assert (
        _divergence(manifest, source_repo=source_repo, cortex_root=cortex_root)
        == f"divergence:cortex_target_absent:{target}"
    )


def test_non_cortex_absent_path_still_emitted_path_absent(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    target = "libs/missing.py"
    manifest = _repo_manifest(target)
    assert (
        _divergence(manifest, source_repo=source_repo, cortex_root=cortex_root)
        == f"divergence:emitted_path_absent:{target}"
    )


def _cortex_manifest(*entries: EffectEntry) -> EffectsManifest:
    return EffectsManifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        surfaces={
            "cortex": SurfaceSection(
                surface="cortex",
                source="test",
                entries=list(entries),
            )
        },
    )


def _closeout_payload(manifest: EffectsManifest, **kwargs: object) -> dict[str, object]:
    body = build_implement_closeout_body(
        dispatch_id="dispatch-1",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=1000,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        sidecar_ref="workspaces://repo/sidecar.md",
        result_bytes=100,
        thread_id="thread-1",
        work_item_ref="todo:foo",
        effects_manifest=manifest,
        **kwargs,
    )
    return json.loads(body)


def test_cortex_surface_has_write_op_detects_assert_and_non_write() -> None:
    assert cortex_surface_has_write_op(
        _cortex_manifest(
            EffectEntry(
                op="cortex",
                detail={"args": {"tool": "assert", "entity_id": "todo:foo"}},
            )
        )
    )
    assert not cortex_surface_has_write_op(
        _cortex_manifest(
            EffectEntry(
                op="cortex",
                detail={"args": {"tool": "entity_get", "entity_id": "todo:foo"}},
            )
        )
    )
    assert not cortex_surface_has_write_op(
        EffectsManifest(dispatch_id="dispatch-1", thread_id="thread-1")
    )


def test_closeout_cortex_writes_unattributed_emits_none_and_deviation() -> None:
    manifest = _cortex_manifest(
        EffectEntry(
            op="cortex",
            detail={"args": {"tool": "assert", "entity_id": "todo:foo"}},
            identity=None,
        )
    )
    payload = _closeout_payload(manifest, capture_status="complete")
    assert payload["evidence_uris"]["cortex_assertions"] is None
    assert "capture:cortex_writes_unattributed" in payload["deviations"]
    assert payload["capture_status"] == "complete"
    assert payload["status"] == "complete"


def test_closeout_harvestable_assert_id_regression() -> None:
    manifest = _cortex_manifest(
        EffectEntry(
            op="cortex",
            detail={"args": {"tool": "assert", "entity_id": "todo:foo"}},
            identity="assertion:123",
        )
    )
    payload = _closeout_payload(manifest, capture_status="complete")
    assert payload["evidence_uris"]["cortex_assertions"] == ["123"]
    assert "capture:cortex_writes_unattributed" not in payload["deviations"]


def test_closeout_no_cortex_writes_regression_empty_list() -> None:
    read_only = _cortex_manifest(
        EffectEntry(
            op="cortex",
            detail={"args": {"tool": "entity_get", "entity_id": "todo:foo"}},
        )
    )
    payload = _closeout_payload(read_only, capture_status="complete")
    assert payload["evidence_uris"]["cortex_assertions"] == []
    assert "capture:cortex_writes_unattributed" not in payload["deviations"]

    empty = EffectsManifest(dispatch_id="dispatch-1", thread_id="thread-1")
    payload = _closeout_payload(empty, capture_status="complete")
    assert payload["evidence_uris"]["cortex_assertions"] == []
    assert "capture:cortex_writes_unattributed" not in payload["deviations"]


def test_cortex_assert_result_id_harvested_in_closeout() -> None:
    turns = [
        {
            "turn": {
                "steps": [
                    {
                        "type": "toolCall",
                        "message": {
                            "type": "mcp",
                            "args": {
                                "toolName": "cortex",
                                "args": {"tool": "assert", "entity_id": "todo:foo"},
                            },
                            "result": {
                                "status": "success",
                                "value": {"item": {"id": 123}},
                            },
                        },
                    }
                ]
            }
        }
    ]
    manifest = build_effects_manifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        turns=turns,
    )
    assert harvest_cortex_assertion_ids(manifest) == ["123"]
    body = build_implement_closeout_body(
        dispatch_id="dispatch-1",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=1000,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        sidecar_ref="workspaces://repo/sidecar.md",
        result_bytes=100,
        thread_id="thread-1",
        work_item_ref="todo:foo",
        effects_manifest=manifest,
    )
    payload = json.loads(body)
    assert payload["evidence_uris"]["cortex_assertions"] == ["123"]


def test_normalize_expected_cortex_uri_rejects_repo_relative() -> None:
    assert normalize_expected_cortex_deliverable_uri("notes/system/foo.md") is None
    assert (
        normalize_expected_cortex_deliverable_uri("cortex://notes/system/foo.md")
        == "cortex://notes/system/foo.md"
    )
    assert (
        normalize_expected_cortex_deliverable_uri("cortex:notes/system/foo.md")
        == "cortex://notes/system/foo.md"
    )


def test_repo_change_set_skips_dot_target_drops(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    manifest = _repo_manifest(".")
    _, _, dropped = repo_change_set_from_manifest(manifest, source_repo=source_repo)
    assert dropped == []


def test_gitignored_expected_on_disk_non_degrading(tmp_path: Path) -> None:
    """AC3: expected gitignored path present on disk does not hard-degrade."""
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel = "services/rag/property_index/test.py"
    (source_repo / ".gitignore").write_text("services/rag/property_index/\n", encoding="utf-8")
    target = source_repo / rel
    target.parent.mkdir(parents=True)
    target.write_text("ok\n", encoding="utf-8")
    assert (
        closeout_divergence_reason(
            deliverables_expected=True,
            degraded_reason=None,
            change_set=ChangeSet(created=(), modified=(), deleted=()),
            files_expected=[rel],
            divergent_rels=(),
            source_repo=source_repo,
            cortex_root=cortex_root,
            manifest=_repo_manifest(rel),
            files_untracked_or_ignored=(rel,),
        )
        is None
    )


def test_gitignored_expected_absent_hard_partial(tmp_path: Path) -> None:
    """AC4/F7: expected gitignored path absent on disk hard-degrades."""
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel = "services/rag/property_index/missing.py"
    (source_repo / ".gitignore").write_text("services/rag/property_index/\n", encoding="utf-8")
    assert (
        closeout_divergence_reason(
            deliverables_expected=True,
            degraded_reason=None,
            change_set=ChangeSet(created=(), modified=(), deleted=()),
            files_expected=[rel],
            divergent_rels=(),
            source_repo=source_repo,
            cortex_root=cortex_root,
            manifest=_repo_manifest(rel),
            files_untracked_or_ignored=(rel,),
        )
        == "divergence:repo_diff_gitignored_present"
    )


def test_scoped_unattributed_hard_ambient_annotate(tmp_path: Path) -> None:
    """AC5: 23015 ambient visibility token is annotate-only; scoped stays hard."""
    from services.git_integration_worker.cursor_sdk_capture_status import (
        repo_diff_unattributed_deviation,
    )

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    expected = "services/job.py"
    ambient = "services/foreign.py"
    (source_repo / expected).parent.mkdir(parents=True)
    (source_repo / expected).write_text("job\n", encoding="utf-8")
    (source_repo / ambient).write_text("foreign\n", encoding="utf-8")
    ambient_token, scoped_token = repo_diff_unattributed_deviation(
        change_set=ChangeSet(created=(ambient, expected), modified=(), deleted=()),
        manifest=None,
        source_repo=source_repo,
        files_expected=[expected],
        baseline={"codes": {}, "hashes": {}},
    )
    assert ambient_token is not None
    assert ":ambient:" in ambient_token
    assert disposition_for_deviation(ambient_token) == DeviationDisposition.ANNOTATE
    assert scoped_token is not None
    assert disposition_for_deviation(scoped_token) == DeviationDisposition.HARD_FAIL


def test_outside_repo_annotate_only_lane_a(tmp_path: Path) -> None:
    """AC7: outside_repo on Lane-A is annotate-only."""
    assert (
        disposition_for_deviation("capture:outside_repo_paths_present")
        == DeviationDisposition.ANNOTATE
    )


def test_deviation_registry_fail_closed_default() -> None:
    """AC11: unknown deviation tokens fail closed to hard_fail."""
    assert disposition_for_deviation("capture:totally_unknown_token") == (
        DeviationDisposition.HARD_FAIL
    )


def test_reconcile_divergences_annotate_capture() -> None:
    """Sister (a): honest uncertainty tokens must not HARD_FAIL capture."""
    tokens = (
        "divergence:seat_claimed_unobserved:document:honest-observability-class-architecture",
        "divergence:observed_unclaimed:stream-cortex:todo:x",
        "reconcile:observed_vs_committed",
    )
    for token in tokens:
        assert disposition_for_deviation(token) == DeviationDisposition.ANNOTATE
        assert degrade_target_for_deviation(token) == DegradeTarget.CAPTURE
        assert deviation_degrades_capture_status(token) is False


def test_git_posture_cross_links_25030() -> None:
    """AC12: git-posture skill cross-links shared-checkout housekeeping a:25030."""
    from pathlib import Path

    skill = (
        Path(__file__).resolve().parents[2]
        / ".claude"
        / "skills"
        / "git-posture"
        / "SKILL.md"
    )
    text = skill.read_text(encoding="utf-8")
    assert "a:25030" in text
    assert "shared-checkout-housekeeping" in text
