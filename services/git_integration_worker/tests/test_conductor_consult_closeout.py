"""CONSULT_PENDING wrapper grades as consult, not gate_d / work."""

from __future__ import annotations

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_auto.closeout_status_polarity import (
    classify_status_incomplete_class,
)
from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    SdkRunOutcome,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
    CONDUCTOR_NEST_IN_FLIGHT,
    CONDUCTOR_ROW_PINNED,
)
from services.git_integration_worker.cursor_sdk_closeout.degraded_reasons import (
    CONDUCTOR_CONSULT_HANDOFF_MISSING,
    CONDUCTOR_CONSULT_PENDING,
    conductor_closeout_degraded_reason,
    conductor_consult_pending_degraded_reason,
)
from services.git_integration_worker.cursor_sdk_closeout.deliverable_probe import (
    verify_deliverables,
)

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

_BARE_CONSULT = """\
CONSULT_PENDING
"""

_NARRATIVE_RESUME_CONSULT = """\
resumed_at: CONSULT_PENDING
status: complete
"""


def test_narrative_resumed_at_not_conductor_consult_pending() -> None:
    reason = conductor_consult_pending_degraded_reason(
        body=_NARRATIVE_RESUME_CONSULT,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason is None


def test_consult_pending_with_next_admit_is_consult_reason() -> None:
    reason = conductor_consult_pending_degraded_reason(
        body=_WAIT_WITH_HANDOFF,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason == CONDUCTOR_CONSULT_PENDING


def test_bare_consult_pending_is_handoff_missing() -> None:
    reason = conductor_consult_pending_degraded_reason(
        body=_BARE_CONSULT,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason == CONDUCTOR_CONSULT_HANDOFF_MISSING


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


_ROW_PINNED = """\
ROW_PINNED
resume_at: G3
SCORE_RESURFACE posted on summoning_thread_id=9638
"""


def test_row_pinned_is_consult_reason() -> None:
    reason = conductor_closeout_degraded_reason(
        body=_ROW_PINNED,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason == CONDUCTOR_ROW_PINNED
    incomplete = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.CHECKS_FAILED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["gate_d:no_expected_files_touched"],
        degraded_reason=CONDUCTOR_ROW_PINNED,
    )
    assert incomplete == "consult"


def test_nested_live_outranks_empty_assistant() -> None:
    reason = conductor_closeout_degraded_reason(
        body="",
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
        nested_live=True,
    )
    assert reason == CONDUCTOR_NEST_IN_FLIGHT
    incomplete = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.CHECKS_FAILED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["gate_d:no_expected_files_touched"],
        degraded_reason=CONDUCTOR_NEST_IN_FLIGHT,
    )
    assert incomplete == "consult"


def test_verify_deliverables_suppresses_gate_d_on_row_pinned(tmp_path) -> None:
    outcome = SdkRunOutcome(
        body=_ROW_PINNED,
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
    assert any("exit_persist_stop" in row.command for row in rows)


def test_verify_deliverables_suppresses_gate_d_on_bare_consult(tmp_path) -> None:
    outcome = SdkRunOutcome(
        body=_BARE_CONSULT,
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


def test_should_page_row_pinned() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_closeout_pager import (
        should_page_conductor_silence,
    )

    assert should_page_conductor_silence(
        degraded_reason=CONDUCTOR_ROW_PINNED, nest_under=None
    )
    assert should_page_conductor_silence(
        degraded_reason=CONDUCTOR_NEST_IN_FLIGHT, nest_under=None
    )
    assert not should_page_conductor_silence(
        degraded_reason=None, nest_under=None
    )


def test_should_page_orphan_nest(monkeypatch) -> None:
    from services.git_integration_worker.cursor_sdk_closeout import (
        conductor_closeout_pager as pager_mod,
    )

    class _Led:
        def dispatch_status_by_id(self, *, dispatch_id: str):
            return {"dispatch_id": dispatch_id, "status": "completed"}

        @classmethod
        def instance(cls):
            return cls()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_dispatch_ledger.CursorDispatchLedger",
        _Led,
    )
    assert pager_mod.should_page_conductor_silence(
        degraded_reason=None, nest_under="dead-parent"
    )
