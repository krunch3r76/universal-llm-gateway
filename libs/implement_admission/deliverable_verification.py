"""Gate D — deliverable verification (git-derived files_* + fail-soft done-transition).

Extracted from drift_gates for SLOC compliance. Imports drift_gates internals
(_resolve_materialized_spec, evaluate_drift_gate, gate_state, DriftGateResult,
DriftGateState) that are stable cross-module boundaries within implement_admission.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from universal_logging import get_logger

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.closeout_models import (
    ImplementCloseout,
    Verification,
    derived_gate_verification,
)
from implement_admission.scheme_resolve import parse_schemed_path
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


def _is_cortex_expected_path(raw: str) -> bool:
    path = raw.strip().lower()
    return path.startswith("cortex://") or path.startswith("cortex:")


def _repo_expected_paths(files_expected: list[str]) -> list[str]:
    return [p for p in files_expected if not _is_cortex_expected_path(p)]


def _cortex_expected_paths(files_expected: list[str]) -> list[str]:
    return [p for p in files_expected if _is_cortex_expected_path(p)]


def _expected_repo_path_on_disk(source_repo: Path, raw: str) -> bool:
    """Mirror cortex pinned-deliverable resolver: ``Path.is_file()`` only."""
    rel = _normalize_expected_path(raw)
    try:
        return (source_repo / rel).is_file()
    except OSError:
        return False


def _expected_cortex_path_on_disk(raw: str, cortex_root: Path | None = None) -> bool:
    """Mirror repo resolver: ``Path.is_file()`` only against cortex sandbox root."""
    parsed = parse_schemed_path(_normalize_expected_path(raw))
    if parsed.scheme != "cortex":
        return False
    root = (cortex_root or cortex_files_root()).resolve()
    try:
        return (root / parsed.rel_path).is_file()
    except OSError:
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
    """Pack Gate-D as a *derived* boolean wearing a process-exit shape.

    ``exit_code`` here is not a subprocess returncode — it is ``0 if passed
    else 1``. Marked ``derived`` so readers cannot treat it as an observed
    process exit (row 29 member 5 / specimen auto-00a23d2a4f45 class).
    """
    cmd = f"{GATE_D_PREFIX}{reason}"
    if note:
        cmd = f"{cmd};note={note}"
    # invocation_id must be unique per capture — reason echo collides when two
    # Gate-D rows share a reason (row 29 member-8 rider / auto-a9e3035e56b7).
    return derived_gate_verification(
        command=cmd,
        exit_code=0 if passed else 1,
        basis="gate_d_boolean_pass",
        invocation_id=f"gate_d:{uuid4().hex}",
    )


def evaluate_deliverable_verification(
    *,
    spec: ImplementSpec | None,
    closeout: ImplementCloseout,
    sidecar_resolvable: bool = True,
    run_finished: bool = True,
    tool_call_count: int = 0,
    baseline_dirty_in_expected: bool = False,
    files_expected: list[str] | None = None,
    source_repo: Path | None = None,
    cortex_root: Path | None = None,
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
    if expected and not _paths_intersect(expected, changed):
        repo_expected = _repo_expected_paths(expected)
        cortex_expected = _cortex_expected_paths(expected)
        uncaptured_on_disk = False
        if source_repo is not None and repo_expected:
            uncaptured_on_disk = any(
                _expected_repo_path_on_disk(source_repo, path) for path in repo_expected
            )
        if not uncaptured_on_disk and cortex_expected:
            resolved_cortex_root = cortex_root or cortex_files_root()
            uncaptured_on_disk = any(
                _expected_cortex_path_on_disk(path, resolved_cortex_root)
                for path in cortex_expected
            )
        if uncaptured_on_disk:
            entries.append(
                build_gate_d_verification(
                    reason="expected_present_on_disk_uncaptured", passed=False
                )
            )
        else:
            entries.append(
                build_gate_d_verification(
                    reason="no_expected_files_touched", passed=False
                )
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
        source_repo=workspaces_root,
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
        source_repo=workspaces_root,
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
