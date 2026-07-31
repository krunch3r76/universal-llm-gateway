"""Hermetic tests for cursor-sdk dispatch report verification (Gate D)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from implement_admission.closeout import apply_closeout, run_adapters
from implement_admission.closeout_models import (
    EvidenceUris,
    ImplementCloseout,
)
from implement_admission.closeout_runtime import (
    CloseoutRuntime,
    reset_runtime,
    set_runtime,
)
from implement_admission.deliverable_verification import (
    GATE_D_PREFIX,
    apply_closeout_gate_d,
    build_gate_d_verification,
    evaluate_deliverable_verification,
    gate_d_passed,
)
from implement_admission.spec import (
    Acceptance,
    Closeout,
    CloseoutAdapterKind,
    CloseoutStatus,
    ExecutorStyle,
    ImplementSpec,
    Intent,
    OrchestrationMode,
    Readiness,
    ReadinessState,
    Routing,
    RoutingDerivation,
    Scope,
    Source,
    SourceKind,
    finalize_spec,
)

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    capture_wt_baseline,
    changed_paths,
    prepare_closeout_delivery,
    verify_deliverables,
)
from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    reset_runtime()


def _init_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _ready_spec(*, files_expected: list[str]) -> ImplementSpec:
    return finalize_spec(
        ImplementSpec(
            source=Source(
                source_ref="todo:verify-me",
                canonical_ref="todo:verify-me",
                source_kind=SourceKind.TODO,
            ),
            intent=Intent(summary="verify deliverables"),
            scope=Scope(files_expected=files_expected, bounded=True),
            readiness=Readiness(state=ReadinessState.READY),
            routing=Routing(
                orchestration_mode=OrchestrationMode.SINGLE,
                executor_style=ExecutorStyle.MECHANICAL,
                derivation=RoutingDerivation(mode_rule="m", style_rule="s"),
            ),
            acceptance=Acceptance(criteria=["done"]),
            closeout=Closeout(adapter=CloseoutAdapterKind.TODO),
        )
    )


def _outcome(**kwargs: object) -> SdkRunOutcome:
    defaults = {
        "body": "done",
        "status": "finished",
        "duration_ms": 1000,
        "tool_call_count": 3,
    }
    defaults.update(kwargs)
    return SdkRunOutcome(**defaults)  # type: ignore[arg-type]


@pytest.mark.offline
def test_baseline_isolates_preexisting_drift(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    target = repo / "libs" / "foo.py"
    target.parent.mkdir(parents=True)
    target.write_text("pre\n", encoding="utf-8")
    subprocess.run(["git", "add", "libs/foo.py"], cwd=repo, check=True)
    baseline = capture_wt_baseline(repo)
    target.write_text("pre\nchanged\n", encoding="utf-8")
    delta = changed_paths(repo, baseline)
    assert "libs/foo.py" in delta.modified


@pytest.mark.offline
def test_verify_passes_when_expected_file_touched(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    baseline = capture_wt_baseline(repo)
    touched = repo / "services" / "git_integration_worker" / "routes" / "cursor_sdk.py"
    touched.parent.mkdir(parents=True, exist_ok=True)
    touched.write_text("# edit\n", encoding="utf-8")
    sidecar = tmp_path / "sidecar.md"
    sidecar.write_text("x", encoding="utf-8")
    change_set = changed_paths(repo, baseline)
    verification = verify_deliverables(
        spec=None,
        change_set=change_set,
        outcome=_outcome(),
        sidecar_path=sidecar,
        files_expected=[str(touched.relative_to(repo))],
        baseline=baseline,
        source_repo=repo,
    )
    assert gate_d_passed(
        ImplementCloseout(
            status=CloseoutStatus.COMPLETE,
            summary="x",
            source_ref="todo:verify-me",
            verification=verification,
        )
    )


@pytest.mark.offline
def test_verify_no_expected_files_touched(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    baseline = capture_wt_baseline(repo)
    change_set = changed_paths(repo, baseline)
    sidecar = tmp_path / "sidecar.md"
    sidecar.write_text("x", encoding="utf-8")
    verification = verify_deliverables(
        spec=None,
        change_set=change_set,
        outcome=_outcome(),
        sidecar_path=sidecar,
        files_expected=["services/missing.py"],
        baseline=baseline,
        source_repo=repo,
    )
    assert any(
        v.command == f"{GATE_D_PREFIX}no_expected_files_touched" for v in verification
    )
    assert not gate_d_passed(
        ImplementCloseout(
            status=CloseoutStatus.COMPLETE,
            summary="x",
            source_ref="todo:verify-me",
            verification=verification,
        )
    )


@pytest.mark.offline
def test_verify_expected_present_on_disk_uncaptured(tmp_path: Path) -> None:
    """AC1: on-disk expected file absent from capture → honest partial reason."""
    repo = _init_git_repo(tmp_path)
    expected = repo / "services" / "uncaptured.py"
    expected.parent.mkdir(parents=True)
    expected.write_text("# present on disk\n", encoding="utf-8")
    subprocess.run(["git", "add", "services/uncaptured.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add file"], cwd=repo, check=True)
    sidecar = tmp_path / "sidecar.md"
    sidecar.write_text("x", encoding="utf-8")
    change_set = ChangeSet(created=(), modified=(), deleted=())
    verification = verify_deliverables(
        spec=None,
        change_set=change_set,
        outcome=_outcome(),
        sidecar_path=sidecar,
        files_expected=["services/uncaptured.py"],
        baseline={},
        source_repo=repo,
    )
    assert any(
        v.command == f"{GATE_D_PREFIX}expected_present_on_disk_uncaptured"
        for v in verification
    )
    assert not any(
        v.command == f"{GATE_D_PREFIX}no_expected_files_touched" for v in verification
    )
    assert not gate_d_passed(
        ImplementCloseout(
            status=CloseoutStatus.COMPLETE,
            summary="x",
            source_ref="todo:verify-me",
            verification=verification,
        )
    )


@pytest.mark.offline
def test_verify_source_repo_none_keeps_no_expected_files_touched(tmp_path: Path) -> None:
    """AC2: without source_repo, absent capture still reports no_expected_files_touched."""
    repo = _init_git_repo(tmp_path)
    expected = repo / "services" / "uncaptured.py"
    expected.parent.mkdir(parents=True)
    expected.write_text("# present on disk\n", encoding="utf-8")
    subprocess.run(["git", "add", "services/uncaptured.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add file"], cwd=repo, check=True)
    sidecar = tmp_path / "sidecar.md"
    sidecar.write_text("x", encoding="utf-8")
    change_set = ChangeSet(created=(), modified=(), deleted=())
    verification = verify_deliverables(
        spec=None,
        change_set=change_set,
        outcome=_outcome(),
        sidecar_path=sidecar,
        files_expected=["services/uncaptured.py"],
        baseline={},
        source_repo=None,
    )
    assert any(
        v.command == f"{GATE_D_PREFIX}no_expected_files_touched" for v in verification
    )
    assert not any(
        v.command == f"{GATE_D_PREFIX}expected_present_on_disk_uncaptured"
        for v in verification
    )


@pytest.mark.offline
def test_evaluate_deliverable_expected_on_disk_uncaptured(tmp_path: Path) -> None:
    """AC1 (direct): evaluate_deliverable_verification mirrors is_file() backstop."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "libs" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("on disk\n", encoding="utf-8")
    spec = _ready_spec(files_expected=["libs/a.py"])
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:verify-me",
    )
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=closeout,
        sidecar_resolvable=True,
        tool_call_count=1,
        source_repo=repo,
    )
    assert entries[0].command == f"{GATE_D_PREFIX}expected_present_on_disk_uncaptured"
    assert entries[0].exit_code == 1


@pytest.mark.offline
def test_sidecar_unresolvable(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    baseline = capture_wt_baseline(repo)
    touched = repo / "a.py"
    touched.write_text("x", encoding="utf-8")
    change_set = changed_paths(repo, baseline)
    verification = verify_deliverables(
        spec=None,
        change_set=change_set,
        outcome=_outcome(),
        sidecar_path=tmp_path / "missing.md",
        files_expected=["a.py"],
        baseline=baseline,
    )
    assert any(
        v.command == f"{GATE_D_PREFIX}sidecar_unresolvable" for v in verification
    )


@pytest.mark.offline
def test_prepare_closeout_on_disk_uncaptured_surfaces_honest_gate_d(
    tmp_path: Path,
) -> None:
    """AC6: closeout delivery surfaces expected_present_on_disk_uncaptured, not silent pass."""
    repo = _init_git_repo(tmp_path)
    expected = repo / "services" / "only_on_disk.py"
    expected.parent.mkdir(parents=True)
    expected.write_text("# committed deliverable\n", encoding="utf-8")
    subprocess.run(["git", "add", "services/only_on_disk.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add expected"], cwd=repo, check=True)
    baseline = capture_wt_baseline(repo)
    packet = (
        "<scope>\n"
        "Files expected: - `services/only_on_disk.py`\n"
        "</scope>\n"
    )
    delivery = prepare_closeout_delivery(
        source_repo=repo,
        dispatch_id="d-uncaptured",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="t1",
        work_item_ref="todo:verify-me",
        baseline=baseline,
        packet_text=packet,
    )
    body = json.loads(delivery.body)
    assert body["status"] == "partial"
    assert any(
        v["command"] == f"{GATE_D_PREFIX}expected_present_on_disk_uncaptured"
        for v in body["verification"]
    )
    assert "services/only_on_disk.py" not in body["files_created"]
    assert "services/only_on_disk.py" not in body["files_modified"]


@pytest.mark.offline
def test_prepare_closeout_surfaces_verification(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    baseline = capture_wt_baseline(repo)
    touched = repo / "libs/implement_admission/drift_gates.py"
    touched.parent.mkdir(parents=True, exist_ok=True)
    touched.write_text("# gate d\n", encoding="utf-8")
    packet = (
        "<scope>\n"
        "Files expected: - `libs/implement_admission/drift_gates.py`\n"
        "</scope>\n"
    )
    delivery = prepare_closeout_delivery(
        source_repo=repo,
        dispatch_id="d1",
        outcome=_outcome(),
        degraded_reason=None,
        thread_id="2005",
        work_item_ref="todo:verify-me",
        baseline=baseline,
        packet_text=packet,
    )
    body = json.loads(delivery.body)
    assert body["files_created"] or body["files_modified"]
    assert body["verification"]
    assert body["verification"][0]["exit_code"] == 0


@pytest.mark.offline
def test_gate_d_before_adapters_blocks_done() -> None:
    calls: list[tuple[str, dict]] = []

    def fake_dispatch(tool: str, args: dict) -> dict:
        calls.append((tool, args))
        if tool == "todo_close_sidecar":
            return {"closure_summary_uri": "cortex://x.md"}
        if tool == "assert":
            return {"id": 1}
        if tool == "entity_update":
            return {"workflow_state": "done"}
        return {}

    set_runtime(CloseoutRuntime(dispatch=fake_dispatch))
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="done",
        source_ref="todo:verify-me",
        verification=[
            build_gate_d_verification(reason="no_expected_files_touched", passed=False)
        ],
        evidence_uris=EvidenceUris(cortex_assertions=["assertion:1"]),
    )
    source = Source(
        source_ref="todo:verify-me",
        canonical_ref="todo:verify-me",
        source_kind=SourceKind.TODO,
    )

    with patch(
        "implement_admission.drift_gates._resolve_materialized_spec",
        return_value=_ready_spec(files_expected=["a.py"]),
    ):
        gated = apply_closeout_gate_d(closeout, source=source)
        assert gated.status == CloseoutStatus.PARTIAL
        out = apply_closeout(gated)

    wf_calls = [c for c in calls if c[0] == "entity_update"]
    assert not wf_calls
    confirmed = [
        c for c in calls if c[0] == "assert" and c[1].get("confidence") == "confirmed"
    ]
    assert not confirmed
    needs_review = [
        c for c in calls if c[0] == "assert" and c[1].get("confidence") == "believed"
    ]
    assert needs_review
    assert out.status == CloseoutStatus.PARTIAL


@pytest.mark.offline
def test_successful_closeout_sets_done() -> None:
    calls: list[tuple[str, dict]] = []

    def fake_dispatch(tool: str, args: dict) -> dict:
        calls.append((tool, args))
        if tool == "todo_close_sidecar":
            return {"closure_summary_uri": "cortex://x.md"}
        if tool == "assert":
            return {"id": 1}
        if tool == "entity_update":
            return {"workflow_state": "done"}
        return {}

    set_runtime(CloseoutRuntime(dispatch=fake_dispatch))
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="done",
        source_ref="todo:verify-me",
        files_modified=["a.py"],
        verification=[build_gate_d_verification(reason="passed", passed=True)],
        evidence_uris=EvidenceUris(cortex_assertions=["assertion:1"]),
    )
    with patch(
        "implement_admission.drift_gates._resolve_materialized_spec",
        return_value=_ready_spec(files_expected=["a.py"]),
    ):
        out = apply_closeout(closeout)
    assert any(c[0] == "entity_update" for c in calls)
    assert out.status in {CloseoutStatus.COMPLETE, CloseoutStatus.PARTIAL}


@pytest.mark.offline
def test_apply_closeout_gate_d_ordering_prevents_adapter_done() -> None:
    adapter_ran = {"done": False}

    class TrackingAdapter:
        def apply(self, closeout, *, source):  # noqa: ANN001
            adapter_ran["done"] = True
            from implement_admission.closeout_models import AdapterResult

            return [AdapterResult(adapter="todo", status="complete")]

    from implement_admission.closeout import ADAPTERS as _ADAPTERS
    from implement_admission.closeout import _load_adapters

    _ADAPTERS.clear()
    _ADAPTERS["todo"] = TrackingAdapter()
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:verify-me",
        verification=[
            build_gate_d_verification(reason="no_expected_files_touched", passed=False)
        ],
    )
    source = Source(
        source_ref="todo:verify-me",
        canonical_ref="todo:verify-me",
        source_kind=SourceKind.TODO,
    )
    with patch(
        "implement_admission.drift_gates._resolve_materialized_spec",
        return_value=_ready_spec(files_expected=["a.py"]),
    ):
        gated = apply_closeout_gate_d(closeout, source=source)
        run_adapters(gated, source)
    assert gated.status == CloseoutStatus.PARTIAL
    _ADAPTERS.clear()
    _ADAPTERS.update(_load_adapters())


@pytest.mark.offline
def test_evaluate_deliverable_dirty_baseline_note() -> None:
    spec = _ready_spec(files_expected=["libs/a.py"])
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:verify-me",
        files_modified=["libs/a.py"],
    )
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=closeout,
        sidecar_resolvable=True,
        tool_call_count=1,
        baseline_dirty_in_expected=True,
    )
    assert entries[0].exit_code == 0
    assert "dirty_baseline_in_files_expected" in entries[0].command


@pytest.mark.offline
def test_ledger_wt_baseline_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    baseline = {"a.py": " M"}
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    req = CursorDispatchRequest(
        dispatch_id="d1",
        thread_id="t1",
        execution_id="e1",
        model="composer",
        packet_path="p.md",
    )
    admission = CursorDispatchResponse(
        admitted=True, dispatch_id="d1", thread_id="t1", model_id="composer"
    )
    ledger.admit(
        req=req,
        fingerprint="fp",
        execution_id="e1",
        caller_agent="dispatch",
        resolved_model="composer",
        admission=admission,
        wt_baseline=json.dumps(baseline),
        contract="implement",
        source_repo=str(tmp_path),
    )
    assert ledger.read_wt_baseline(dispatch_id="d1") == baseline


@pytest.mark.offline
def test_closeout_artifact_paths_sidecar_first() -> None:
    from services.git_integration_worker.cursor_sdk_closeout import (
        build_implement_closeout_body,
    )

    sidecar = "workspaces://universal-llm-gateway/tmp/reviews/closeouts/d1.md"
    body = json.loads(
        build_implement_closeout_body(
            dispatch_id="d1",
            outcome=_outcome(),
            degraded_reason=None,
            sidecar_ref=sidecar,
            result_bytes=100,
            thread_id="2656",
            work_item_ref="todo:test",
            cortex_artifact_paths=["cortex://notes/system/threads/foo.md"],
        )
    )
    paths = body["evidence_uris"]["artifact_paths"]
    assert paths[0].startswith("workspaces://")
    assert "cortex://notes/system/threads/foo.md" in paths


@pytest.mark.offline
@pytest.mark.asyncio
async def test_consult_closeout_pins_terminal_deliverable_to_cortex(
    tmp_path: Path,
) -> None:
    repo = _init_git_repo(tmp_path)
    pinned = "notes/system/threads/consult-findings.md"
    packet = (
        "<scope>\n"
        f"Deliverable: `cortex:{pinned}`\n"
        "</scope>\n"
    )

    async def fake_post(**kwargs: object) -> dict[str, str]:
        assert kwargs["write_if_absent"] is True
        assert kwargs["rel_path"] == pinned
        assert "findings body" in str(kwargs["content"])
        return {"uri": f"cortex://{pinned}"}

    from services.git_integration_worker import cursor_sdk_deliverables as deliverables
    from services.git_integration_worker.cursor_sdk_closeout import (
        prepare_closeout_delivery_async,
    )

    with patch.object(
        deliverables,
        "default_post_pinned_deliverable",
        fake_post,
    ):
        delivery = await prepare_closeout_delivery_async(
            source_repo=repo,
            dispatch_id="d-consult",
            outcome=_outcome(body="findings body"),
            degraded_reason=None,
            thread_id="2656",
            work_item_ref=None,
            baseline=None,
            packet_text=packet,
        )
    body = json.loads(delivery.body)
    paths = body["evidence_uris"]["artifact_paths"]
    assert paths[0].startswith("workspaces://")
    assert paths[1] == f"cortex://{pinned}"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_consult_closeout_degrades_when_pin_write_fails(tmp_path: Path) -> None:
    repo = _init_git_repo(tmp_path)
    pinned = "notes/system/threads/consult-findings.md"
    packet = f"<scope>\nDeliverable: `cortex:{pinned}`\n</scope>\n"

    async def failing_post(**kwargs: object) -> None:
        return None  # simulate Stargate ingress unreachable

    from services.git_integration_worker import cursor_sdk_deliverables as deliverables
    from services.git_integration_worker.cursor_sdk_closeout import (
        prepare_closeout_delivery_async,
    )

    with patch.object(deliverables, "default_post_pinned_deliverable", failing_post):
        delivery = await prepare_closeout_delivery_async(
            source_repo=repo,
            dispatch_id="d-fail",
            outcome=_outcome(body="findings body"),
            degraded_reason=None,
            thread_id="2657",
            work_item_ref=None,
            baseline=None,
            packet_text=packet,
        )
    body = json.loads(delivery.body)
    assert body["status"] == "partial"
    assert delivery.closeout_status.value == "partial"
    assert "pinned_deliverable_write_failed" in body["summary"]
    # artifact_paths still carries the sidecar; cortex uri absent because write failed
    assert body["evidence_uris"]["artifact_paths"][0].startswith("workspaces://")
    assert not any(
        p.startswith("cortex://") for p in body["evidence_uris"]["artifact_paths"]
    )


@pytest.mark.offline
def test_normalize_cortex_rel_parity() -> None:
    from libs.cortex_store.dispatch_ops._pinned_deliverable import (
        normalize_cortex_rel as cortex_norm,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        normalize_cortex_rel as worker_norm,
    )

    fixtures = [
        "cortex://notes/x.md",
        "cortex:notes/x.md",
        "CORTEX://notes/X.md",
        "/notes/x.md",
        "notes/../etc/passwd",
        "",
        "  notes/y.md  ",
        "notes/sub/deliverable.md",
    ]
    for raw in fixtures:
        assert worker_norm(raw) == cortex_norm(raw), f"divergence on {raw!r}"


@pytest.mark.offline
def test_pinned_deliverable_write_skips_when_present(tmp_path: Path) -> None:
    from libs.cortex_store.dispatch_ops._pinned_deliverable import (
        write_pinned_deliverable_impl,
    )
    from libs.cortex_store.dispatch_ops._shared import _FILES_ROOT

    rel = "notes/system/threads/existing.md"
    target = _FILES_ROOT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("already here", encoding="utf-8")
    result = write_pinned_deliverable_impl(
        rel,
        "new content",
        write_if_absent=True,
    )
    assert result.get("skipped") is True
    assert result["uri"] == "cortex://notes/system/threads/existing.md"
    assert target.read_text(encoding="utf-8") == "already here"
