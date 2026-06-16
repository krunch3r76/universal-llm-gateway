"""Gate D — deliverable verification (git-derived files_* + fail-soft done-transition).

Extracted from drift_gates for SLOC compliance. Imports drift_gates internals
(_resolve_materialized_spec, evaluate_drift_gate, gate_state, DriftGateResult,
DriftGateState) that are stable cross-module boundaries within implement_admission.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

from implement_admission.closeout_models import ImplementCloseout, Verification
from implement_admission.spec import ImplementSpec, Source, SourceKind

if TYPE_CHECKING:
    from implement_admission.drift_gates import DriftGateResult

logger = get_logger(__name__)

GATE_D_PREFIX = "gate_d:"


def _normalize_expected_path(raw: str) -> str:
    path = raw.strip()
    if " (" in path:
        path = path.split(" (", 1)[0].strip()
    return path.lstrip("/")


def _changed_path_set(closeout: ImplementCloseout) -> set[str]:
    paths: set[str] = set()
    for group in (
        closeout.files_created,
        closeout.files_modified,
        closeout.files_deleted,
    ):
        for path in group:
            paths.add(path.lstrip("/"))
    return paths


def _paths_intersect(expected: list[str], changed: set[str]) -> bool:
    if not expected:
        return True
    norm_expected = {_normalize_expected_path(p) for p in expected}
    for exp in norm_expected:
        for ch in changed:
            if ch == exp or ch.endswith(f"/{exp}") or exp.endswith(f"/{ch}"):
                return True
    return False


def gate_d_passed(closeout: ImplementCloseout) -> bool:
    """True when no Gate-D verification entry failed (missing entries => pass)."""
    entries = [v for v in closeout.verification if v.command.startswith(GATE_D_PREFIX)]
    if not entries:
        return True
    return all(v.exit_code == 0 for v in entries)


def build_gate_d_verification(
    *, reason: str, passed: bool, note: str | None = None
) -> Verification:
    cmd = f"{GATE_D_PREFIX}{reason}"
    if note:
        cmd = f"{cmd};note={note}"
    return Verification(command=cmd, exit_code=0 if passed else 1)


def evaluate_deliverable_verification(
    *,
    spec: ImplementSpec | None,
    closeout: ImplementCloseout,
    sidecar_resolvable: bool = True,
    run_finished: bool = True,
    tool_call_count: int = 0,
    baseline_dirty_in_expected: bool = False,
    files_expected: list[str] | None = None,
) -> list[Verification]:
    """Mechanical deliverable checks for Gate D (fail-soft; always records entries)."""
    entries: list[Verification] = []
    expected = files_expected
    if expected is None:
        expected = spec.scope.files_expected if spec and spec.scope else []

    if not run_finished:
        entries.append(
            build_gate_d_verification(reason="run_not_finished", passed=False)
        )
        return entries

    if tool_call_count <= 0:
        entries.append(
            build_gate_d_verification(reason="zero_tool_calls", passed=False)
        )
        return entries

    if not sidecar_resolvable:
        entries.append(
            build_gate_d_verification(reason="sidecar_unresolvable", passed=False)
        )
        return entries

    changed = _changed_path_set(closeout)
    if files_expected and not _paths_intersect(files_expected, changed):
        entries.append(
            build_gate_d_verification(reason="no_expected_files_touched", passed=False)
        )
        return entries

    note = (
        "dirty_baseline_in_files_expected"
        if baseline_dirty_in_expected and expected
        else None
    )
    entries.append(build_gate_d_verification(reason="passed", passed=True, note=note))
    return entries


def check_deliverable_verification(
    closeout: ImplementCloseout,
    *,
    source: Source,
    workspaces_root: Path | None = None,
) -> DriftGateResult:
    """Gate D — mechanical deliverable verification (fail-soft; always warn/trip)."""
    from implement_admission.closeout import flatten_evidence_uris
    from implement_admission.drift_gates import (
        DriftGateResult,  # noqa: F401
        _resolve_materialized_spec,
        evaluate_drift_gate,
        gate_state,
    )
    from implement_admission.source_ref import SourceRefError

    if source.source_kind != SourceKind.TODO:
        return DriftGateResult(
            gate_id="d",
            tripped=False,
            action="noop",
            detail="gate d skipped: non-todo lane",
        )
    try:
        spec = _resolve_materialized_spec(
            closeout.source_ref, workspaces_root=workspaces_root
        )
    except SourceRefError:
        return DriftGateResult(
            gate_id="d",
            tripped=False,
            action="noop",
            detail="gate d skipped: source_ref not re-resolvable",
        )

    if not spec.scope.files_expected:
        return DriftGateResult(
            gate_id="d",
            tripped=False,
            action="noop",
            detail="gate d skipped: no files_expected",
        )

    passed = gate_d_passed(closeout)
    if passed and closeout.verification:
        return DriftGateResult(gate_id="d", tripped=False, action="noop")

    sidecar_ok = bool(flatten_evidence_uris(closeout.evidence_uris))
    entries = evaluate_deliverable_verification(
        spec=spec,
        closeout=closeout,
        sidecar_resolvable=sidecar_ok,
    )
    passed = all(v.exit_code == 0 for v in entries)
    reason = next(
        (
            v.command.removeprefix(GATE_D_PREFIX).split(";", 1)[0]
            for v in entries
            if v.exit_code
        ),
        None,
    )
    tripped = not passed
    state = gate_state("d")
    return evaluate_drift_gate(
        "d",
        state,
        tripped=tripped,
        reason=reason,
        detail=f"gate d deliverable verification: {reason}" if reason else None,
    )


def apply_closeout_gate_d(
    closeout: ImplementCloseout,
    *,
    source: Source,
    workspaces_root: Path | None = None,
) -> ImplementCloseout:
    from implement_admission.closeout import flatten_evidence_uris
    from implement_admission.drift_gates import _resolve_materialized_spec
    from implement_admission.source_ref import SourceRefError
    from implement_admission.spec import CloseoutStatus

    result = check_deliverable_verification(
        closeout, source=source, workspaces_root=workspaces_root
    )
    if not result.tripped:
        return closeout

    try:
        spec = _resolve_materialized_spec(
            closeout.source_ref, workspaces_root=workspaces_root
        )
    except SourceRefError:
        return closeout

    sidecar_ok = bool(flatten_evidence_uris(closeout.evidence_uris))
    entries = closeout.verification or evaluate_deliverable_verification(
        spec=spec,
        closeout=closeout,
        sidecar_resolvable=sidecar_ok,
    )
    deviation = result.detail or result.reason or "drift_gate_d"
    status = closeout.status
    if status == CloseoutStatus.COMPLETE:
        status = CloseoutStatus.PARTIAL
    return closeout.model_copy(
        update={
            "status": status,
            "verification": entries,
            "deviations": [*closeout.deviations, deviation],
        }
    )
