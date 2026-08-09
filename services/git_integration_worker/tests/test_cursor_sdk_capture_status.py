"""Regression tests for cursor-sdk capture path canonicalization (Fork A)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    SurfaceSection,
    Verification,
)

from services.git_integration_worker.cursor_sdk_capture_divergence import (
    apply_surface_cross_checks,
)
from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    canonicalize_capture_path,
    filter_probeable_expected_paths,
    is_allowlisted_control_plane_path,
    is_probeable_expected_path,
    project_status_from_work_outcome,
    resolve_closeout_capture_fields,
    resolve_work_outcome,
    stated_intent_no_write_capture_violation,
)
from implement_admission.spec import CloseoutStatus, WorkOutcome
from services.git_integration_worker.cursor_sdk_manifest import (
    repo_change_set_from_manifest,
)

pytestmark = pytest.mark.offline


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "universal-llm-gateway"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _repo_manifest(
    *,
    dispatch_id: str = "d1",
    target: str,
    op: str = "write",
) -> EffectsManifest:
    return EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op=op, target=target, identity=target)],
            )
        },
        coverage={"repo": "complete"},
    )


def test_double_prefix_closeout_sidecar_canonicalized(tmp_path: Path) -> None:
    """AC-A1/A5: double-prefixed allowlisted closeout path must not false-partial."""
    repo = _init_git_repo(tmp_path)
    sidecar_rel = "tmp/reviews/closeouts/d1.md"
    sidecar = repo / sidecar_rel
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text("closeout\n", encoding="utf-8")

    double_target = f"universal-llm-gateway/universal-llm-gateway/{sidecar_rel}"
    manifest = _repo_manifest(target=double_target)
    cortex_root = repo / ".cortex"

    checked = apply_surface_cross_checks(
        manifest,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        source_repo=repo,
        cortex_root=cortex_root,
        files_expected=[],
        divergent_rels=(),
        deliverables_expected=True,
        degraded_reason=None,
    )
    assert checked is not None
    repo_section = checked.surfaces["repo"]
    assert repo_section.cross_check is None
    assert checked.coverage["repo"] == "complete"


def test_no_write_intent_only_allowlisted_manifest_writes_clean(tmp_path: Path) -> None:
    """AC-A2: allowlisted control-plane writes alone do not violate no-write intent."""
    repo = _init_git_repo(tmp_path)
    sidecar_rel = "tmp/reviews/closeouts/d2.md"
    (repo / sidecar_rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / sidecar_rel).write_text("closeout\n", encoding="utf-8")

    manifest = _repo_manifest(target=sidecar_rel)
    change_set = ChangeSet(created=(sidecar_rel,), modified=(), deleted=())
    violation = stated_intent_no_write_capture_violation(
        change_set=change_set,
        manifest=manifest,
        source_repo=repo,
        degraded_reason="stated_intent_no_write",
    )
    assert violation is None


def test_no_write_intent_user_write_surfaces_violation(tmp_path: Path) -> None:
    """AC-A3/A8: real user/workspace writes are not suppressed under no-write intent."""
    repo = _init_git_repo(tmp_path)
    user_path = "libs/example/user_module.py"
    (repo / user_path).parent.mkdir(parents=True, exist_ok=True)
    (repo / user_path).write_text("# user\n", encoding="utf-8")

    manifest = _repo_manifest(target=user_path)
    change_set = ChangeSet(created=(user_path,), modified=(), deleted=())
    violation = stated_intent_no_write_capture_violation(
        change_set=change_set,
        manifest=manifest,
        source_repo=repo,
        degraded_reason="stated_intent_no_write",
    )
    assert violation == f"capture:stated_intent_no_write_violation:{user_path}"


def test_two_gates_do_not_broadly_suppress_files_modified(tmp_path: Path) -> None:
    """AC-A4: manifest projection keeps user writes visible under no-write intent."""
    repo = _init_git_repo(tmp_path)
    user_path = "services/git_integration_worker/example.py"
    manifest = _repo_manifest(target=user_path)
    projected, _outside, _dropped = repo_change_set_from_manifest(
        manifest, source_repo=repo
    )
    assert projected is not None
    assert user_path in projected.created


def test_absolute_inside_repo_converts_to_relative(tmp_path: Path) -> None:
    """AC-A7: absolute path inside repo becomes repo-relative canonical form."""
    repo = _init_git_repo(tmp_path)
    rel = "libs/foo.py"
    absolute = str((repo / rel).resolve())
    canon = canonicalize_capture_path(absolute, source_repo=repo)
    assert canon.canonical_path == rel
    assert canon.scope == "user_workspace"


def test_absolute_outside_repo_is_external(tmp_path: Path) -> None:
    """AC-A10: absolute path outside repo is external_or_unknown."""
    repo = _init_git_repo(tmp_path)
    outside = str((repo.parent / "outside.py").resolve())
    canon = canonicalize_capture_path(outside, source_repo=repo)
    assert canon.scope == "external_or_unknown"
    assert canon.original_path == outside
    assert not is_allowlisted_control_plane_path(canon.canonical_path)


def test_symlink_inside_repo_keeps_logical_path(tmp_path: Path) -> None:
    """AC-A7: symlink resolves for boundary check; logical path retained."""
    repo = _init_git_repo(tmp_path)
    target = repo / "libs" / "real.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# real\n", encoding="utf-8")
    link = repo / "libs" / "linked.py"
    link.symlink_to(target)
    canon = canonicalize_capture_path("libs/linked.py", source_repo=repo)
    assert canon.canonical_path == "libs/linked.py"
    assert canon.scope == "user_workspace"


def test_resolve_closeout_double_prefix_not_partial(tmp_path: Path) -> None:
    """AC-A1: resolve_closeout_capture_fields stays complete for allowlisted double-prefix."""
    repo = _init_git_repo(tmp_path)
    sidecar_rel = "tmp/reviews/closeouts/d3.md"
    (repo / sidecar_rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / sidecar_rel).write_text("closeout\n", encoding="utf-8")
    double_target = f"universal-llm-gateway/{sidecar_rel}"
    manifest = _repo_manifest(target=double_target)

    capture_status, divergence_reason, deviations, _manifest = resolve_closeout_capture_fields(
        deliverables_expected=True,
        baseline={"codes": {}, "hashes": {}},
        files_expected=[],
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        divergent_rels=(),
        source_repo=repo,
        cortex_root=repo / ".cortex",
        manifest=manifest,
    )
    assert capture_status == "complete"
    assert divergence_reason is None
    assert not any("emitted_path_absent" in d for d in deviations)


def test_resolve_closeout_no_write_user_write_partial(tmp_path: Path) -> None:
    """AC-A3: no-write intent + user write degrades via final gate."""
    repo = _init_git_repo(tmp_path)
    user_path = "docs/user_change.md"
    (repo / user_path).parent.mkdir(parents=True, exist_ok=True)
    (repo / user_path).write_text("# doc\n", encoding="utf-8")
    manifest = _repo_manifest(target=user_path)

    capture_status, divergence_reason, deviations, _manifest = resolve_closeout_capture_fields(
        deliverables_expected=True,
        baseline={"codes": {}, "hashes": {}},
        files_expected=[],
        degraded_reason="stated_intent_no_write",
        change_set=ChangeSet(created=(user_path,), modified=(), deleted=()),
        divergent_rels=(),
        source_repo=repo,
        cortex_root=repo / ".cortex",
        manifest=manifest,
    )
    assert capture_status == "partial"
    assert divergence_reason == f"capture:stated_intent_no_write_violation:{user_path}"
    assert any("stated_intent_no_write_violation" in d for d in deviations)


def test_repo_diff_unattributed_deviation_flags_phantom_paths(tmp_path: Path) -> None:
    """Friction 23015: ambient diff paths emit visibility-only ambient token."""
    from services.git_integration_worker.cursor_sdk_capture_status import (
        repo_diff_unattributed_deviation,
    )

    repo = _init_git_repo(tmp_path)
    target = "services/a.py"
    (repo / target).parent.mkdir(parents=True, exist_ok=True)
    (repo / target).write_text("attributed edit\n", encoding="utf-8")
    manifest = _repo_manifest(target=target, op="edit")
    change_set = ChangeSet(
        created=(),
        modified=("services/a.py", "services/phantom_one.py", "services/phantom_two.py"),
        deleted=(),
    )
    ambient, scoped = repo_diff_unattributed_deviation(
        change_set=change_set,
        manifest=manifest,
        source_repo=repo,
        files_expected=["services/a.py"],
        baseline={"codes": {}, "hashes": {}},
    )
    assert scoped is None
    assert ambient is not None
    assert ambient.startswith("divergence:repo_diff_paths_unattributed:ambient:")
    assert "phantom_one" in ambient
    assert "phantom_two" in ambient
    assert "services/a.py" not in ambient


def test_repo_diff_unattributed_deviation_scoped_hard_on_expected(tmp_path: Path) -> None:
    from services.git_integration_worker.cursor_sdk_capture_status import (
        repo_diff_unattributed_deviation,
    )

    repo = _init_git_repo(tmp_path)
    expected = "services/expected.py"
    (repo / expected).parent.mkdir(parents=True, exist_ok=True)
    (repo / expected).write_text("changed\n", encoding="utf-8")
    change_set = ChangeSet(created=(), modified=(expected,), deleted=())
    ambient, scoped = repo_diff_unattributed_deviation(
        change_set=change_set,
        manifest=None,
        source_repo=repo,
        files_expected=[expected],
        baseline={"codes": {}, "hashes": {}},
    )
    assert ambient is None
    assert scoped is not None
    assert scoped.startswith("divergence:repo_diff_paths_unattributed:")
    assert expected in scoped


def test_repo_diff_unattributed_deviation_silent_when_attributed(tmp_path: Path) -> None:
    """No deviation when every diff path has hash-bound manifest write-evidence."""
    from services.git_integration_worker.cursor_sdk_capture_status import (
        repo_diff_unattributed_deviation,
    )

    repo = _init_git_repo(tmp_path)
    target = "services/a.py"
    (repo / target).parent.mkdir(parents=True, exist_ok=True)
    content = "changed\n"
    (repo / target).write_text(content, encoding="utf-8")
    manifest = _repo_manifest(target=target, op="edit")
    change_set = ChangeSet(created=(), modified=(target,), deleted=())
    ambient, scoped = repo_diff_unattributed_deviation(
        change_set=change_set,
        manifest=manifest,
        source_repo=repo,
        files_expected=[target],
        baseline={"codes": {}, "hashes": {}},
    )
    assert ambient is None
    assert scoped is None


def test_repo_diff_unattributed_deviation_silent_on_empty_diff(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    from services.git_integration_worker.cursor_sdk_capture_status import (
        repo_diff_unattributed_deviation,
    )

    ambient, scoped = repo_diff_unattributed_deviation(
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        manifest=None,
        source_repo=repo,
    )
    assert ambient is None
    assert scoped is None


def test_annotate_only_deviations_stay_complete_when_deliverables_present(
    tmp_path: Path,
) -> None:
    """a:25136 — ANNOTATE noise must not force partial when deliverables landed."""
    repo = _init_git_repo(tmp_path)
    expected = "libs/example/delivered.py"
    (repo / expected).parent.mkdir(parents=True, exist_ok=True)
    (repo / expected).write_text("# delivered\n", encoding="utf-8")
    manifest = _repo_manifest(target=expected)
    repo_section = manifest.surfaces["repo"]
    manifest = manifest.model_copy(
        update={
            "coverage": {"repo": "partial"},
            "surfaces": {
                "repo": repo_section.model_copy(
                    update={"cross_check": "divergence:manifest_vs_git_labels"}
                )
            },
        }
    )
    capture_status, divergence_reason, deviations, _manifest = resolve_closeout_capture_fields(
        deliverables_expected=True,
        baseline={"codes": {}, "hashes": {}},
        files_expected=[expected],
        degraded_reason=None,
        change_set=ChangeSet(created=(expected,), modified=(), deleted=()),
        divergent_rels=(),
        source_repo=repo,
        cortex_root=repo / ".cortex",
        manifest=manifest,
    )
    assert capture_status == "complete"
    assert divergence_reason == "divergence:manifest_vs_git_labels"
    assert any("manifest_vs_git_labels" in d for d in deviations)


def test_scoped_unattributed_still_partial_with_deliverables(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    expected = "services/expected.py"
    extra = "services/unexpected.py"
    for path in (expected, extra):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("changed\n", encoding="utf-8")
    manifest = _repo_manifest(target=expected)
    capture_status, _divergence_reason, deviations, _manifest = resolve_closeout_capture_fields(
        deliverables_expected=True,
        baseline={"codes": {}, "hashes": {}},
        files_expected=[expected, extra],
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(expected, extra), deleted=()),
        divergent_rels=(),
        source_repo=repo,
        cortex_root=repo / ".cortex",
        manifest=manifest,
    )
    assert capture_status == "partial"
    assert any(
        d.startswith("divergence:repo_diff_paths_unattributed:")
        and "ambient:" not in d
        for d in deviations
    )


def test_is_probeable_expected_path_rejects_malformed_tokens() -> None:
    assert not is_probeable_expected_path("cortex://")
    assert not is_probeable_expected_path("6524-checkpoint-close.md")
    assert is_probeable_expected_path(
        "cortex://notes/system/threads/6524-checkpoint-close.md"
    )
    assert is_probeable_expected_path("notes/system/specs/foo.md")


def test_filter_probeable_expected_paths_strips_bare_scheme() -> None:
    assert filter_probeable_expected_paths(
        ("cortex://", "notes/system/specs/foo.md")
    ) == ("notes/system/specs/foo.md",)


def test_ac4_residual_twin_work_outcome_shipped_status_complete(tmp_path: Path) -> None:
    """AC4 — D1 row 8fa565: shipped work, degraded capture, status=complete."""
    from services.git_integration_worker.cursor_sdk_closeout import (
        SdkRunOutcome,
        build_implement_closeout_body,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel_a = "notes/system/specs/cursor-auto-request-liveness-degrade.md"
    rel_b = "notes/system/specs/g4-terra-check.md"
    for rel in (rel_a, rel_b):
        target = cortex_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# spec\n", encoding="utf-8")
    offgit = [f"cortex://{rel_a}", f"cortex://{rel_b}"]

    capture_status, divergence_reason, deviations, _manifest = (
        resolve_closeout_capture_fields(
            deliverables_expected=True,
            baseline=None,
            files_expected=[],
            degraded_reason=None,
            change_set=ChangeSet(created=(), modified=(), deleted=()),
            divergent_rels=(),
            source_repo=source_repo,
            cortex_root=cortex_root,
            manifest=None,
            light_bounded_expected_paths=("cortex://",),
        )
    )
    assert capture_status == "unavailable"
    assert divergence_reason == "capture:expected_paths_all_malformed:cortex://"
    work_outcome = resolve_work_outcome(
        degraded_reason=None,
        verification=[],
        files_offgit_produced=offgit,
        artifact_paths=offgit,
        light_bounded_expected_paths=("cortex://",),
        files_expected=[],
        manifest=None,
        source_repo=source_repo,
        cortex_root=cortex_root,
        divergence_reason=divergence_reason,
        deviations=deviations,
        deliverables_expected=True,
    )
    assert work_outcome == WorkOutcome.SHIPPED
    assert project_status_from_work_outcome(work_outcome, None) == CloseoutStatus.COMPLETE

    body = build_implement_closeout_body(
        dispatch_id="8fa5653162a7-33b9b17e",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=1000,
            tool_call_count=3,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("8fa5653162a7-33b9b17e"),
        result_bytes=100,
        thread_id="6582",
        work_item_ref="todo:test",
        capture_status="partial",
        divergence_reason="capture:oob_cortex_write_unobserved:cortex://",
        deviations=["capture:oob_cortex_write_unobserved:cortex://"],
        offgit_deliverable_uris=offgit,
        source_repo=source_repo,
        cortex_root=cortex_root,
        light_bounded_expected_paths=("cortex://",),
        deliverables_expected=True,
    )
    import json

    payload = json.loads(body)
    assert payload["work_outcome"] == "shipped"
    assert payload["status"] == "complete"
    assert payload["capture_status"] == "partial"


def test_finalize_closeout_body_preserves_work_outcome() -> None:
    """AC8 — reduced finalize payload retains work_outcome."""
    import json

    from services.git_integration_worker.cursor_sdk_closeout import finalize_closeout_body

    payload = {
        "schema_version": 1,
        "status": "complete",
        "work_outcome": "shipped",
        "summary": "dispatch d1: 1 tool calls, 1.0s, 100B -> sidecar",
        "source_ref": "todo:test",
        "capture_status": "partial",
        "deviations": [f"padding:{index}" for index in range(800)],
    }
    reduced = json.loads(finalize_closeout_body(json.dumps(payload)))
    assert reduced["work_outcome"] == "shipped"
    assert reduced["status"] == "complete"


def test_i3_stated_intent_no_write_caps_work_at_unverified(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    user_path = "docs/user_change.md"
    (repo / user_path).parent.mkdir(parents=True, exist_ok=True)
    (repo / user_path).write_text("# doc\n", encoding="utf-8")
    manifest = _repo_manifest(target=user_path)
    divergence = f"capture:stated_intent_no_write_violation:{user_path}"
    work_outcome = resolve_work_outcome(
        degraded_reason="stated_intent_no_write",
        verification=[],
        manifest=manifest,
        source_repo=repo,
        cortex_root=repo / ".cortex",
        divergence_reason=divergence,
        deviations=[divergence],
        deliverables_expected=True,
    )
    assert work_outcome == WorkOutcome.UNVERIFIED
    assert work_outcome != WorkOutcome.NOT_SHIPPED


def test_g1_negative_control_pinned_write_failed_gate_d_exit1(tmp_path: Path) -> None:
    """G1 — 091c2d5caaff shape: pinned write fail + gate_d presence exit 1.

    D2 intentionally maps ``pinned_deliverable_write_failed`` to UNVERIFIED (I3
    honest middle), not NOT_SHIPPED — see closeout reachability prose.
    """
    from services.git_integration_worker.cursor_sdk_closeout import (
        SdkRunOutcome,
        build_implement_closeout_body,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    missing_rel = "notes/system/threads/capture-status-work-outcome-split/missing.md"
    degraded_reason = f"pinned_deliverable_write_failed:{missing_rel}"
    verification = [
        Verification(command="gate_d:no_expected_files_touched", exit_code=1)
    ]

    capture_status, divergence_reason, deviations, _manifest = (
        resolve_closeout_capture_fields(
            deliverables_expected=True,
            baseline={"codes": {}, "hashes": {}},
            files_expected=[f"cortex://{missing_rel}"],
            degraded_reason=degraded_reason,
            change_set=ChangeSet(created=(), modified=(), deleted=()),
            divergent_rels=(missing_rel,),
            source_repo=source_repo,
            cortex_root=cortex_root,
            manifest=None,
        )
    )
    work_outcome = resolve_work_outcome(
        degraded_reason=degraded_reason,
        verification=verification,
        files_offgit_produced=[],
        artifact_paths=[],
        light_bounded_expected_paths=(),
        files_expected=[f"cortex://{missing_rel}"],
        manifest=None,
        source_repo=source_repo,
        cortex_root=cortex_root,
        divergence_reason=divergence_reason,
        deviations=deviations,
        deliverables_expected=True,
    )
    status = project_status_from_work_outcome(work_outcome, degraded_reason)

    assert work_outcome == WorkOutcome.UNVERIFIED
    assert work_outcome != WorkOutcome.NOT_SHIPPED
    assert status == CloseoutStatus.PARTIAL

    body = build_implement_closeout_body(
        dispatch_id="091c2d5caaff-912b882f",
        outcome=SdkRunOutcome(
            body="blocked on pinned write",
            status="finished",
            duration_ms=500,
            tool_call_count=2,
        ),
        degraded_reason=degraded_reason,
        sidecar_ref=sidecar_workspaces_ref("091c2d5caaff-912b882f"),
        result_bytes=100,
        thread_id="6588",
        work_item_ref="todo:capture-status-work-outcome-split",
        verification=verification,
        capture_status=capture_status,
        divergence_reason=divergence_reason,
        deviations=deviations,
        source_repo=source_repo,
        cortex_root=cortex_root,
        deliverables_expected=True,
    )
    import json

    payload = json.loads(body)
    assert payload["work_outcome"] == "unverified"
    assert payload["status"] == "partial"


def test_g1_not_shipped_reachable_via_terminal_degrade_tokens(tmp_path: Path) -> None:
    """G1 reachability — production paths that emit NOT_SHIPPED."""
    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()

    for degraded_reason in (
        "zero_tool_calls",
        "empty_assistant_turn",
        "empty_terminal_output",
    ):
        work_outcome = resolve_work_outcome(
            degraded_reason=degraded_reason,
            verification=[],
            manifest=None,
            source_repo=source_repo,
            cortex_root=cortex_root,
            deliverables_expected=True,
        )
        assert work_outcome == WorkOutcome.NOT_SHIPPED, degraded_reason
        assert (
            project_status_from_work_outcome(work_outcome, degraded_reason)
            == CloseoutStatus.FAILED
        ), degraded_reason

    run_status_outcome = resolve_work_outcome(
        degraded_reason="run_status=cancelled",
        verification=[],
        manifest=None,
        source_repo=source_repo,
        cortex_root=cortex_root,
        deliverables_expected=True,
    )
    assert run_status_outcome == WorkOutcome.NOT_SHIPPED
    assert (
        project_status_from_work_outcome(run_status_outcome, "run_status=cancelled")
        == CloseoutStatus.FAILED
    )


def test_g2_ac4_bare_filename_fixture_8b2fdfd6ae7d(tmp_path: Path) -> None:
    """G2 — auto-8b2fdfd6ae7d: bare filename light-bounded token, offgit present."""
    from services.git_integration_worker.cursor_sdk_closeout import (
        SdkRunOutcome,
        build_implement_closeout_body,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    source_repo = tmp_path / "repo"
    cortex_root = tmp_path / "cortex"
    source_repo.mkdir()
    cortex_root.mkdir()
    rel = (
        "notes/system/threads/capture-status-work-outcome-split/"
        "d1-falsifier-probe-2026-07-31.md"
    )
    target = cortex_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# falsifier probe\n", encoding="utf-8")
    offgit = [f"cortex://{rel}"]
    bare_filename = "fable-arch-bind-2026-07-31.md"
    deviations = ["degraded:sdk_git_probe_absent"]

    capture_status, divergence_reason, capture_deviations, _manifest = (
        resolve_closeout_capture_fields(
            deliverables_expected=True,
            baseline=None,
            files_expected=[],
            degraded_reason=None,
            change_set=ChangeSet(created=(), modified=(), deleted=()),
            divergent_rels=(),
            source_repo=source_repo,
            cortex_root=cortex_root,
            manifest=None,
            light_bounded_expected_paths=(bare_filename,),
        )
    )
    work_outcome = resolve_work_outcome(
        degraded_reason=None,
        verification=[],
        files_offgit_produced=offgit,
        artifact_paths=offgit,
        light_bounded_expected_paths=(bare_filename,),
        files_expected=[],
        manifest=None,
        source_repo=source_repo,
        cortex_root=cortex_root,
        divergence_reason=divergence_reason,
        deviations=[*deviations, *capture_deviations],
        deliverables_expected=True,
    )
    status = project_status_from_work_outcome(work_outcome, None)

    assert work_outcome == WorkOutcome.SHIPPED
    assert status == CloseoutStatus.COMPLETE
    assert capture_status == "unavailable"
    assert divergence_reason == (
        "capture:expected_paths_all_malformed:fable-arch-bind-2026-07-31.md"
    )
    # H4 triple: work_outcome=shipped, status=complete, capture_status=unavailable
    assert (
        work_outcome == WorkOutcome.SHIPPED
        and status == CloseoutStatus.COMPLETE
        and capture_status == "unavailable"
    )

    body = build_implement_closeout_body(
        dispatch_id="8b2fdfd6ae7d-4c1a9b2e",
        outcome=SdkRunOutcome(
            body="shipped with bare filename token",
            status="finished",
            duration_ms=800,
            tool_call_count=4,
        ),
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("8b2fdfd6ae7d-4c1a9b2e"),
        result_bytes=100,
        thread_id="6588",
        work_item_ref="todo:capture-status-work-outcome-split",
        capture_status=capture_status,
        divergence_reason=divergence_reason,
        deviations=deviations,
        offgit_deliverable_uris=offgit,
        source_repo=source_repo,
        cortex_root=cortex_root,
        light_bounded_expected_paths=(bare_filename,),
        deliverables_expected=True,
    )
    import json

    payload = json.loads(body)
    assert payload["work_outcome"] == "shipped"
    assert payload["status"] == "complete"
    assert payload["capture_status"] == "unavailable"
    assert "degraded:sdk_git_probe_absent" in deviations
    assert "capture:expected_paths_all_malformed:" in divergence_reason


def test_g3a_deviations_conserved_across_work_outcome_split(tmp_path: Path) -> None:
    """G3a — deviations byte-identical before/after work_outcome resolution."""
    fixtures = (
        {
            "label": "8fa565 bare-scheme",
            "baseline": None,
            "light_bounded": ("cortex://",),
            "offgit": (
                "cortex://notes/system/specs/cursor-auto-request-liveness-degrade.md",
                "cortex://notes/system/specs/g4-terra-check.md",
            ),
            "degraded_reason": None,
        },
        {
            "label": "8b2fdfd6 bare-filename",
            "baseline": None,
            "light_bounded": ("fable-arch-bind-2026-07-31.md",),
            "offgit": (
                "cortex://notes/system/threads/capture-status-work-outcome-split/"
                "d1-falsifier-probe-2026-07-31.md",
            ),
            "degraded_reason": None,
        },
    )
    for fixture in fixtures:
        source_repo = tmp_path / f"repo-{fixture['label']}"
        cortex_root = tmp_path / f"cortex-{fixture['label']}"
        source_repo.mkdir()
        cortex_root.mkdir()
        for uri in fixture["offgit"]:
            rel = uri.removeprefix("cortex://")
            path = cortex_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")

        capture_status, divergence_reason, deviations, _manifest = (
            resolve_closeout_capture_fields(
                deliverables_expected=True,
                baseline=fixture["baseline"],
                files_expected=[],
                degraded_reason=fixture["degraded_reason"],
                change_set=ChangeSet(created=(), modified=(), deleted=()),
                divergent_rels=(),
                source_repo=source_repo,
                cortex_root=cortex_root,
                manifest=None,
                light_bounded_expected_paths=fixture["light_bounded"],
            )
        )
        deviations_before = list(deviations)
        _ = resolve_work_outcome(
            degraded_reason=fixture["degraded_reason"],
            verification=[],
            files_offgit_produced=fixture["offgit"],
            artifact_paths=list(fixture["offgit"]),
            light_bounded_expected_paths=fixture["light_bounded"],
            files_expected=[],
            manifest=None,
            source_repo=source_repo,
            cortex_root=cortex_root,
            divergence_reason=divergence_reason,
            deviations=deviations,
            deliverables_expected=True,
        )
        assert deviations == deviations_before, fixture["label"]
        assert capture_status is not None


def test_g3b_closeout_pipeline_tolerates_omitted_work_outcome() -> None:
    """G3b — Stargate trigger + relay tolerate closeout JSON omitting work_outcome."""
    from unittest.mock import MagicMock, patch

    from implement_admission.closeout_models import ImplementCloseout
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        synthesize_section2,
    )
    from systems.frontier_consult.closeout_reply import trigger_closeout_from_turn

    legacy_payload = {
        "schema_version": 1,
        "status": "complete",
        "summary": "dispatch legacy: no work_outcome field",
        "source_ref": "todo:capture-status-work-outcome-split",
        "capture_status": "complete",
        "deviations": [],
    }
    assert "work_outcome" not in legacy_payload
    parsed = ImplementCloseout.model_validate(legacy_payload)
    assert parsed.work_outcome is None

    with patch(
        "systems.frontier_consult.closeout_reply.run_implement_closeout_pipeline",
        new=MagicMock(return_value={"ok": True}),
    ) as pipeline_mock:
        result = trigger_closeout_from_turn(
            thread_id="6588",
            body=json.dumps(legacy_payload),
            tags=["contract:implement"],
        )
    assert result == {"ok": True}
    pipeline_mock.assert_called_once()
    closeout_arg = pipeline_mock.call_args[0][0]
    assert "work_outcome" not in closeout_arg

    synthesized = synthesize_section2(
        wrapper_text=json.dumps(legacy_payload),
        sidecar_text=None,
        dispatch_id="legacy-no-work-outcome",
    )
    assert synthesized is not None
    assert "work_outcome" not in synthesized
    assert "capture_status=complete" in synthesized


# --- todo:success-shaped-silence G₁ three-trace pins (round-1 specimens) ---


def test_g1_trace_i_auto_bb6dd0a409f6_refuse_stated_intent_no_write(
    tmp_path: Path,
) -> None:
    """(i) auto-bb6dd0a409f6 — G₁ REFUSE even when closeout receipt is probeable.

    Specimen emitted complete/shipped with degraded_reason=stated_intent_no_write
    because positive short-circuited on the worker receipt. G₁ evaluates no-write
    before positive and excludes closeout receipts from intended artifacts.
    """
    repo = _init_git_repo(tmp_path)
    cortex_root = repo / ".cortex"
    cortex_root.mkdir()
    receipt = "tmp/reviews/closeouts/auto-bb6dd0a409f6.md"
    (repo / receipt).parent.mkdir(parents=True, exist_ok=True)
    (repo / receipt).write_text("receipt\n", encoding="utf-8")

    work_outcome = resolve_work_outcome(
        degraded_reason="stated_intent_no_write",
        verification=[],
        files_offgit_produced=[],
        artifact_paths=[
            f"workspaces://universal-llm-gateway/{receipt}",
            receipt,
        ],
        manifest=None,
        source_repo=repo,
        cortex_root=cortex_root,
        deviations=["divergence:light_bounded_path_absent:x", "degraded:sdk_git_probe_absent"],
        deliverables_expected=False,
    )
    assert work_outcome == WorkOutcome.UNVERIFIED
    assert work_outcome != WorkOutcome.SHIPPED
    assert (
        project_status_from_work_outcome(work_outcome, "stated_intent_no_write")
        == CloseoutStatus.PARTIAL
    )


def test_g1_trace_ii_auto_625a11ce0892_admit_with_cortex_artifacts(
    tmp_path: Path,
) -> None:
    """(ii) auto-625a11ce0892 — G₁ ADMIT on status plane (cortex artifacts present)."""
    repo = _init_git_repo(tmp_path)
    cortex_root = tmp_path / "cortex"
    seed = "notes/system/threads/success-shaped-silence/judgment-seed.md"
    spec = "notes/system/specs/success-shaped-silence.md"
    for rel in (seed, spec):
        path = cortex_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("artifact\n", encoding="utf-8")

    work_outcome = resolve_work_outcome(
        degraded_reason=None,
        verification=[],
        files_offgit_produced=[f"cortex://{seed}", f"cortex://{spec}"],
        artifact_paths=[],
        manifest=None,
        source_repo=repo,
        cortex_root=cortex_root,
        deviations=["degraded:sdk_git_probe_absent"],
        deliverables_expected=True,
    )
    assert work_outcome == WorkOutcome.SHIPPED
    assert (
        project_status_from_work_outcome(work_outcome, None) == CloseoutStatus.COMPLETE
    )


def test_g1_trace_iii_auto_028dbc284356_admit_probe_absent_ignored(
    tmp_path: Path,
) -> None:
    """(iii) auto-028dbc284356 — G₁ ADMIT; sdk_git_probe_absent does not refuse."""
    repo = _init_git_repo(tmp_path)
    cortex_root = tmp_path / "cortex"
    rel = "notes/system/threads/6929-hop-window-health-investigate.md"
    path = cortex_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("hop health\n", encoding="utf-8")

    work_outcome = resolve_work_outcome(
        degraded_reason=None,
        verification=[],
        files_offgit_produced=[f"cortex://{rel}"],
        artifact_paths=[],
        manifest=None,
        source_repo=repo,
        cortex_root=cortex_root,
        deviations=["degraded:sdk_git_probe_absent"],
        deliverables_expected=True,
    )
    assert work_outcome == WorkOutcome.SHIPPED
    assert (
        project_status_from_work_outcome(work_outcome, None) == CloseoutStatus.COMPLETE
    )


def test_g1_closeout_receipt_alone_not_intended_artifact(tmp_path: Path) -> None:
    """Closeout receipt cannot satisfy intended_artifact_evidence under G₁."""
    repo = _init_git_repo(tmp_path)
    cortex_root = repo / ".cortex"
    cortex_root.mkdir()
    receipt = "tmp/reviews/closeouts/auto-bb6dd0a409f6.md"
    (repo / receipt).parent.mkdir(parents=True, exist_ok=True)
    (repo / receipt).write_text("receipt\n", encoding="utf-8")

    work_outcome = resolve_work_outcome(
        degraded_reason=None,
        verification=[],
        files_offgit_produced=[],
        artifact_paths=[receipt],
        manifest=None,
        source_repo=repo,
        cortex_root=cortex_root,
        deliverables_expected=True,
    )
    assert work_outcome == WorkOutcome.UNVERIFIED
