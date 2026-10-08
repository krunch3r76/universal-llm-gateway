"""CONSULT_PENDING wrapper grades as consult, not gate_d / work."""

from __future__ import annotations

import json

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    resolve_work_outcome,
)
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    SdkRunOutcome,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_exit_reasons import (
    CONDUCTOR_EXIT_PERSIST,
    CONDUCTOR_NEST_IN_FLIGHT,
    CONDUCTOR_ROW_HOP,
    CONDUCTOR_ROW_PINNED,
)
from services.git_integration_worker.cursor_sdk_closeout.degraded_reasons import (
    CONDUCTOR_CONSULT_HANDOFF_MISSING,
    CONDUCTOR_CONSULT_PENDING,
    CONDUCTOR_PARK_HARVEST_OWED,
    _map_closeout_status,
    conductor_closeout_degraded_reason,
    conductor_consult_pending_degraded_reason,
)
from services.git_integration_worker.cursor_sdk_closeout.deliverable_probe import (
    verify_deliverables,
)
from services.git_integration_worker.relay.closeout_status_polarity import (
    classify_status_incomplete_class,
    incomplete_class_from_wrapper,
    qualify_partial_measurement,
)

_CONDUCTOR_PACKET = """\
---

work_key: todo:fixture-slug
packet_kind: conductor
contract: conductor
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
    )
    assert reason is None


def test_consult_pending_with_next_admit_is_consult_reason() -> None:
    reason = conductor_consult_pending_degraded_reason(
        body=_WAIT_WITH_HANDOFF,
        packet_text=_CONDUCTOR_PACKET,
    )
    assert reason == CONDUCTOR_CONSULT_PENDING


def test_bare_consult_pending_is_handoff_missing() -> None:
    reason = conductor_consult_pending_degraded_reason(
        body=_BARE_CONSULT,
        packet_text=_CONDUCTOR_PACKET,
    )
    assert reason == CONDUCTOR_CONSULT_HANDOFF_MISSING


def test_consult_pending_without_handoff_is_handoff_missing() -> None:
    reason = conductor_consult_pending_degraded_reason(
        body=_WAIT_NO_HANDOFF,
        packet_text=_CONDUCTOR_PACKET,
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


_ROW_HOP = """\
stop: ROW_HOP
hop_seq: 2
row_closed: G3
status: complete
"""


def test_row_hop_is_consult_reason() -> None:
    reason = conductor_closeout_degraded_reason(
        body=_ROW_HOP,
        packet_text=_CONDUCTOR_PACKET,
    )
    assert reason == CONDUCTOR_ROW_HOP
    incomplete = classify_status_incomplete_class(
        status=CloseoutStatus.PARTIAL,
        work_outcome=WorkOutcome.CHECKS_FAILED,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["gate_d:no_expected_files_touched"],
        degraded_reason=CONDUCTOR_ROW_HOP,
    )
    assert incomplete == "consult"


def test_row_pinned_is_consult_reason() -> None:
    reason = conductor_closeout_degraded_reason(
        body=_ROW_PINNED,
        packet_text=_CONDUCTOR_PACKET,
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


def test_should_page_only_consult_handoff_missing() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_closeout_pager import (
        should_page_conductor_silence,
    )

    assert should_page_conductor_silence(
        degraded_reason=CONDUCTOR_CONSULT_HANDOFF_MISSING, nest_under=None
    )
    assert not should_page_conductor_silence(
        degraded_reason=CONDUCTOR_ROW_PINNED, nest_under=None
    )
    assert not should_page_conductor_silence(
        degraded_reason=CONDUCTOR_ROW_HOP, nest_under=None
    )
    assert not should_page_conductor_silence(
        degraded_reason=CONDUCTOR_NEST_IN_FLIGHT, nest_under=None
    )
    assert not should_page_conductor_silence(
        degraded_reason=CONDUCTOR_CONSULT_PENDING, nest_under=None
    )
    assert not should_page_conductor_silence(
        degraded_reason=CONDUCTOR_EXIT_PERSIST, nest_under=None
    )
    assert not should_page_conductor_silence(
        degraded_reason=None, nest_under=None, is_conductor=True
    )
    assert not should_page_conductor_silence(
        degraded_reason=None, nest_under=None, is_conductor=False
    )
    assert not should_page_conductor_silence(
        degraded_reason=None, nest_under="any-parent", is_conductor=False
    )
    assert not should_page_conductor_silence(
        degraded_reason=CONDUCTOR_PARK_HARVEST_OWED, nest_under=None
    )


_LIVE_HARVEST_UUID = "642fe99c-45e4-487d-9abb-132c1023a679"
_LIVE_HARVEST_HEX = "eb1edc38505e-684913a2"


def _park_body(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def _grade(body: str, tmp_path):
    reason = conductor_closeout_degraded_reason(
        body=body,
        packet_text=_CONDUCTOR_PACKET,
    )
    status = _map_closeout_status(reason)
    outcome = resolve_work_outcome(
        degraded_reason=reason,
        verification=[],
        manifest=None,
        source_repo=tmp_path,
        cortex_root=tmp_path,
    )
    incomplete = classify_status_incomplete_class(
        status=status,
        work_outcome=outcome,
        capture_status="partial",
        escalation_harvest="none",
        deviations=["capture:noise"],
        degraded_reason=reason,
    )
    return reason, status, outcome, incomplete


def test_live_park_harvest_grades_park_not_consult(tmp_path) -> None:
    """AC1: PARKED_TRANSPORT + NEXT_ADMIT harvest <uuid> is a designed park."""
    body = _park_body(
        "stop: PARKED_TRANSPORT",
        f"NEXT_ADMIT: harvest {_LIVE_HARVEST_UUID}",
    )
    reason, status, outcome, incomplete = _grade(body, tmp_path)
    assert reason == CONDUCTOR_PARK_HARVEST_OWED
    assert reason != CONDUCTOR_EXIT_PERSIST
    assert status == CloseoutStatus.PARTIAL
    assert outcome is None
    assert incomplete == "park"
    assert qualify_partial_measurement("partial", incomplete) == "partial:park"
    assert incomplete_class_from_wrapper({"status_incomplete_class": "park"}) == "park"


def test_live_park_harvest_accepts_hex_dash_dispatch_id(tmp_path) -> None:
    body = _park_body(
        "stop: PARKED_TRANSPORT",
        f"NEXT_ADMIT: harvest {_LIVE_HARVEST_HEX}",
    )
    reason, status, outcome, incomplete = _grade(body, tmp_path)
    assert reason == CONDUCTOR_PARK_HARVEST_OWED
    assert status == CloseoutStatus.PARTIAL
    assert outcome is None
    assert incomplete == "park"


def test_park_without_live_harvest_stays_exit_persist(tmp_path) -> None:
    """AC2: missing, none, non-id, DONE, archive, or web-anthropic stay consult."""
    cases = {
        "no_next_admit": _park_body("stop: PARKED_TRANSPORT"),
        "next_admit_none": _park_body(
            "stop: PARKED_TRANSPORT",
            "NEXT_ADMIT: none",
        ),
        "harvest_without_id": _park_body(
            "stop: PARKED_TRANSPORT",
            "NEXT_ADMIT: harvest G1",
        ),
        "done": _park_body(
            "stop: PARKED_TRANSPORT",
            "stop: DONE",
            f"NEXT_ADMIT: harvest {_LIVE_HARVEST_UUID}",
        ),
        "archive_uri": _park_body(
            "stop: PARKED_TRANSPORT",
            f"NEXT_ADMIT: harvest {_LIVE_HARVEST_UUID}",
            "archive_uri: cortex://notes/system/x",
        ),
        "web_anthropic": _park_body(
            "stop: PARKED_TRANSPORT",
            f"NEXT_ADMIT: harvest {_LIVE_HARVEST_UUID}",
            "from: web-anthropic",
        ),
    }
    for name, body in cases.items():
        reason, status, outcome, incomplete = _grade(body, tmp_path)
        assert reason == CONDUCTOR_EXIT_PERSIST, name
        assert status == CloseoutStatus.PARTIAL, name
        assert outcome == WorkOutcome.UNVERIFIED, name
        assert incomplete == "consult", name


def test_other_designed_stops_unchanged_beside_live_park(tmp_path) -> None:
    """ROW_PINNED, ROW_HOP, and CONSULT_PENDING still win; HOLD/OPERATOR stay persist."""
    row_pinned = _park_body(
        "stop: ROW_PINNED",
        "stop: PARKED_TRANSPORT",
        f"NEXT_ADMIT: harvest {_LIVE_HARVEST_UUID}",
    )
    assert _grade(row_pinned, tmp_path)[0] == CONDUCTOR_ROW_PINNED
    row_hop = _park_body(
        "stop: ROW_HOP",
        "stop: PARKED_TRANSPORT",
        f"NEXT_ADMIT: harvest {_LIVE_HARVEST_UUID}",
    )
    assert _grade(row_hop, tmp_path)[0] == CONDUCTOR_ROW_HOP
    consult = _park_body(
        "CONSULT_PENDING",
        "execution_id: fabcc1e6-xxxx",
        "poll_hint: agent_bus.wait thread=9677",
        "stop: PARKED_TRANSPORT",
        f"NEXT_ADMIT: harvest {_LIVE_HARVEST_UUID}",
    )
    assert _grade(consult, tmp_path)[0] == CONDUCTOR_CONSULT_PENDING
    for token in ("HOLD_MERGE", "OPERATOR_GATE"):
        reason, _status, outcome, incomplete = _grade(
            _park_body(f"stop: {token}"),
            tmp_path,
        )
        assert reason == CONDUCTOR_EXIT_PERSIST, token
        assert outcome == WorkOutcome.UNVERIFIED, token
        assert incomplete == "consult", token


def test_lane_closeout_settles_partial_when_park_work_outcome_is_null() -> None:
    """work_outcome null falls through to status; settled stays partial."""
    from bus_watch.lane_closeout import _parse_json_closeout_envelope

    body = json.dumps(
        {
            "status": "partial",
            "work_outcome": None,
            "degraded_reason": CONDUCTOR_PARK_HARVEST_OWED,
            "status_incomplete_class": "park",
        }
    )
    parsed = _parse_json_closeout_envelope(body)
    assert parsed is not None
    assert parsed["settled"] == "partial"
