"""Unit tests for cursor-sdk closeout validation helpers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from implement_admission.spec import CloseoutStatus

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    attribution_effects_paths,
    baseline_dirty_in_expected,
    dirty_expected_hashes_available,
    normalize_wt_baseline,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    MAX_TURN_BODY_CHARS,
    SdkRunOutcome,
    build_implement_closeout_body,
    capture_wt_baseline,
    capture_wt_baseline_with_hashes,
    changed_paths,
    count_tool_calls,
    degraded_implement_reason,
    degraded_reasons_from_exception,
    empty_assistant_turn_reason,
    empty_output_degraded_reason,
    extract_sdk_git_snapshot,
    finalize_closeout_body,
    format_delivery_fallback_body,
    merge_degraded_reasons,
    prepare_closeout_delivery,
    read_post_wait_snapshot,
    sdk_fs_git_mismatch_reason,
    stream_only_effect_deviations,
)
from services.git_integration_worker.cursor_sdk_closeout_trigger import (
    build_closeout_idempotency_key,
    build_closeout_trigger_payload,
    emit_implement_closeout_trigger,
    extract_turn_number,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    artifact_paths_for_closeout,
    sidecar_workspaces_ref,
)
from services.git_integration_worker.cursor_sdk_packet import (
    extract_source_ref_from_packet,
    infer_contract_from_text,
    resolve_prompt_preamble,
)
from services.git_integration_worker.cursor_sdk_transcript import (
    reconstruct_run_transcript,
    resolve_run_body,
)


def _step(step_type: str) -> object:
    return type("Step", (), {"type": step_type})()


def _turn(*step_types: str) -> object:
    steps = tuple(_step(step_type) for step_type in step_types)
    agent_turn = type("AgentTurn", (), {"steps": steps})()
    return type("ConversationTurn", (), {"turn": agent_turn})()


def test_count_tool_calls() -> None:
    turns = [
        _turn("thinking", "toolCall", "assistant"),
        _turn("assistant"),
        _turn("toolCall", "toolCall"),
    ]
    assert count_tool_calls(turns) == 3


def test_degraded_implement_zero_tool_calls() -> None:
    outcome = SdkRunOutcome(
        body="Implementing",
        status="finished",
        duration_ms=100,
        tool_call_count=0,
    )
    assert degraded_implement_reason(outcome) == "zero_tool_calls"


def test_degraded_implement_bad_status() -> None:
    outcome = SdkRunOutcome(
        body="oops",
        status="error",
        duration_ms=100,
        tool_call_count=2,
    )
    assert degraded_implement_reason(outcome) == "run_status=error"


def test_infer_contract_from_frontmatter() -> None:
    text = "---\ncontract: implement\n---\n<body>"
    assert infer_contract_from_text(text) == "implement"


def test_resolve_prompt_preamble_implement_fallback() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract=None,
        prompt_preamble=None,
        inferred_contract="implement",
    )
    assert "Execute this task NOW" in preamble
    assert "architecture-invariants" in preamble
    assert "docstring-quality" in preamble


def test_resolve_prompt_preamble_implement_no_self_post() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "Post your closeout" not in preamble
    assert "delivers your closeout automatically" in preamble
    assert "Execute this task NOW" in preamble


def test_resolve_prompt_preamble_non_implement_no_self_post() -> None:
    preamble = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "delivers your closeout automatically" not in preamble
    assert "Post your closeout" not in preamble


def test_build_implement_closeout_body_includes_effects_manifest() -> None:
    from services.git_integration_worker.cursor_sdk_manifest import (
        build_effects_manifest,
    )

    turns = [
        type(
            "ConversationTurn",
            (),
            {
                "turn": type(
                    "AgentConversationTurn",
                    (),
                    {
                        "steps": (
                            type(
                                "ToolCallConversationStep",
                                (),
                                {
                                    "type": "toolCall",
                                    "message": {
                                        "type": "write",
                                        "args": {"path": "services/x.py"},
                                    },
                                },
                            )(),
                        )
                    },
                )()
            },
        )()
    ]
    manifest = build_effects_manifest(
        dispatch_id="dm",
        thread_id="tm",
        turns=turns,
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=1500,
        tool_call_count=2,
        effects_manifest=manifest,
    )
    body = build_implement_closeout_body(
        dispatch_id="dm",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("dm"),
        result_bytes=4,
        thread_id="tm",
        work_item_ref="todo:manifest-test",
        change_set=ChangeSet(created=("services/x.py",), modified=(), deleted=()),
        effects_manifest=manifest,
    )
    payload = json.loads(body)
    assert payload["effects_manifest"]["dispatch_id"] == "dm"
    assert "repo" in payload["effects_manifest"]["surfaces"]
    assert payload["files_created"] == ["services/x.py"]


def test_build_implement_closeout_body_ok() -> None:
    outcome = SdkRunOutcome(
        body="status: complete — done",
        status="finished",
        duration_ms=1500,
        tool_call_count=5,
    )
    sidecar_ref = sidecar_workspaces_ref("d1")
    body = build_implement_closeout_body(
        dispatch_id="d1",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="t1",
        work_item_ref=None,
        sidecar_markdown="status: complete — done",
    )
    payload = json.loads(body)
    assert payload["schema_version"] == 1
    assert payload["status"] == "complete"
    assert payload["source_ref"] == sidecar_ref
    assert "5 tool calls" in payload["summary"]
    assert len(body) <= MAX_TURN_BODY_CHARS


def test_build_implement_closeout_body_degraded() -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=0,
    )
    body = build_implement_closeout_body(
        dispatch_id="d2",
        outcome=outcome,
        degraded_reason="zero_tool_calls",
        sidecar_ref=sidecar_workspaces_ref("d2"),
        result_bytes=4,
        thread_id="t2",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] == "failed"
    assert payload["degraded_reason"] == "zero_tool_calls"
    assert payload["tool_call_count"] == 0
    assert "zero_tool_calls" in payload["summary"]


def test_build_implement_closeout_body_run_failed() -> None:
    outcome = SdkRunOutcome(
        body="timeout",
        status="timeout",
        duration_ms=100,
        tool_call_count=0,
    )
    body = build_implement_closeout_body(
        dispatch_id="d3",
        outcome=outcome,
        degraded_reason="run_status=timeout",
        sidecar_ref=sidecar_workspaces_ref("d3"),
        result_bytes=7,
        thread_id="t3",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] == "failed"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "test"],
        check=True,
        capture_output=True,
    )


def _init_git_repo_with_commit(path: Path) -> str:
    """Init repo with one commit; return HEAD sha."""
    _init_git_repo(path)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def test_lane_a_capture_head_sha_from_closeout_head(tmp_path: Path) -> None:
    """AC2: real admit_head + empty range → measured commits_ahead=0."""
    head = _init_git_repo_with_commit(tmp_path)
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="lane-a-head-sha",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline={"admit_head": head},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload.get("head_sha") == head
    assert payload.get("lane") is None
    assert payload.get("commits_ahead") == 0


def test_lane_a_capture_commits_ahead_absent_without_admit_head(
    tmp_path: Path,
) -> None:
    """AC1: unresolvable admit_head must not emit measured commits_ahead=0.

    Fails before the DOOR-1 fix: len(commits_between(admit_head=None)) launders
    into present 0 and the plane gate demotes tip-on-master to NOT landed.
    """
    from services.git_integration_worker.cursor_auto.closeout_plane_probe import (
        PlaneObservation,
        apply_landed_admit_gate,
        parse_capture_plane_keys,
        render_plane_headline,
    )

    head = _init_git_repo_with_commit(tmp_path)
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="lane-a-no-admit-head",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload.get("head_sha") == head
    assert "commits_ahead" not in payload

    keys = parse_capture_plane_keys(delivery.body)
    assert keys.commits_ahead_presence == "absent"
    assert keys.commits_ahead is None
    gated = apply_landed_admit_gate(
        PlaneObservation(
            head_sha=head,
            branch=None,
            commit_exists=True,
            landed_local_master=True,
            published_origin=None,
            unknown_reason=None,
            as_of="t0",
        ),
        commits_ahead=keys.commits_ahead,
        commits_ahead_presence=keys.commits_ahead_presence,
    )
    headline = render_plane_headline(gated)
    assert gated.landed_local_master is None
    assert "unknown@local-master (commits_ahead absent)" in headline
    assert "NOT landed@local-master" not in headline
    assert "landed@local-master" not in headline.replace(
        "unknown@local-master", ""
    )


def test_lane_a_capture_commits_ahead_after_commit(tmp_path: Path) -> None:
    """AC3: Lane-A tip advance after real admit_head → commits_ahead>=1."""
    admit = _init_git_repo_with_commit(tmp_path)
    (tmp_path / "lane_a_progress.py").write_text("progress\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "lane_a_progress.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "lane-a progress"],
        check=True,
        capture_output=True,
    )
    closeout = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="lane-a-commits-ahead",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline={"admit_head": admit},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload.get("head_sha") == closeout
    assert payload.get("commits_ahead") >= 1


def test_prepare_closeout_delivery_baseline_none_ignores_dirty_tree(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "peer_leftover.py").write_text("# stale peer edit\n", encoding="utf-8")
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-no-baseline",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=None,
    )
    payload = json.loads(delivery.body)
    assert payload["files_created"] == []
    assert payload["files_modified"] == []
    assert payload["files_deleted"] == []
    assert payload["verification"] == []
    assert payload.get("capture_status") is None


def test_prepare_closeout_delivery_baseline_none_still_harvests_pytest(
    tmp_path: Path,
) -> None:
    """Wiring: harvest is not gated on baseline (e93f light-bounded path)."""
    from services.git_integration_worker.cursor_sdk_stream_capture import (
        ToolCallObservation,
    )

    _init_git_repo(tmp_path)
    result = {
        "status": "success",
        "value": {"exitCode": 0, "stdout": "1 passed\n", "stderr": ""},
    }
    obs = ToolCallObservation(
        call_id="call-baseline-none-pytest",
        tool_name="shell",
        status="completed",
        arg_bytes=1,
        result_bytes=1,
        truncated_fields=(),
        args={"command": "pytest -q services/foo/test_bar.py"},
        result=result,
        result_body=result,
        result_body_status="present",
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        tool_calls=(obs,),
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-no-baseline-harvest",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=None,
    )
    payload = json.loads(delivery.body)
    assert payload["files_created"] == []
    assert payload["files_modified"] == []
    verification = payload["verification"]
    assert len(verification) == 1
    assert verification[0]["exit_code_register"] == "observed"
    assert verification[0]["exit_code"] == 0
    assert verification[0]["basis"] == "shell_tool_result.exitCode"
    assert verification[0]["invocation_id"] == "test:call-baseline-none-pytest"


def test_prepare_closeout_delivery_implement_clean_complete(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-clean",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline={},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "complete"
    assert payload["status"] == "complete"


def test_prepare_closeout_delivery_implement_dirty_partial(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    baseline = {"services/foo.py": " M services/foo.py"}
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-dirty",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=baseline,
        deliverables_expected=True,
        packet_text="<scope>\nFiles expected: - `services/foo.py`\n</scope>\n",
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "partial"
    assert payload["status"] == "partial"
    assert "capture:dirty_baseline_under_capture" in payload["deviations"]


def test_prepare_closeout_delivery_implement_unavailable(tmp_path: Path) -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-unavail",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=None,
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "unavailable"
    assert payload["status"] == "partial"
    assert payload["files_created"] == []
    assert payload["files_modified"] == []


def test_prepare_closeout_delivery_light_bounded_written_path_complete(
    tmp_path: Path,
) -> None:
    """AC: light-bounded dispatch that wrote its named path is not false-degraded."""
    target = tmp_path / "tasks" / "journal" / "review.md"
    target.parent.mkdir(parents=True)
    target.write_text("review notes\n", encoding="utf-8")
    outcome = SdkRunOutcome(
        body="Wrote the review to tasks/journal/review.md.",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-lb-complete",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=None,
        deliverables_expected=True,
        light_bounded_expected_paths=("tasks/journal/review.md",),
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "complete"
    assert payload["status"] == "complete"


def test_prepare_closeout_delivery_light_bounded_missing_path_partial(
    tmp_path: Path,
) -> None:
    """AC: light-bounded dispatch that named a path but never wrote it is flagged."""
    outcome = SdkRunOutcome(
        body="I'll write the review to tasks/journal/review.md.",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-lb-partial",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=None,
        deliverables_expected=True,
        light_bounded_expected_paths=("tasks/journal/review.md",),
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "partial"
    assert payload["status"] == "partial"
    assert (
        "divergence:light_bounded_path_absent:tasks/journal/review.md"
        in payload["deviations"]
    )


def test_prepare_closeout_delivery_light_bounded_wrote_elsewhere_partial(
    tmp_path: Path,
) -> None:
    """AC: writing a different file than the one named must still be flagged."""
    wrong = tmp_path / "tasks" / "journal" / "other.md"
    wrong.parent.mkdir(parents=True)
    wrong.write_text("wrong file\n", encoding="utf-8")
    outcome = SdkRunOutcome(
        body="Wrote the review to tasks/journal/other.md.",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-lb-elsewhere",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=None,
        deliverables_expected=True,
        light_bounded_expected_paths=("tasks/journal/review.md",),
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "partial"
    assert payload["status"] == "partial"


def test_capture_wt_baseline_failure_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("git unavailable")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.subprocess.run",
        _boom,
    )
    assert capture_wt_baseline(tmp_path) is None


def test_changed_paths_tolerates_none_current_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.capture_wt_baseline",
        lambda _repo: None,
    )
    delta, _polarity_deviations = changed_paths(tmp_path, {"a.py": "?? a.py"})
    assert delta.created == ()
    assert delta.modified == ()
    assert delta.deleted == ()


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_changed_paths_same_code_dirty_tracked_content_change_modified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = "before\n"
    after = "after\n"
    (tmp_path / "a.py").write_text(after, encoding="utf-8")
    baseline = {
        "codes": {"a.py": " M"},
        "hashes": {"a.py": _sha256_hex(before)},
    }
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.capture_wt_baseline",
        lambda _repo: {"a.py": " M"},
    )
    delta, _polarity_deviations = changed_paths(tmp_path, baseline)
    assert delta.modified == ("a.py",)
    assert delta.created == ()
    assert delta.deleted == ()


def test_changed_paths_same_code_untracked_content_change_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = "stale\n"
    after = "fresh\n"
    (tmp_path / "n.md").write_text(after, encoding="utf-8")
    baseline = {
        "codes": {"n.md": "??"},
        "hashes": {"n.md": _sha256_hex(before)},
    }
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.capture_wt_baseline",
        lambda _repo: {"n.md": "??"},
    )
    delta, _polarity_deviations = changed_paths(tmp_path, baseline)
    assert delta.created == ("n.md",)
    assert delta.modified == ()
    assert delta.deleted == ()


def test_changed_paths_same_code_content_unchanged_not_attributed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = "unchanged\n"
    (tmp_path / "a.py").write_text(content, encoding="utf-8")
    digest = _sha256_hex(content)
    baseline = {"codes": {"a.py": " M"}, "hashes": {"a.py": digest}}
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.capture_wt_baseline",
        lambda _repo: {"a.py": " M"},
    )
    delta, _polarity_deviations = changed_paths(tmp_path, baseline)
    assert delta.created == ()
    assert delta.modified == ()
    assert delta.deleted == ()


def test_n4_integration_baseline_normalization_both_shapes_both_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = "before\n"
    after = "after\n"
    path = "tracked.py"
    (tmp_path / path).write_text(after, encoding="utf-8")
    admit_hash = _sha256_hex(before)
    legacy_baseline = {path: " M"}
    nested_baseline = {
        "codes": {path: " M"},
        "hashes": {path: admit_hash},
    }
    files_expected = [path]
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.capture_wt_baseline",
        lambda _repo: {path: " M"},
    )

    legacy_codes, legacy_hashes = normalize_wt_baseline(legacy_baseline)
    nested_codes, nested_hashes = normalize_wt_baseline(nested_baseline)
    assert legacy_codes == nested_codes == {path: " M"}
    assert legacy_hashes == {}
    assert nested_hashes == {path: admit_hash}

    assert baseline_dirty_in_expected(legacy_baseline, files_expected) is True
    assert baseline_dirty_in_expected(nested_baseline, files_expected) is True
    assert dirty_expected_hashes_available(legacy_baseline, files_expected) is False
    assert dirty_expected_hashes_available(nested_baseline, files_expected) is True

    legacy_delta, _legacy_deviations = changed_paths(tmp_path, legacy_baseline)
    nested_delta, _nested_deviations = changed_paths(tmp_path, nested_baseline)
    assert legacy_delta.modified == ()
    assert nested_delta.modified == (path,)
    assert legacy_delta.created == nested_delta.created == ()
    assert legacy_delta.deleted == nested_delta.deleted == ()


def test_n5_mixed_clean_new_and_dirty_recovered_in_one_closeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = "before\n"
    after = "after\n"
    dirty_path = "tracked.py"
    new_path = "new_file.py"
    (tmp_path / dirty_path).write_text(after, encoding="utf-8")
    (tmp_path / new_path).write_text("x = 1\n", encoding="utf-8")
    baseline = {
        "codes": {dirty_path: " M"},
        "hashes": {dirty_path: _sha256_hex(before)},
    }
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.capture_wt_baseline",
        lambda _repo: {dirty_path: " M", new_path: "??"},
    )
    delta, _polarity_deviations = changed_paths(tmp_path, baseline)
    assert delta.created == (new_path,)
    assert delta.modified == (dirty_path,)
    assert delta.deleted == ()


def test_changed_paths_clean_baseline_new_untracked_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "new_file.py").write_text("x = 1\n", encoding="utf-8")
    baseline = {"codes": {}, "hashes": {}}
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.capture_wt_baseline",
        lambda _repo: {"new_file.py": "??"},
    )
    delta, _polarity_deviations = changed_paths(tmp_path, baseline)
    assert delta.created == ("new_file.py",)
    assert delta.modified == ()
    assert delta.deleted == ()


def test_capture_wt_baseline_with_hashes_only_dirty_set(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.py").write_text("dirty\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("clean\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "clean.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "tracked.py").write_text("dirty edit\n", encoding="utf-8")
    from services.git_integration_worker.cursor_sdk_closeout import (
        capture_wt_baseline_with_hashes,
    )

    snapshot = capture_wt_baseline_with_hashes(tmp_path)
    assert snapshot is not None
    assert "tracked.py" in snapshot["codes"]
    assert "clean.py" not in snapshot["codes"]
    assert set(snapshot["hashes"]) == set(snapshot["codes"])


def _materialize_manifest_repo_files(repo_root: Path, manifest: object) -> None:
    from implement_admission.closeout_models import EffectsManifest

    assert isinstance(manifest, EffectsManifest)
    section = manifest.surfaces.get("repo")
    if section is None:
        return
    for entry in section.entries:
        if not entry.target or entry.target in {".", ""}:
            continue
        rel = entry.target
        if rel.startswith(str(repo_root)):
            rel = str(Path(rel).relative_to(repo_root))
        target = repo_root / rel
        if target.suffix == ".py" or rel.endswith(".py"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# generated\n", encoding="utf-8")


def test_prepare_closeout_delivery_dirty_baseline_recovered_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "services" / "foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("before = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "services/foo.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    target.write_text("dirty = 1\n", encoding="utf-8")
    admit_hash = _sha256_hex("dirty = 1\n")
    target.write_text("after = 1\n", encoding="utf-8")
    baseline = {
        "codes": {"services/foo.py": " M"},
        "hashes": {"services/foo.py": admit_hash},
        "outside_repo": [],
    }
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    manifest = EffectsManifest(
        dispatch_id="disp-recovered",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="edit",
                        target=str(target),
                        identity="services/foo.py",
                    )
                ],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-recovered",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=baseline,
        deliverables_expected=True,
        packet_text="<scope>\nFiles expected: - `services/foo.py`\n</scope>\n",
    )
    payload = json.loads(delivery.body)
    assert "services/foo.py" in payload["files_modified"]
    assert payload["capture_status"] == "complete"
    assert payload["status"] == "complete"
    assert "capture:dirty_baseline_under_capture" not in payload["deviations"]


def test_prepare_closeout_delivery_with_baseline_computes_files(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    baseline: dict[str, str] = {}
    (tmp_path / "new_file.py").write_text("x = 1\n", encoding="utf-8")
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-baseline",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=baseline,
    )
    payload = json.loads(delivery.body)
    assert payload["files_created"] == ["new_file.py"]
    assert payload["verification"] != []


def test_prepare_closeout_delivery_writes_sidecar_and_bounds_body(
    tmp_path: Path,
) -> None:
    outcome = SdkRunOutcome(
        body="x" * 8500,
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-big",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
    )
    assert len(delivery.body) <= MAX_TURN_BODY_CHARS
    assert delivery.sidecar_ref == sidecar_workspaces_ref("disp-big")
    sidecar_text = delivery.sidecar_path.read_text(encoding="utf-8")
    assert sidecar_text.startswith(outcome.body)
    assert "## structured_closeout_full" in sidecar_text
    assert delivery.sidecar_ref in delivery.body


def test_prepare_closeout_delivery_body_is_json(tmp_path: Path) -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-json",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
    )
    payload = json.loads(delivery.body)
    assert payload["schema_version"] == 1
    assert payload["source_ref"] == delivery.sidecar_ref


def test_prepare_closeout_delivery_degraded_sidecar(tmp_path: Path) -> None:
    outcome = SdkRunOutcome(
        body="Implementing",
        status="finished",
        duration_ms=50,
        tool_call_count=0,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-degraded",
        outcome=outcome,
        degraded_reason="zero_tool_calls",
        thread_id="t1",
        work_item_ref=None,
    )
    sidecar_text = delivery.sidecar_path.read_text(encoding="utf-8")
    assert sidecar_text.startswith("status: degraded\nreason: zero_tool_calls")
    assert "Implementing" in sidecar_text
    payload = json.loads(delivery.body)
    assert payload["status"] == "failed"
    assert "zero_tool_calls" in payload["summary"]


def test_trigger_closeout_from_turn_accepts_structured_body() -> None:
    from systems.frontier_consult.closeout_reply import trigger_closeout_from_turn

    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=1500,
        tool_call_count=5,
    )
    sidecar_ref = sidecar_workspaces_ref("d-closeout")
    body = build_implement_closeout_body(
        dispatch_id="d-closeout",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="t1",
        work_item_ref=None,
    )
    with patch(
        "systems.frontier_consult.closeout_reply.run_implement_closeout_pipeline",
        new=MagicMock(return_value={"ok": True}),
    ) as pipeline_mock:
        result = trigger_closeout_from_turn(
            thread_id="t1",
            body=body,
            tags=["contract:implement"],
        )
    pipeline_mock.assert_called_once()
    assert result == {"ok": True}
    closeout_arg = pipeline_mock.call_args[0][0]
    assert closeout_arg["schema_version"] == 1


def test_format_delivery_fallback_body() -> None:
    body = format_delivery_fallback_body(
        status_code=413,
        sidecar_ref=sidecar_workspaces_ref("disp-fail"),
        result_bytes=8375,
    )
    assert "status: delivery_failed" in body
    assert "bus_status_code: 413" in body
    assert len(body) <= MAX_TURN_BODY_CHARS


def test_extract_source_ref_from_packet_source_ref_line() -> None:
    assert (
        extract_source_ref_from_packet("---\nsource_ref: todo:foo\n---") == "todo:foo"
    )


def test_extract_source_ref_from_packet_todo_line() -> None:
    assert extract_source_ref_from_packet("---\ntodo: todo:bar\n---") == "todo:bar"


def test_extract_source_ref_from_packet_none() -> None:
    assert extract_source_ref_from_packet("---\ncontract: implement\n---\nbody") is None


def test_build_body_uses_work_item_ref() -> None:
    outcome = SdkRunOutcome(
        body="done", status="finished", duration_ms=1000, tool_call_count=3
    )
    sidecar_ref = sidecar_workspaces_ref("dx")
    body = build_implement_closeout_body(
        dispatch_id="dx",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="1865",
        work_item_ref="todo:x",
    )
    payload = json.loads(body)
    assert payload["source_ref"] == "todo:x"
    assert payload["evidence_uris"]["artifact_paths"] == [sidecar_ref]
    assert payload["evidence_uris"]["bus_threads"] == ["1865"]
    assert payload["evidence_uris"]["dispatch_ids"] == ["dx"]


def test_build_body_fallback_sidecar() -> None:
    outcome = SdkRunOutcome(
        body="done", status="finished", duration_ms=1000, tool_call_count=3
    )
    sidecar_ref = sidecar_workspaces_ref("dy")
    body = build_implement_closeout_body(
        dispatch_id="dy",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="1865",
        work_item_ref=None,
    )
    assert json.loads(body)["source_ref"] == sidecar_ref


def test_build_closeout_idempotency_key() -> None:
    key = build_closeout_idempotency_key(execution_id="E", thread_id="T", turn_number=5)
    assert key == "implement-closeout:E:T:5"


def test_build_closeout_trigger_payload() -> None:
    body_json = json.dumps({"schema_version": 1, "status": "complete"})
    payload = build_closeout_trigger_payload(
        body_json=body_json, source_ref="todo:x", idempotency_key="k"
    )
    assert payload["closeout"] == {"schema_version": 1, "status": "complete"}
    assert payload["source_ref"] == "todo:x"
    assert payload["idempotency_key"] == "k"


@pytest.mark.asyncio
async def test_emit_trigger_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("transport down")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout_trigger.make_async_client",
        _boom,
    )
    result = await emit_implement_closeout_trigger(
        body_json=json.dumps({"status": "complete"}),
        source_ref="todo:x",
        idempotency_key="k",
    )
    assert result is None


def test_extract_turn_number() -> None:
    assert extract_turn_number({"turn_number": 5}) == 5
    assert extract_turn_number({"turn": {"turn_number": 7}}) == 7
    assert extract_turn_number("x") is None


# --- friction 19819: empty-sidecar / output-loss regression coverage ---


def _assistant_step(text: str) -> object:
    message = type("AssistantMessage", (), {"text": text})()
    return type(
        "AssistantConversationStep",
        (),
        {"type": "assistantMessage", "message": message},
    )()


def _toolcall_step(message: dict) -> object:
    return type(
        "ToolCallConversationStep", (), {"type": "toolCall", "message": message}
    )()


def _agent_turn(*steps: object) -> object:
    agent_turn = type("AgentConversationTurn", (), {"steps": tuple(steps)})()
    return type("ConversationTurn", (), {"turn": agent_turn})()


def _shell_turn(
    command: str, stdout: str, stderr: str = "", exit_code: int = 0
) -> object:
    cmd = type("ShellCommand", (), {"command": command, "working_directory": ""})()
    out = type(
        "ShellOutput", (), {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    )()
    inner = type(
        "ShellConversationTurn", (), {"shell_command": cmd, "shell_output": out}
    )()
    return type("ConversationTurn", (), {"turn": inner})()


def _shell_toolcall(command: str, stdout: str, exit_code: int = 0) -> dict:
    return {
        "type": "shell",
        "args": {"command": command},
        "result": {
            "status": "success",
            "value": {"stdout": stdout, "stderr": "", "exitCode": exit_code},
        },
    }


def test_reconstruct_transcript_captures_tool_output() -> None:
    turn = _agent_turn(
        _assistant_step("Running the tests."),
        _toolcall_step(_shell_toolcall("pytest -v", "37 passed")),
    )
    text = reconstruct_run_transcript([turn])
    assert "pytest -v" in text
    assert "37 passed" in text
    assert "Running the tests." in text


def test_reconstruct_transcript_captures_shell_turn() -> None:
    turn = _shell_turn("pytest -q", "37 passed", exit_code=0)
    text = reconstruct_run_transcript([turn])
    assert "pytest -q" in text
    assert "37 passed" in text


def test_reconstruct_transcript_tolerates_bare_steps() -> None:
    # Steps without .message (older/unknown shapes) are skipped, never raise.
    bare = _agent_turn(_step("thinking"), _step("toolCall"))
    assert reconstruct_run_transcript([bare]) == ""


def test_resolve_run_body_prefers_result_text() -> None:
    turn = _agent_turn(_toolcall_step(_shell_toolcall("pytest", "37 passed")))
    assert resolve_run_body("final summary", [turn]) == "final summary"


def test_resolve_run_body_falls_back_to_transcript_when_result_empty() -> None:
    turn = _agent_turn(_toolcall_step(_shell_toolcall("pytest", "37 passed")))
    body = resolve_run_body("", [turn])
    assert body.strip()
    assert "37 passed" in body


def test_resolve_run_body_empty_when_no_output_anywhere() -> None:
    assert resolve_run_body("", []) == ""
    assert resolve_run_body("   ", []) == ""


def test_empty_output_degraded_reason_finished_empty() -> None:
    outcome = SdkRunOutcome(
        body="", status="finished", duration_ms=100, tool_call_count=3
    )
    assert empty_output_degraded_reason(outcome) == "empty_terminal_output"


def test_empty_output_degraded_reason_nonempty_body_is_none() -> None:
    outcome = SdkRunOutcome(
        body="37 passed", status="finished", duration_ms=100, tool_call_count=3
    )
    assert empty_output_degraded_reason(outcome) is None


def test_empty_output_degraded_reason_unfinished_is_none() -> None:
    # Non-finished runs are covered by the run_status= reason, not this guard.
    outcome = SdkRunOutcome(
        body="", status="timeout", duration_ms=100, tool_call_count=0
    )
    assert empty_output_degraded_reason(outcome) is None


# --- friction 24299: hollow-model-no-op outranks pinned_deliverable_* ---


def test_empty_assistant_turn_reason_empty_body_zero_tools() -> None:
    outcome = SdkRunOutcome(
        body="", status="finished", duration_ms=100, tool_call_count=0
    )
    assert empty_assistant_turn_reason(outcome) == "empty_assistant_turn"


def test_empty_assistant_turn_reason_is_status_independent() -> None:
    # The 24299 signature: SDK reported a non-"finished" status for a hollow stop,
    # so the finished-gated empty_output guard misses it — this one must not.
    for status in ("aborted", "timeout", "error", "", "cancelled"):
        outcome = SdkRunOutcome(
            body="   ", status=status, duration_ms=100, tool_call_count=0
        )
        assert empty_assistant_turn_reason(outcome) == "empty_assistant_turn"


def test_empty_assistant_turn_reason_none_when_body_present() -> None:
    outcome = SdkRunOutcome(
        body="did something", status="finished", duration_ms=100, tool_call_count=0
    )
    assert empty_assistant_turn_reason(outcome) is None


def test_empty_assistant_turn_reason_none_when_tools_ran() -> None:
    # Empty body but tool calls landed is the empty_terminal_output domain, not
    # a hollow no-op — this guard must defer to it.
    outcome = SdkRunOutcome(
        body="", status="finished", duration_ms=100, tool_call_count=2
    )
    assert empty_assistant_turn_reason(outcome) is None


def test_empty_assistant_turn_maps_failed_with_reason_in_summary() -> None:
    outcome = SdkRunOutcome(
        body="", status="aborted", duration_ms=100, tool_call_count=0
    )
    body = build_implement_closeout_body(
        dispatch_id="d-hollow",
        outcome=outcome,
        degraded_reason="empty_assistant_turn",
        sidecar_ref=sidecar_workspaces_ref("d-hollow"),
        result_bytes=0,
        thread_id="t-hollow",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] == "failed"
    assert "empty_assistant_turn" in payload["summary"]


@pytest.mark.parametrize(
    "degraded_reason",
    ["zero_tool_calls", "empty_terminal_output"],
)
def test_no_run_degraded_reasons_map_failed(degraded_reason: str) -> None:
    outcome = SdkRunOutcome(
        body="",
        status="aborted" if degraded_reason != "empty_terminal_output" else "finished",
        duration_ms=100,
        tool_call_count=0 if degraded_reason != "empty_terminal_output" else 3,
    )
    body = build_implement_closeout_body(
        dispatch_id=f"d-{degraded_reason}",
        outcome=outcome,
        degraded_reason=degraded_reason,
        sidecar_ref=sidecar_workspaces_ref(f"d-{degraded_reason}"),
        result_bytes=0,
        thread_id=f"t-{degraded_reason}",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] == "failed"
    assert degraded_reason in payload["summary"]


def test_pinned_deliverable_write_failed_still_maps_partial() -> None:
    outcome = SdkRunOutcome(
        body="blocked on pinned write",
        status="finished",
        duration_ms=500,
        tool_call_count=2,
    )
    body = build_implement_closeout_body(
        dispatch_id="d-pinned-fail",
        outcome=outcome,
        degraded_reason="pinned_deliverable_write_failed:notes/system/templates/foo.md",
        sidecar_ref=sidecar_workspaces_ref("d-pinned-fail"),
        result_bytes=100,
        thread_id="t-pinned-fail",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] == "partial"
    assert "pinned_deliverable_write_failed" in payload["summary"]


async def _failing_pin_resolution(**_: object):
    from services.git_integration_worker.cursor_sdk_deliverables import PinnedResolution

    return PinnedResolution(
        uris=[],
        satisfied_rels=(),
        divergent_rels=(
            "pinned_deliverable_write_failed:notes/system/templates/foo.md",
        ),
    )


@pytest.mark.asyncio
async def test_pin_reason_promoted_when_no_run_health_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-fix promotion path: a failed cortex pin becomes the primary reason only
    when no run-health reason precedes it (documents the 24299 misdiagnosis)."""
    from services.git_integration_worker import cursor_sdk_closeout as closeout_mod
    from services.git_integration_worker.cursor_sdk_closeout import (
        prepare_closeout_delivery_async,
    )

    monkeypatch.setattr(
        closeout_mod, "resolve_cortex_pinned_deliverables", _failing_pin_resolution
    )
    outcome = SdkRunOutcome(
        body="", status="finished", duration_ms=3400, tool_call_count=0
    )
    delivery = await prepare_closeout_delivery_async(
        source_repo=tmp_path,
        dispatch_id="disp-24299-pin",
        outcome=outcome,
        degraded_reason=None,
        thread_id="5121",
        work_item_ref=None,
        packet_text=(
            "<scope>\nFiles expected: - `cortex://notes/system/templates/foo.md`"
            "\n</scope>\n"
        ),
        deliverables_expected=True,
        execution_id="exec-24299",
    )
    payload = json.loads(delivery.body)
    assert "pinned_deliverable_write_failed" in payload["summary"]


@pytest.mark.asyncio
async def test_empty_assistant_turn_outranks_pin_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix (friction 24299): the hollow-no-op reason computed upstream survives the
    ``degraded_reason or pin_reason`` promotion, so operators see the model no-op —
    not the secondary pin-write miss."""
    from services.git_integration_worker import cursor_sdk_closeout as closeout_mod
    from services.git_integration_worker.cursor_sdk_closeout import (
        prepare_closeout_delivery_async,
    )

    monkeypatch.setattr(
        closeout_mod, "resolve_cortex_pinned_deliverables", _failing_pin_resolution
    )
    outcome = SdkRunOutcome(
        body="", status="finished", duration_ms=3400, tool_call_count=0
    )
    delivery = await prepare_closeout_delivery_async(
        source_repo=tmp_path,
        dispatch_id="disp-24299-hollow",
        outcome=outcome,
        # _finalize_success would compute this via empty_assistant_turn_reason.
        degraded_reason="empty_assistant_turn",
        thread_id="5121",
        work_item_ref=None,
        packet_text=(
            "<scope>\nFiles expected: - `cortex://notes/system/templates/foo.md`"
            "\n</scope>\n"
        ),
        deliverables_expected=True,
        execution_id="exec-24299",
    )
    payload = json.loads(delivery.body)
    assert "empty_assistant_turn" in payload["summary"]
    assert "pinned_deliverable_write_failed" not in payload["summary"]
    assert payload["status"] == "failed"


def test_empty_terminal_output_maps_failed_not_complete() -> None:
    outcome = SdkRunOutcome(
        body="", status="finished", duration_ms=100, tool_call_count=3
    )
    body = build_implement_closeout_body(
        dispatch_id="d-empty",
        outcome=outcome,
        degraded_reason="empty_terminal_output",
        sidecar_ref=sidecar_workspaces_ref("d-empty"),
        result_bytes=0,
        thread_id="t-empty",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] == "failed"
    assert payload["status"] != "complete"
    assert "empty_terminal_output" in payload["summary"]


# --- friction 20588: cortex-authoritative artifact_paths ordering ---


def test_artifact_paths_for_closeout_cortex_first() -> None:
    sidecar = sidecar_workspaces_ref("d-cortex")
    cortex_uri = "cortex://notes/system/closeout.md"
    paths = artifact_paths_for_closeout(sidecar, [cortex_uri], cortex_first=True)
    assert paths == [cortex_uri, sidecar]


def test_artifact_paths_for_closeout_cortex_first_empty_uris_noop() -> None:
    sidecar = sidecar_workspaces_ref("d-noop")
    assert artifact_paths_for_closeout(sidecar, [], cortex_first=True) == [sidecar]


def test_artifact_paths_for_closeout_default_sidecar_first() -> None:
    sidecar = sidecar_workspaces_ref("d-default")
    cortex_uri = "cortex://notes/system/foo.md"
    paths = artifact_paths_for_closeout(sidecar, [cortex_uri])
    assert paths == [sidecar, cortex_uri]


def test_artifact_paths_for_closeout_dedupes_sidecar_in_cortex_list() -> None:
    sidecar = sidecar_workspaces_ref("d-dedup")
    paths = artifact_paths_for_closeout(
        sidecar, [sidecar, "cortex://notes/a.md"], cortex_first=True
    )
    assert paths == ["cortex://notes/a.md", sidecar]


def test_build_implement_closeout_body_cortex_first() -> None:
    outcome = SdkRunOutcome(
        body="done", status="finished", duration_ms=1000, tool_call_count=3
    )
    sidecar_ref = sidecar_workspaces_ref("dc")
    cortex_uri = "cortex://notes/system/pin.md"
    body = build_implement_closeout_body(
        dispatch_id="dc",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="t-cortex",
        work_item_ref="todo:pin-test",
        cortex_artifact_paths=[cortex_uri],
        cortex_first=True,
    )
    payload = json.loads(body)
    assert payload["evidence_uris"]["artifact_paths"] == [cortex_uri, sidecar_ref]


def test_prepare_closeout_delivery_cortex_authoritative_ordering(
    tmp_path: Path,
) -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    cortex_uri = "cortex://notes/system/satisfied.md"
    sidecar_ref = sidecar_workspaces_ref("disp-satisfied")
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-satisfied",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref="todo:satisfied",
        cortex_artifact_paths=[cortex_uri],
        gate_d_created_rels=("notes/system/satisfied.md",),
    )
    payload = json.loads(delivery.body)
    assert payload["evidence_uris"]["artifact_paths"] == [cortex_uri, sidecar_ref]
    assert delivery.sidecar_ref == sidecar_ref
    assert delivery.sidecar_path.is_file()


def test_prepare_closeout_delivery_divergent_pin_keeps_sidecar_first(
    tmp_path: Path,
) -> None:
    """URI present but rel not satisfied must not flip cortex authority."""
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    cortex_uri = "cortex://notes/system/wrong-sandbox.md"
    sidecar_ref = sidecar_workspaces_ref("disp-divergent")
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-divergent",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        cortex_artifact_paths=[cortex_uri],
        gate_d_created_rels=(),
        divergent_rels=(
            "pinned_deliverable_wrong_sandbox:notes/system/wrong-sandbox.md",
        ),
    )
    payload = json.loads(delivery.body)
    assert payload["evidence_uris"]["artifact_paths"] == [sidecar_ref, cortex_uri]


def test_closeout_raw_shell_outside_repo_falsifier(tmp_path: Path) -> None:
    """AC1: outside-repo shell write surfaces path + divergence + partial status."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    mount = tmp_path / "projects"
    source_repo = mount / "universal-llm-gateway"
    _init_git_repo(source_repo)
    outside_rel = "tasks/specs/falsifier.md"
    outside_path = mount / outside_rel
    outside_path.parent.mkdir(parents=True, exist_ok=True)
    outside_path.write_text("# outside repo\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="d-falsifier",
        thread_id="t-falsifier",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(
                        op="shell",
                        target="mkdir -p tasks/specs && cat > tasks/specs/falsifier.md",
                        identity="shell",
                    )
                ],
            )
        },
        coverage={"repo": "partial"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="d-falsifier",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-falsifier",
        work_item_ref="todo:cursor-sdk-stargate-fs-view-divergence",
        baseline={"codes": {}, "hashes": {}, "outside_repo": []},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert outside_rel not in payload.get("files_modified", [])
    assert outside_rel in payload.get("files_outside_repo", [])
    assert payload["files_outside_repo"]
    assert payload["status"] == "complete"
    assert payload["capture_status"] == "complete"
    assert "capture:outside_repo_paths_present" in payload["deviations"]
    assert not any(
        "divergence:unknown_root_child" in deviation
        for deviation in payload["deviations"]
    )


def test_closeout_gitignored_file_in_untracked_surface(
    tmp_path: Path,
) -> None:
    """AC2: gitignored dispatch file lands in files_untracked_or_ignored, not PASS."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "services/rag/property_index/\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".gitignore"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "ignore"],
        check=True,
        capture_output=True,
    )
    rel = "services/rag/property_index/test_skill_vocabulary.py"
    ignored_file = tmp_path / rel
    ignored_file.parent.mkdir(parents=True, exist_ok=True)
    ignored_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="d-gitignored",
        thread_id="t-gitignored",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(op="write", target=rel, identity=rel),
                ],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-gitignored",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-gitignored",
        work_item_ref="todo:cursor-sdk-stargate-fs-view-divergence",
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
        packet_text=f"<scope>\nFiles expected: - `{rel}`\n</scope>\n",
    )
    payload = json.loads(delivery.body)
    assert rel not in payload["files_modified"]
    assert rel not in payload.get("files_created", [])
    assert rel in payload["files_untracked_or_ignored"]
    assert rel in payload["effects"]
    assert "effects" in payload
    assert payload["capture_status"] == "complete"
    assert "divergence:repo_diff_gitignored_present" not in payload["deviations"]
    assert "capture:gitignored_present_unattributed" in payload["deviations"]
    assert "path(s) touched" in payload["summary"]


def test_attribution_effects_paths_swamp_excluded_from_untracked_leg() -> None:
    effects = attribution_effects_paths(
        files_untracked_or_ignored=(
            ".cursor/rules/foo.mdc",
            "services/rag/property_index/test_x.py",
        ),
    )
    assert ".cursor/rules/foo.mdc" not in effects
    assert "services/rag/property_index/test_x.py" in effects


def test_attribution_effects_paths_tracked_untracked_union_sorted() -> None:
    effects = attribution_effects_paths(
        created=("b.py",),
        modified=("a.py",),
        files_untracked_or_ignored=("z.py",),
    )
    assert effects == ("a.py", "b.py", "z.py")


def test_attribution_effects_paths_all_empty_emits_empty_tuple() -> None:
    assert attribution_effects_paths() == ()


def test_build_implement_closeout_body_always_emits_effects_key() -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
    )
    body = build_implement_closeout_body(
        dispatch_id="d-empty-effects",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("d-empty-effects"),
        result_bytes=4,
        thread_id="t1",
        work_item_ref="todo:probe",
        change_set=ChangeSet(created=(), modified=(), deleted=()),
    )
    payload = json.loads(body)
    assert payload["effects"] == []


def test_finalize_closeout_body_preserves_effects_total() -> None:
    payload = {
        "schema_version": 1,
        "status": "complete",
        "summary": "x" * 7900,
        "source_ref": "todo:probe",
        "effects": ["a.py", "b.py", "c.py"],
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
    }
    body = finalize_closeout_body(json.dumps(payload))
    reduced = json.loads(body)
    assert reduced["effects_total"] == 3
    assert reduced["effects"] == ["a.py", "b.py", "c.py"]


def test_prepare_closeout_delivery_light_bounded_gitignored_carries_effects(
    tmp_path: Path,
) -> None:
    """AC10: light-bounded assembly inherits ``effects`` for untracked writes."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("tasks/journal/\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".gitignore"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "ignore"],
        check=True,
        capture_output=True,
    )
    rel = "tasks/journal/review.md"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text("review notes\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="disp-lb-effects",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="write", target=rel, identity=rel)],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="Wrote the review.",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-lb-effects",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
        light_bounded_expected_paths=(rel,),
    )
    payload = json.loads(delivery.body)
    assert rel in payload["effects"]
    assert rel in payload["files_untracked_or_ignored"]


def test_closeout_summary_suffix_only_on_wrapper_not_sidecar_body(
    tmp_path: Path,
) -> None:
    """AM-8: honesty suffix appends to wrapper summary; sidecar prose unchanged."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".gitignore"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "ignore"],
        check=True,
        capture_output=True,
    )
    rel = "ignored/out.txt"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text("data\n", encoding="utf-8")
    agent_body = "## §2 CLOSEOUT\n\nOperator summary here.\n"
    manifest = EffectsManifest(
        dispatch_id="d-suffix",
        thread_id="t-suffix",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="write", target=rel, identity=rel)],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body=agent_body,
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-suffix",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-suffix",
        work_item_ref="todo:probe",
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert "path(s) touched" in payload["summary"]
    sidecar_text = delivery.sidecar_path.read_text(encoding="utf-8")
    assert agent_body.strip() in sidecar_text
    assert "path(s) touched" not in sidecar_text.split("## effects_manifest")[0]


def test_closeout_gitignored_partition_to_effects_integration(
    tmp_path: Path,
) -> None:
    """Decisive falsifier: partition → effects with empty porcelain buckets."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("job/\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".gitignore"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "ignore"],
        check=True,
        capture_output=True,
    )
    rel = "job/test_out.py"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text("x = 1\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="d-falsifier",
        thread_id="t-falsifier",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="write", target=rel, identity=rel)],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-falsifier",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-falsifier",
        work_item_ref="todo:probe",
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["files_created"] == []
    assert payload["files_modified"] == []
    assert payload["files_deleted"] == []
    assert rel in payload["files_untracked_or_ignored"]
    assert payload["effects"] == [rel]
    oversize = {**payload, "summary": "x" * 7000}
    reduced = json.loads(finalize_closeout_body(json.dumps(oversize)))
    assert reduced.get("effects_total") == 1


def test_closeout_source_repo_write_stays_clean(tmp_path: Path) -> None:
    """AC3: normal tracked source_repo write reports complete without fs false positive."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    _init_git_repo(tmp_path)
    rel = "services/git_integration_worker/example.py"
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# ok\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", rel], check=True, capture_output=True
    )
    manifest = EffectsManifest(
        dispatch_id="d-clean",
        thread_id="t-clean",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="edit", target=rel, identity=rel)],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-clean",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-clean",
        work_item_ref="todo:probe",
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
        packet_text=f"<scope>\nFiles expected: - `{rel}`\n</scope>\n",
    )
    payload = json.loads(delivery.body)
    assert rel in payload["files_modified"]
    assert payload["status"] == "complete"
    assert payload["capture_status"] == "complete"
    hard_divergences = [
        d
        for d in payload["deviations"]
        if d.startswith("divergence:")
        and not d.startswith("divergence:repo_diff_paths_unattributed:ambient:")
        and d != "divergence:manifest_vs_git_labels"
    ]
    assert not hard_divergences


def _many_created_paths(count: int = 2000) -> ChangeSet:
    created = tuple(f"services/generated/file_{index:04d}.py" for index in range(count))
    return ChangeSet(created=created, modified=(), deleted=())


def test_finalize_closeout_body_oversize_with_relocation_pointer() -> None:
    change_set = _many_created_paths()
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=3,
    )
    sidecar_ref = sidecar_workspaces_ref("disp-oversize")
    full_body = build_implement_closeout_body(
        dispatch_id="disp-oversize",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_ref,
        result_bytes=4,
        thread_id="t-oversize",
        work_item_ref=None,
        change_set=change_set,
    )
    assert len(full_body) > MAX_TURN_BODY_CHARS
    relocated_uri = (
        "cortex://notes/system/threads/t-oversize-cursor-sdk-closeout-disp-oversize.md"
    )
    body_relocated = {
        "uri": relocated_uri,
        "sha256": "deadbeef",
        "body_chars": len(full_body),
    }
    body = finalize_closeout_body(full_body, body_relocated=body_relocated)
    payload = json.loads(body)
    assert len(body) <= MAX_TURN_BODY_CHARS
    assert payload["body_relocated"]["uri"] == relocated_uri
    assert payload["files_created_total"] == 2000
    assert len(payload["files_created"]) == 5
    assert "effects_manifest" not in payload


def _oversize_effects_manifest(
    *,
    dispatch_id: str,
    thread_id: str,
    count: int = 2000,
) -> object:
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    entries = [
        EffectEntry(
            op="write",
            target=f"services/generated/file_{index:04d}.py",
            identity=f"services/generated/file_{index:04d}.py",
        )
        for index in range(count)
    ]
    return EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=entries,
            )
        },
        coverage={"repo": "complete"},
    )


def test_prepare_closeout_delivery_oversize_uses_cortex_sidecar(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    manifest = _oversize_effects_manifest(
        dispatch_id="disp-cortex",
        thread_id="t-oversize",
    )
    _materialize_manifest_repo_files(tmp_path, manifest)
    outcome = SdkRunOutcome(
        body="status: complete — done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
        capture_branch="B",
    )
    relocated_uri = (
        "cortex://notes/system/threads/t-oversize-cursor-sdk-closeout-disp-cortex.md"
    )

    def stub_cortex_writer(
        *,
        full_body: str,
        dispatch_id: str,
        thread_id: str,
        **_: object,
    ) -> dict[str, str | int]:
        return {
            "uri": relocated_uri,
            "sha256": hashlib.sha256(full_body.encode("utf-8")).hexdigest(),
            "body_chars": len(full_body),
        }

    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-cortex",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-oversize",
        work_item_ref=None,
        baseline={"codes": {}, "hashes": {}, "outside_repo": []},
        post_closeout_sidecar_fn=stub_cortex_writer,
    )
    payload = json.loads(delivery.body)
    assert len(delivery.body) <= MAX_TURN_BODY_CHARS
    assert payload["body_relocated"]["uri"] == relocated_uri
    assert payload["files_created_total"] == 2000
    assert delivery.closeout_status == CloseoutStatus.COMPLETE


def test_prepare_closeout_delivery_oversize_repo_sidecar_fallback(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    manifest = _oversize_effects_manifest(
        dispatch_id="disp-repo-fallback",
        thread_id="t-oversize",
    )
    _materialize_manifest_repo_files(tmp_path, manifest)
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
        capture_branch="B",
    )

    def stub_cortex_writer(**_: object) -> None:
        return None

    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-repo-fallback",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-oversize",
        work_item_ref=None,
        baseline={"codes": {}, "hashes": {}, "outside_repo": []},
        post_closeout_sidecar_fn=stub_cortex_writer,
    )
    payload = json.loads(delivery.body)
    assert len(delivery.body) <= MAX_TURN_BODY_CHARS
    assert payload["body_relocated"]["uri"] == delivery.sidecar_ref
    sidecar_text = delivery.sidecar_path.read_text(encoding="utf-8")
    assert "## structured_closeout_full" in sidecar_text
    full_json = sidecar_text.split("## structured_closeout_full\n\n", 1)[1]
    full_payload = json.loads(full_json)
    assert len(full_payload["files_created"]) == 2000


def test_prepare_closeout_delivery_normal_size_has_no_body_relocated(
    tmp_path: Path,
) -> None:
    outcome = SdkRunOutcome(
        body="status: complete — done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-normal",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-normal",
        work_item_ref=None,
    )
    payload = json.loads(delivery.body)
    assert len(delivery.body) <= MAX_TURN_BODY_CHARS
    assert "body_relocated" not in payload
    assert payload["status"] == "complete"
    sidecar_text = delivery.sidecar_path.read_text(encoding="utf-8")
    assert "## structured_closeout_full" in sidecar_text
    full_json = sidecar_text.split("## structured_closeout_full\n\n", 1)[1]
    full_payload = json.loads(full_json)
    assert full_payload["schema_version"] == 1
    assert "files_ambient_repo_movement" in full_payload


def test_prepare_closeout_delivery_structured_receipt_write_failure_is_loud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    emit_mock = MagicMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events.emit_sdk_closeout_sidecar_receipt_failed",
        emit_mock,
    )

    def _fail_append(sidecar_path: Path, full_body: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_deliverables.append_structured_closeout_full_to_repo_sidecar",
        _fail_append,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-receipt-fail",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-fail",
        work_item_ref=None,
    )
    payload = json.loads(delivery.body)
    assert any(
        str(d).startswith("closeout:structured_receipt_sidecar_failed:")
        for d in (payload.get("deviations") or [])
    )
    assert payload["capture_status"] == "partial"
    emit_mock.assert_called_once()


def test_reconcile_segregates_outside_repo_from_modified(tmp_path: Path) -> None:
    """AC1: outside-repo snapshot deltas land in files_outside_repo, not files_modified."""
    from services.git_integration_worker.cursor_sdk_closeout import (
        reconcile_workspace_changes,
    )

    mount = tmp_path / "projects"
    source_repo = mount / "universal-llm-gateway"
    source_repo.mkdir(parents=True)
    _init_git_repo(source_repo)
    tracked_paths = [f"services/tracked_{i}.py" for i in range(5)]
    for rel in tracked_paths:
        path = source_repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(source_repo), "add", *tracked_paths],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    for rel in tracked_paths:
        (source_repo / rel).write_text(f"# {rel} modified\n", encoding="utf-8")
    outside_paths = [f"tasks/outside_{i}.md" for i in range(900)]
    for rel in outside_paths:
        path = mount / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("outside\n", encoding="utf-8")
    baseline = {
        "codes": {rel: " M" for rel in tracked_paths},
        "hashes": {rel: _sha256_hex(f"# {rel}\n") for rel in tracked_paths},
        "outside_repo": [],
    }
    git_change, _, outside_repo, _polarity_deviations = reconcile_workspace_changes(
        source_repo=source_repo,
        baseline=baseline,
        manifest=None,
        mount_root=mount,
    )
    assert len(git_change.modified) == 5
    assert len(git_change.created) == 0
    assert len(outside_repo) == 900


def test_prepare_closeout_segregates_outside_repo_totals(tmp_path: Path) -> None:
    """AC1: closeout body reports tracked-only files_modified + files_outside_repo."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    mount = tmp_path / "projects"
    source_repo = mount / "universal-llm-gateway"
    source_repo.mkdir(parents=True)
    _init_git_repo(source_repo)
    tracked_paths = [f"services/tracked_{i}.py" for i in range(5)]
    for rel in tracked_paths:
        path = source_repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(source_repo), "add", *tracked_paths],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    baseline = capture_wt_baseline_with_hashes(source_repo)
    assert baseline is not None
    for rel in tracked_paths:
        (source_repo / rel).write_text(f"# {rel} modified\n", encoding="utf-8")
    outside_paths = [f"tasks/outside_{i}.md" for i in range(900)]
    for rel in outside_paths:
        path = mount / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("outside\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="disp-segregate",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(op="edit", target=rel, identity=rel)
                    for rel in tracked_paths
                ],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="disp-segregate",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline=baseline,
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["files_modified_total"] == 5
    assert payload["files_created_total"] == 0
    assert payload["files_outside_repo_total"] == 900
    assert len(payload.get("files_outside_repo", [])) <= 5


def test_repo_change_set_from_manifest_drops_non_file_entries(tmp_path: Path) -> None:
    """AC2: directories and '.' never appear in files_created."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    from services.git_integration_worker.cursor_sdk_manifest import (
        repo_change_set_from_manifest,
    )

    mount = tmp_path / "projects"
    source_repo = mount / "universal-llm-gateway"
    source_repo.mkdir(parents=True)
    _init_git_repo(source_repo)
    libs_dir = mount / "libs"
    libs_dir.mkdir()
    real_rel = "services/real.py"
    real = source_repo / real_rel
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("# ok\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="d-hygiene",
        thread_id="t-hygiene",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(op="write", target=".", identity="."),
                    EffectEntry(op="write", target=str(libs_dir), identity="libs"),
                    EffectEntry(op="write", target=real_rel, identity=real_rel),
                ],
            )
        },
        coverage={"repo": "complete"},
    )
    change_set, outside, dropped = repo_change_set_from_manifest(
        manifest,
        source_repo=source_repo,
        mount_root=mount,
    )
    assert change_set is not None
    assert change_set.created == (real_rel,)
    assert dropped
    assert outside == ()


def test_prepare_closeout_non_file_manifest_deviation(tmp_path: Path) -> None:
    """AC2: non-file manifest drops surface capture:non_file_manifest_entry_dropped."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    mount = tmp_path / "projects"
    source_repo = mount / "universal-llm-gateway"
    source_repo.mkdir(parents=True)
    _init_git_repo(source_repo)
    (mount / "libs").mkdir()
    real_rel = "services/real.py"
    (source_repo / real_rel).parent.mkdir(parents=True, exist_ok=True)
    (source_repo / real_rel).write_text("# ok\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="d-hygiene",
        thread_id="t-hygiene",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(op="write", target=".", identity="."),
                    EffectEntry(op="write", target=str(mount / "libs"), identity="libs"),
                    EffectEntry(op="write", target=real_rel, identity=real_rel),
                ],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="d-hygiene",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-hygiene",
        work_item_ref=None,
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["files_created"] == [real_rel]
    assert "capture:non_file_manifest_entry_dropped" in payload["deviations"]


def test_relocated_sidecar_pretty_printed_multiline(tmp_path: Path) -> None:
    """AC3: relocated sidecar is multi-line; sha256 matches written bytes."""
    from services.git_integration_worker.cursor_sdk_deliverables import (
        pretty_relocated_closeout_body,
        relocate_oversize_closeout_body_sync,
    )

    change_set = _many_created_paths(count=50)
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
    )
    full_body = build_implement_closeout_body(
        dispatch_id="disp-pretty",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref=sidecar_workspaces_ref("disp-pretty"),
        result_bytes=4,
        thread_id="t-pretty",
        work_item_ref=None,
        change_set=change_set,
    )
    sidecar_path = tmp_path / "sidecar.md"
    sidecar_path.write_text("composer output\n", encoding="utf-8")
    meta, tier = relocate_oversize_closeout_body_sync(
        full_body=full_body,
        sidecar_path=sidecar_path,
        sidecar_ref=sidecar_workspaces_ref("disp-pretty"),
        dispatch_id="disp-pretty",
        thread_id="t-pretty",
    )
    pretty = pretty_relocated_closeout_body(full_body)
    assert "\n" in pretty
    sidecar_text = sidecar_path.read_text(encoding="utf-8")
    relocated_json = sidecar_text.split("## structured_closeout_full\n\n", 1)[1]
    assert "\n" in relocated_json
    assert meta["sha256"] == hashlib.sha256(relocated_json.encode("utf-8")).hexdigest()
    assert meta["body_chars"] == len(relocated_json)
    assert tier == "repo_sidecar"


# Post-25024 golden: gitignored expected on disk is non-degrading; dirty baseline still partial.


def test_mixed_vector_capture_golden_unchanged(tmp_path: Path) -> None:
    """AC5/6: segregated body fields; capture classification byte-identical pre-change."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    mount = tmp_path / "projects"
    source_repo = mount / "universal-llm-gateway"
    source_repo.mkdir(parents=True)
    _init_git_repo(source_repo)
    tracked_rel = "services/foo.py"
    tracked = source_repo / tracked_rel
    tracked.parent.mkdir(parents=True)
    tracked.write_text("# tracked\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(source_repo), "add", tracked_rel],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    baseline = capture_wt_baseline_with_hashes(source_repo)
    assert baseline is not None
    tracked.write_text("# modified\n", encoding="utf-8")
    outside_rel = "tasks/outside.md"
    outside = mount / outside_rel
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("outside\n", encoding="utf-8")
    (mount / "libs").mkdir()
    (source_repo / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    ignored_rel = "ignored.py"
    (source_repo / ignored_rel).write_text("ignored\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="d-mv",
        thread_id="t-mv",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(op="edit", target=tracked_rel, identity=tracked_rel),
                    EffectEntry(op="write", target=".", identity="."),
                    EffectEntry(op="write", target=str(mount / "libs"), identity="libs"),
                    EffectEntry(op="write", target=ignored_rel, identity=ignored_rel),
                ],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="d-mv",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-mv",
        work_item_ref="todo:mv",
        baseline=baseline,
        deliverables_expected=True,
        packet_text=f"<scope>\nFiles expected: - `{tracked_rel}`\n</scope>\n",
    )
    payload = json.loads(delivery.body)
    assert payload["files_modified"] == [tracked_rel]
    assert outside_rel in payload["files_outside_repo"]
    assert ignored_rel in payload["files_untracked_or_ignored"]
    assert "." not in payload.get("files_created", [])
    assert payload["capture_status"] == "complete"
    assert payload.get("divergence_reason") is None
    golden_deviations = {
        "capture:gitignored_present_unattributed",
        "capture:outside_repo_paths_present",
        "capture:non_file_manifest_entry_dropped",
        "divergence:manifest_vs_git_labels",
        "divergence:repo_diff_paths_unattributed:ambient:.gitignore,tmp/reviews/closeouts/d-mv.md",
    }
    assert set(payload["deviations"]) == golden_deviations


# --- a:25024 shared-master capture scoring (AC1–AC12) ---


def test_25024_class_fixture_complete_with_ambient_deviations(tmp_path: Path) -> None:
    """AC1/AC2: 833ed72cda94-class mix — complete when expected evidenced + ambient visible."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    mount = tmp_path / "projects"
    source_repo = mount / "universal-llm-gateway"
    source_repo.mkdir(parents=True)
    _init_git_repo(source_repo)
    expected_rel = "services/git_integration_worker/scoring.py"
    expected = source_repo / expected_rel
    expected.parent.mkdir(parents=True)
    expected.write_text("# dispatch land\n", encoding="utf-8")
    admit_hash = _sha256_hex("# dispatch land\n")
    subprocess.run(
        ["git", "-C", str(source_repo), "add", expected_rel],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source_repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    expected.write_text("# dispatch land v2\n", encoding="utf-8")
    ambient_rel = "services/parallel_wip.py"
    (source_repo / ambient_rel).write_text("# peer\n", encoding="utf-8")
    outside_rel = "tasks/outside.md"
    (mount / outside_rel).parent.mkdir(parents=True, exist_ok=True)
    (mount / outside_rel).write_text("outside\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="833ed72cda94",
        thread_id="t-25024",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[
                    EffectEntry(op="edit", target=expected_rel, identity=expected_rel),
                    EffectEntry(
                        op="shell",
                        target="echo via shell",
                        identity="shell",
                    ),
                ],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=source_repo,
        dispatch_id="833ed72cda94",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-25024",
        work_item_ref="todo:shared-master-capture-scoring",
        baseline={
            "codes": {expected_rel: " M"},
            "hashes": {expected_rel: admit_hash},
            "outside_repo": [],
        },
        deliverables_expected=True,
        packet_text=f"<scope>\nFiles expected: - `{expected_rel}`\n</scope>\n",
    )
    payload = json.loads(delivery.body)
    assert payload["status"] == "complete"
    assert payload["capture_status"] == "complete"
    assert any(
        d.startswith("divergence:repo_diff_paths_unattributed:ambient:")
        for d in payload["deviations"]
    )
    assert "capture:outside_repo_paths_present" in payload["deviations"]
    assert "capture:shell_repo_writes_unverified" in payload["deviations"]


def test_25024_expected_gitignored_absent_partial(tmp_path: Path) -> None:
    """AC4/F7: expected gitignored path absent on disk stays partial."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    _init_git_repo(tmp_path)
    rel = "services/rag/property_index/missing.py"
    (tmp_path / ".gitignore").write_text("services/rag/property_index/\n", encoding="utf-8")
    manifest = EffectsManifest(
        dispatch_id="d-absent-gitignored",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="write", target=rel, identity=rel)],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
        capture_branch="B",
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-absent-gitignored",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref="todo:shared-master-capture-scoring",
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
        packet_text=f"<scope>\nFiles expected: - `{rel}`\n</scope>\n",
    )
    payload = json.loads(delivery.body)
    assert payload["capture_status"] == "partial"
    assert any(
        token in payload["deviations"]
        for token in (
            "divergence:repo_diff_gitignored_present",
            "divergence:emitted_path_absent:services/rag/property_index/missing.py",
        )
    )


def test_25024_true_missing_expected_partial(tmp_path: Path) -> None:
    """AC6: promised deliverable never landed → partial."""
    _init_git_repo(tmp_path)
    missing = "services/missing.py"
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-missing",
        outcome=outcome,
        degraded_reason=None,
        thread_id="t1",
        work_item_ref="todo:shared-master-capture-scoring",
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
        packet_text=f"<scope>\nFiles expected: - `{missing}`\n</scope>\n",
    )
    payload = json.loads(delivery.body)
    assert payload["status"] == "partial"
    assert payload["capture_status"] == "partial"


def test_25024_isolated_worktree_outside_repo_hard_fail(tmp_path: Path) -> None:
    """AC8: isolated worktree preserves hard-fail for outside-repo violations."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    from services.git_integration_worker.cursor_sdk_capture_divergence import (
        closeout_divergence_reason,
    )
    from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    outside = "tasks/outside.md"
    manifest = EffectsManifest(
        dispatch_id="d-isolated",
        thread_id="t1",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="write", target="services/x.py", identity="x")],
            )
        },
    )
    reason = closeout_divergence_reason(
        deliverables_expected=True,
        degraded_reason=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        files_expected=["services/x.py"],
        divergent_rels=(),
        source_repo=source_repo,
        cortex_root=tmp_path / "cortex",
        manifest=manifest,
        outside_repo_paths=(outside,),
        worktree_isolated=True,
    )
    assert reason == f"divergence:unknown_root_child:{outside}"


def test_25024_no_worktree_isolated_on_shared_master_delivery(tmp_path: Path) -> None:
    """AC10: production closeout path never wires worktree_isolated=True."""
    import inspect

    from services.git_integration_worker import cursor_sdk_closeout as closeout_mod

    source = inspect.getsource(closeout_mod.prepare_closeout_delivery)
    assert "worktree_isolated=True" not in source
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="d-lane-a",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=50,
            tool_call_count=1,
        ),
        degraded_reason=None,
        thread_id="t1",
        work_item_ref=None,
        baseline={"codes": {}, "hashes": {}},
        deliverables_expected=True,
    )
    payload = json.loads(delivery.body)
    assert payload["status"] in {"complete", "partial"}


def test_25024_manifest_authority_classify_unchanged(tmp_path: Path) -> None:
    """AC9: classify_capture_status manifest coverage gates remain authoritative."""
    from implement_admission.closeout_models import EffectsManifest

    from services.git_integration_worker.cursor_sdk_capture_status import (
        classify_capture_status,
    )

    manifest = EffectsManifest(
        dispatch_id="d1",
        thread_id="t1",
        coverage={"repo": "partial"},
    )
    assert (
        classify_capture_status(
            deliverables_expected=True,
            baseline={"codes": {}, "hashes": {}},
            files_expected=["services/x.py"],
            manifest=manifest,
            baseline_has_hashes=True,
        )
        == "partial"
    )


def test_close_contract_lead_stays_active_after_terminal(bus_db) -> None:
    """AC4: close_contract=lead keeps thread active after terminal closeout."""
    from agent_bus_store.close_on_read import CLOSE_ON_READ_TAG
    from agent_bus_store.db import admit_dispatch, create_thread_with_turn
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.threads import set_thread_tags
    from agent_bus_store.turns_models import ThreadStatus
    from systems.frontier_consult.cursor_sdk_worker_dispatch import (
        assemble_cursor_sdk_generate_tags,
    )

    thread_row, *_ = create_thread_with_turn(
        slug="sdk-lead-close",
        from_agent="dispatch",
        to_agent="cursor-sdk:dispatch:exec-lead",
        subject="implement",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    tags = assemble_cursor_sdk_generate_tags(
        ["cursor-sdk-generate", "type:generate", "contract:implement"],
        close_contract="lead",
    )
    with connect() as conn:
        set_thread_tags(conn, thread_id, tags)
    assert CLOSE_ON_READ_TAG not in tags
    assert "bus_lifecycle:persistent" in tags
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-lead",
        pipeline_id="cursor-sdk-generate",
    )
    resp = bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch closeout",
            "body": "closeout summary",
            "after_turn": 1,
        },
    )
    assert resp.status_code == 201
    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={
            "terminal_status": "completed",
            "execution_id": "exec-lead",
            "bus_lifecycle": "persistent",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.ACTIVE


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from agent_bus_store.db import init_db

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    from fastapi.testclient import TestClient

    yield TestClient(app)
    app.dependency_overrides.clear()


# --- sdk019 closeout-correctness ---


def test_merge_degraded_reasons_singular_first() -> None:
    assert merge_degraded_reasons("zero_tool_calls", "sdk_fs_mismatch") == (
        "zero_tool_calls",
        "sdk_fs_mismatch",
    )


def test_degraded_reasons_from_exception_taxonomy() -> None:
    from cursor_sdk.errors import (
        AgentBusyError,
        AgentNotFoundError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        ConfigurationError,
        CursorSDKError,
        IntegrationNotConnectedError,
        InternalServerError,
        NetworkError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
        UnsupportedRunOperationError,
    )

    assert degraded_reasons_from_exception(RateLimitError("slow")) == (
        "sdk_rate_limited",
    )
    assert degraded_reasons_from_exception(AgentBusyError("wait")) == ("sdk_agent_busy",)
    assert degraded_reasons_from_exception(AuthenticationError("nope")) == (
        "sdk_auth_failed",
    )
    assert degraded_reasons_from_exception(NotFoundError("gone")) == (
        "sdk_run_not_found",
    )
    assert degraded_reasons_from_exception(APITimeoutError("late")) == ("sdk_timeout",)
    assert degraded_reasons_from_exception(PermissionDeniedError("denied")) == (
        "sdk_permission_denied",
    )
    assert degraded_reasons_from_exception(BadRequestError("bad")) == (
        "sdk_bad_request",
    )
    assert degraded_reasons_from_exception(
        IntegrationNotConnectedError("x", provider="gh", help_url="https://h")
    ) == ("sdk_integration_not_connected",)
    assert degraded_reasons_from_exception(
        UnsupportedRunOperationError("cancel")
    ) == ("sdk_unsupported_run_operation",)
    assert degraded_reasons_from_exception(ConfigurationError("cfg")) == (
        "sdk_configuration",
    )
    assert degraded_reasons_from_exception(InternalServerError("500")) == (
        "sdk_internal_server",
    )
    assert degraded_reasons_from_exception(NetworkError("net")) == ("sdk_network",)
    assert degraded_reasons_from_exception(AgentNotFoundError("missing")) == (
        "sdk_agent_not_found",
    )
    bare = CursorSDKError("opaque", code="weird_code")
    assert degraded_reasons_from_exception(bare) == ("sdk_error:weird_code",)


def test_degraded_reasons_collapsed_subclasses_are_distinct() -> None:
    """Roadmap item 4 falsifier: class-distinct failures → distinct tokens."""
    from cursor_sdk.errors import BadRequestError, PermissionDeniedError

    bad_req = degraded_reasons_from_exception(BadRequestError("invalid arg"))
    perm = degraded_reasons_from_exception(PermissionDeniedError("forbidden"))
    assert bad_req != perm
    assert bad_req == ("sdk_bad_request",)
    assert perm == ("sdk_permission_denied",)


def test_degraded_reasons_sdk_run_aborted_unwraps_typed_cause() -> None:
    from cursor_sdk.errors import APITimeoutError

    from services.git_integration_worker.routes.cursor_sdk import SdkRunAbortedError

    wrapped = SdkRunAbortedError("abort", forensics={"cause": "x"})
    wrapped.__cause__ = APITimeoutError("read timeout")
    assert degraded_reasons_from_exception(wrapped) == ("sdk_timeout",)


def test_degraded_reasons_sdk_run_aborted_without_typed_cause() -> None:
    from services.git_integration_worker.routes.cursor_sdk import SdkRunAbortedError

    wrapped = SdkRunAbortedError("abort", forensics={"cause": "x"})
    assert degraded_reasons_from_exception(wrapped) == ("bridge_read_timeout",)


def test_extract_sdk_git_snapshot() -> None:
    branch = type("Branch", (), {"repo_url": "r", "branch": "main", "pr_url": "p"})()
    git_info = type("Git", (), {"branches": (branch,)})()
    assert extract_sdk_git_snapshot(git_info) == {
        "repo_url": "r",
        "branch": "main",
        "pr_url": "p",
    }


def test_sdk_fs_git_mismatch_reason(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "feature/sdk019"],
        check=True,
        capture_output=True,
    )
    reason = sdk_fs_git_mismatch_reason(
        {"branch": "main", "repo_url": "x", "pr_url": None},
        tmp_path,
    )
    assert reason == "sdk_fs_mismatch"


def test_stream_only_effect_deviations() -> None:
    from services.git_integration_worker.cursor_sdk_stream_capture import (
        ToolCallObservation,
    )

    stream_call = ToolCallObservation(
        call_id="1",
        tool_name="fs",
        status="completed",
        arg_bytes=1,
        result_bytes=1,
        truncated_fields=(),
    )
    assert stream_only_effect_deviations(
        stream_tool_calls=(stream_call,),
        conversation_tool_call_count=0,
    ) == ("stream_only_effect",)


def test_read_post_wait_snapshot_polls_empty_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    class _Run:
        def conversation(self) -> list[object]:
            calls["n"] += 1
            return [] if calls["n"] == 1 else [object()]

    result = type("Result", (), {"status": "finished", "git": None})()
    snapshot = read_post_wait_snapshot(
        run=_Run(),
        agent=type("Agent", (), {})(),
        result=result,
        poll_fallback=True,
    )
    assert calls["n"] >= 2
    assert snapshot.conversation


def test_observe_run_stream_captures_request_id() -> None:
    from services.git_integration_worker.cursor_sdk_stream_capture import (
        observe_run_stream,
    )

    request_msg = type(
        "SDKRequestMessage",
        (),
        {"type": "request", "request_id": "req-stream-1", "call_id": ""},
    )()

    class _Event:
        interaction_update = None
        sdk_message = request_msg

    class _Run:
        def events(self):
            yield _Event()

    capture = observe_run_stream(
        _Run(),
        dispatch_id="d-req",
        thread_id="t-req",
        resolved_model="composer-2.5",
    )
    assert capture.sdk_request_id == "req-stream-1"
    assert capture.request_id_source == "stream"


def test_closeout_status_enum_unchanged_sdk019() -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=1,
        degraded_reasons=("sdk_fs_mismatch",),
    )
    body = build_implement_closeout_body(
        dispatch_id="sdk019",
        outcome=outcome,
        degraded_reason="zero_tool_calls",
        sidecar_ref=sidecar_workspaces_ref("sdk019"),
        result_bytes=4,
        thread_id="t",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] in {"complete", "partial", "failed"}
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=1,
        degraded_reasons=("sdk_fs_mismatch",),
    )
    body = build_implement_closeout_body(
        dispatch_id="sdk019",
        outcome=outcome,
        degraded_reason="zero_tool_calls",
        sidecar_ref=sidecar_workspaces_ref("sdk019"),
        result_bytes=4,
        thread_id="t",
        work_item_ref=None,
    )
    payload = json.loads(body)
    assert payload["status"] in {"complete", "partial", "failed"}


def test_closeout_registers_attributed_paths_in_seat_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice A — attributed repo_change_set rows land under dispatch arc."""
    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    from services.git_integration_worker.config import WorkerConfig
    from services.git_integration_worker.cursor_sdk_closeout import (
        _assemble_closeout_delivery,
    )
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    _init_git_repo_with_commit(tmp_path)
    dispatch_id = "disp-register"
    rel = "services/written.py"
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text("x=1\n", encoding="utf-8")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None

    SeatWriteLedger.reset_instance()
    ledger = SeatWriteLedger(db_path=tmp_path.parent / "seat-write-ledger.db")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.SeatWriteLedger.instance",
        lambda: ledger,
    )
    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=tmp_path,
        worktree_root=tmp_path / "wt",
        dispatch_workspace=tmp_path / "ws",
        green_gate_cmd=["true"],
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.load_config",
        lambda: cfg,
    )

    manifest = EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id="t-reg",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="write", target=rel, identity=rel)],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=2,
        effects_manifest=manifest,
    )
    _assemble_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-reg",
        work_item_ref=None,
        baseline=baseline,
        packet_text=f"<scope>\nFiles expected:\n- `{rel}`\n</scope>\n",
        files_expected=[rel],
        cortex_artifact_paths=[],
        gate_d_created_rels=(),
        deliverables_expected=True,
    )
    assert ledger.has_paths_for_arc(arc_id=dispatch_id) is True
    registered = ledger.registered_paths(source_repo=str(tmp_path))
    assert rel in registered


def test_closeout_does_not_register_ambient_only_git_delta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice A — ambient dirty paths diverted by resolve are not ledger-seeded."""
    from services.git_integration_worker.config import WorkerConfig
    from services.git_integration_worker.cursor_sdk_closeout import (
        _assemble_closeout_delivery,
    )
    from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

    _init_git_repo_with_commit(tmp_path)
    dispatch_id = "disp-ambient"
    ambient = "parallel_wip.py"
    (tmp_path / ambient).write_text("ambient\n", encoding="utf-8")
    baseline = capture_wt_baseline_with_hashes(tmp_path)
    assert baseline is not None
    expected = "services/attributed.py"
    (tmp_path / expected).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / expected).write_text("lane\n", encoding="utf-8")

    SeatWriteLedger.reset_instance()
    ledger = SeatWriteLedger(db_path=tmp_path.parent / "seat-write-ledger.db")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.SeatWriteLedger.instance",
        lambda: ledger,
    )
    cfg = WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=tmp_path,
        worktree_root=tmp_path / "wt",
        dispatch_workspace=tmp_path / "ws",
        green_gate_cmd=["true"],
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.load_config",
        lambda: cfg,
    )

    from implement_admission.closeout_models import (
        EffectEntry,
        EffectsManifest,
        SurfaceSection,
    )

    manifest = EffectsManifest(
        dispatch_id=dispatch_id,
        thread_id="t-ambient",
        capture_sources=["conversation"],
        surfaces={
            "repo": SurfaceSection(
                surface="repo",
                source="conversation",
                entries=[EffectEntry(op="write", target=expected, identity=expected)],
            )
        },
        coverage={"repo": "complete"},
    )
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=50,
        tool_call_count=1,
        effects_manifest=manifest,
    )
    _assemble_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=None,
        thread_id="t-ambient",
        work_item_ref=None,
        baseline=baseline,
        packet_text=f"<scope>\nFiles expected:\n- `{expected}`\n</scope>\n",
        files_expected=[expected],
        cortex_artifact_paths=[],
        gate_d_created_rels=(),
        deliverables_expected=True,
    )
    registered = ledger.registered_paths(source_repo=str(tmp_path))
    assert expected in registered
    assert ambient not in registered
