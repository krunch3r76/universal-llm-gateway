"""Unit tests for Gate D deliverable verification (cortex sandbox backstop)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.closeout_models import ImplementCloseout
from implement_admission.deliverable_verification import (
    GATE_D_PREFIX,
    evaluate_deliverable_verification,
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


def _closeout() -> ImplementCloseout:
    return ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:verify-me",
    )


def _gate_d_reason(entries: list) -> str:
    return entries[0].command.removeprefix(GATE_D_PREFIX).split(";", 1)[0]


@pytest.mark.offline
def test_cortex_expected_present_on_disk_uncaptured(tmp_path: Path) -> None:
    cortex_root = tmp_path / "cortex"
    target = cortex_root / "configs" / "foo.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}\n", encoding="utf-8")
    spec = _ready_spec(files_expected=["cortex://configs/foo.json"])
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=_closeout(),
        sidecar_resolvable=True,
        tool_call_count=1,
        cortex_root=cortex_root,
    )
    assert _gate_d_reason(entries) == "expected_present_on_disk_uncaptured"
    assert entries[0].exit_code == 1
    # Member 5: Gate-D boolean-as-exit is derived, not a process observation.
    assert entries[0].exit_code_register == "derived"
    assert entries[0].basis == "gate_d_boolean_pass"
    # Member-8 rider: invocation_id is a unique handle, not a reason echo.
    assert entries[0].invocation_id is not None
    assert entries[0].invocation_id.startswith("gate_d:")
    assert entries[0].invocation_id != entries[0].command


@pytest.mark.offline
def test_cortex_expected_absent_reports_no_expected_files_touched(
    tmp_path: Path,
) -> None:
    cortex_root = tmp_path / "cortex"
    cortex_root.mkdir()
    spec = _ready_spec(files_expected=["cortex://configs/missing.json"])
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=_closeout(),
        sidecar_resolvable=True,
        tool_call_count=1,
        cortex_root=cortex_root,
    )
    assert _gate_d_reason(entries) == "no_expected_files_touched"
    assert entries[0].exit_code == 1


@pytest.mark.offline
def test_mixed_repo_and_cortex_uncaptured_via_cortex(tmp_path: Path) -> None:
    cortex_root = tmp_path / "cortex"
    cortex_target = cortex_root / "notes" / "spec.md"
    cortex_target.parent.mkdir(parents=True)
    cortex_target.write_text("# spec\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _ready_spec(
        files_expected=[
            "libs/missing.py",
            "cortex://notes/spec.md",
        ]
    )
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=_closeout(),
        sidecar_resolvable=True,
        tool_call_count=1,
        source_repo=repo,
        cortex_root=cortex_root,
    )
    assert _gate_d_reason(entries) == "expected_present_on_disk_uncaptured"


@pytest.mark.offline
def test_mixed_repo_and_cortex_uncaptured_via_repo(tmp_path: Path) -> None:
    cortex_root = tmp_path / "cortex"
    cortex_root.mkdir()
    repo = tmp_path / "repo"
    repo_target = repo / "libs" / "present.py"
    repo_target.parent.mkdir(parents=True)
    repo_target.write_text("# present\n", encoding="utf-8")
    spec = _ready_spec(
        files_expected=[
            "libs/present.py",
            "cortex://notes/missing.md",
        ]
    )
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=_closeout(),
        sidecar_resolvable=True,
        tool_call_count=1,
        source_repo=repo,
        cortex_root=cortex_root,
    )
    assert _gate_d_reason(entries) == "expected_present_on_disk_uncaptured"


@pytest.mark.offline
def test_mixed_repo_and_cortex_both_absent(tmp_path: Path) -> None:
    cortex_root = tmp_path / "cortex"
    cortex_root.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = _ready_spec(
        files_expected=["libs/missing.py", "cortex://notes/missing.md"]
    )
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=_closeout(),
        sidecar_resolvable=True,
        tool_call_count=1,
        source_repo=repo,
        cortex_root=cortex_root,
    )
    assert _gate_d_reason(entries) == "no_expected_files_touched"


@pytest.mark.offline
def test_repo_path_behavior_unchanged_without_cortex(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "services" / "uncaptured.py"
    target.parent.mkdir(parents=True)
    target.write_text("# present\n", encoding="utf-8")
    spec = _ready_spec(files_expected=["services/uncaptured.py"])
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=_closeout(),
        sidecar_resolvable=True,
        tool_call_count=1,
        source_repo=repo,
    )
    assert _gate_d_reason(entries) == "expected_present_on_disk_uncaptured"


@pytest.mark.offline
def test_member5_specimen_auto_00a23d2a4f45_class_distinguishable() -> None:
    """Specimen class: prose 'All checks passed!' vs structured exit_code:1.

    After Packet C, a reader must tell Gate-D (derived boolean) from a
    process-observed ruff exit, and tell two ruff invocations apart by
    invocation_id — a bare register without identity is not enough.
    """
    from implement_admission.closeout_models import (
        Verification,
        observed_process_verification,
    )
    from implement_admission.deliverable_verification import build_gate_d_verification

    # Verbatim specimen shapes (auto-00a23d2a4f45 verification array):
    #   gate_d:passed / 0   +   ruff check 8 touched files / 1
    gate = build_gate_d_verification(reason="passed", passed=True)
    gate_again = build_gate_d_verification(reason="passed", passed=True)
    mid_run = observed_process_verification(
        command="ruff check 8 touched files",
        exit_code=1,
        invocation_id="lint:mid-run-shell-a1",
        basis="subprocess.run.returncode",
    )
    later_pass = observed_process_verification(
        command="ruff check 8 touched files",
        exit_code=0,
        invocation_id="lint:closeout-pack-b2",
        basis="subprocess.run.returncode",
    )

    assert gate.exit_code_register == "derived"
    assert gate.basis == "gate_d_boolean_pass"
    assert gate.command == "gate_d:passed"
    assert gate.invocation_id != gate.command
    assert gate.invocation_id != gate_again.invocation_id
    assert mid_run.exit_code_register == "observed"
    assert later_pass.exit_code_register == "observed"
    assert mid_run.invocation_id != later_pass.invocation_id
    assert mid_run.command == later_pass.command
    assert mid_run.exit_code != later_pass.exit_code

    # Legacy two-field wire still loads (phased default) but announces unknown.
    legacy = Verification.model_validate(
        {"command": "ruff check 8 touched files", "exit_code": 1}
    )
    assert legacy.exit_code_register == "unknown"
    assert legacy.invocation_id is None


@pytest.mark.offline
def test_unattributed_register_packs_without_observed_semantics() -> None:
    """Member 5 — unattributed rows are distinct from observed packers."""
    from implement_admission.closeout_models import unattributed_process_verification

    row = unattributed_process_verification(
        command="pytest -q | tee /tmp/out; echo SUITE_EXIT",
        exit_code=0,
        invocation_id="test:unattributed-pack",
        basis="shell_tool_result.exitCode",
    )
    assert row.exit_code_register == "unattributed"
    assert row.exit_code == 0
    assert row.basis == "shell_tool_result.exitCode"
