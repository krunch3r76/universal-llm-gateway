"""Packet ``files_expected`` extraction and the Gate-D deliverable probe adapter.

Turns packet/light-bounded expected paths into the list Gate-D consumes, then
builds a probe ``ImplementCloseout`` and calls
``evaluate_deliverable_verification`` (logic stays in implement_admission).
``_baseline_dirty_in_expected`` lives in ``worktree_baseline``; this module
imports that sibling by defining module, not through ``__init__``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.closeout_models import ImplementCloseout, Verification
from implement_admission.deliverable_verification import (
    build_gate_d_verification,
    evaluate_deliverable_verification,
)
from implement_admission.normalize import _files_from_packet
from implement_admission.spec import CloseoutStatus, ImplementSpec

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet

from .closeout_records import SdkRunOutcome
from .worktree_baseline import _baseline_dirty_in_expected


def _files_expected_from_packet(packet_text: str | None) -> list[str]:
    if not packet_text:
        return []
    return _files_from_packet(packet_text)


def _files_expected_for_pinning(
    packet_text: str | None,
    deliverables_expected: bool,
    light_bounded_expected_paths: tuple[str, ...],
) -> list[str]:
    if light_bounded_expected_paths:
        return list(light_bounded_expected_paths)
    if deliverables_expected:
        return _files_expected_from_packet(packet_text)
    return []


def verify_deliverables(
    *,
    spec: ImplementSpec | None,
    change_set: ChangeSet,
    outcome: SdkRunOutcome,
    sidecar_path: Path | None,
    files_expected: list[str] | None = None,
    baseline: dict[str, Any] | None = None,
    source_repo: Path | None = None,
) -> list[Verification]:
    """Gate-D probe; ``source_repo`` enables on-disk backstop for uncaptured repo paths.

    See ``classify_capture_status`` for the repo capture trust boundary (porcelain +
    manifest fold vs shell side effects).
    """
    from claude_bundles.conductor_stop import (
        is_exit_persist_stop,
        parse_stop_tokens,
    )

    tokens = parse_stop_tokens(outcome.body).tokens
    if "CONSULT_PENDING" in tokens:
        return [
            build_gate_d_verification(
                reason="passed", passed=True, note="consult_pending_wait"
            )
        ]
    if is_exit_persist_stop(outcome.body):
        return [
            build_gate_d_verification(
                reason="passed", passed=True, note="exit_persist_stop"
            )
        ]
    expected = files_expected or (spec.scope.files_expected if spec else [])
    closeout_probe = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="probe",
        source_ref="todo:probe",
        files_created=list(change_set.created),
        files_modified=list(change_set.modified),
        files_deleted=list(change_set.deleted),
    )
    sidecar_ok = sidecar_path is not None and sidecar_path.is_file()
    return evaluate_deliverable_verification(
        spec=spec,
        closeout=closeout_probe,
        sidecar_resolvable=sidecar_ok,
        run_finished=outcome.status == "finished",
        tool_call_count=outcome.tool_call_count,
        baseline_dirty_in_expected=_baseline_dirty_in_expected(baseline, expected),
        files_expected=expected,
        source_repo=source_repo,
    )
