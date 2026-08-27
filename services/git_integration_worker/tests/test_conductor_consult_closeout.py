"""CONSULT_PENDING wrapper grades as consult, not gate_d / work."""

from __future__ import annotations

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_auto.closeout_status_polarity import (
    classify_status_incomplete_class,
)
from services.git_integration_worker.cursor_sdk_closeout.degraded_reasons import (
    CONDUCTOR_CONSULT_HANDOFF_MISSING,
    CONDUCTOR_CONSULT_PENDING,
    conductor_consult_pending_degraded_reason,
)
from services.git_integration_worker.cursor_sdk_closeout.deliverable_probe import (
    verify_deliverables,
)
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    SdkRunOutcome,
)
from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet

_CONDUCTOR_PACKET = """\
---
packet_kind: conductor
work_key: todo:fixture-slug
contract: light-bounded
lane: B
---
<scope>Conductor session.</scope>
"""

_WAIT_WITH_HANDOFF = """\
CONSULT_PENDING
execution_id: fabcc1e6-xxxx
poll_hint: agent_bus.wait thread=9677
NEXT_ADMIT: harvest G1
cse: cse_01Wi3x5yzzRtfRYUXJKgii99
"""

_WAIT_NO_HANDOFF = """\
CONSULT_PENDING
execution_id: fabcc1e6-xxxx
poll_hint: agent_bus.wait thread=9677
cse: cse_01Wi3x5yzzRtfRYUXJKgii99
"""


def test_consult_pending_with_next_admit_is_consult_reason() -> None:
    reason = conductor_consult_pending_degraded_reason(
        body=_WAIT_WITH_HANDOFF,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason == CONDUCTOR_CONSULT_PENDING


def test_consult_pending_without_handoff_is_handoff_missing() -> None:
    reason = conductor_consult_pending_degraded_reason(
        body=_WAIT_NO_HANDOFF,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason == CONDUCTOR_CONSULT_HANDOFF_MISSING


def test_classify_consult_before_checks_failed() -> None:
    incomplete = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.CHECKS_FAILED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["gate_d:no_expected_files_touched"],
        degraded_reason=CONDUCTOR_CONSULT_PENDING,
    )
    assert incomplete == "consult"


def test_existing_partial_work_unchanged() -> None:
    incomplete = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.CHECKS_FAILED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["land:lane_b_unlanded"],
    )
    assert incomplete == "work"


def test_verify_deliverables_suppresses_gate_d_on_consult_wait(tmp_path) -> None:
    outcome = SdkRunOutcome(
        body=_WAIT_WITH_HANDOFF,
        status="finished",
        duration_ms=100,
        tool_call_count=3,
    )
    rows = verify_deliverables(
        spec=None,
        change_set=ChangeSet(created=(), modified=(), deleted=()),
        outcome=outcome,
        sidecar_path=tmp_path / "missing.md",
        files_expected=["cortex://notes/system/scoreboards/x.md"],
        source_repo=tmp_path,
    )
    assert rows
    assert all(row.exit_code == 0 for row in rows)
    assert any("consult_pending_wait" in row.command for row in rows)
