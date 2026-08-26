"""GIW closeout grade: conductor G1-pin requires S4b rich-seed evidence."""

from __future__ import annotations

import json
from pathlib import Path

from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_closeout.degraded_reasons import (
    conductor_g1_pin_s4b_degraded_reason,
)

_CONDUCTOR_PACKET = """\
---
packet_kind: conductor
work_key: todo:fixture-slug
contract: light-bounded
lane: B
---
<scope>Conductor session.</scope>
stop_after pin: G1.
"""

_G1_PIN_S4B_OK = """\
| G1 | Architecture consult | DONE | ROW_PINNED |
Problem: Fixture problem statement
Scope: services/git_integration_worker only
Acceptance: targeted pytest green
density_triage: judgment_required
status: complete
"""

_G1_PIN_NO_S4B = """\
| G1 | Architecture consult | DONE | ROW_PINNED |
status: complete
ROW_PINNED
"""

_G3_PIN_NO_S4B = """\
| G3 | Densify | OPEN | ROW_PINNED |
resume_at: G3
status: complete
"""


def test_conductor_g1_pin_missing_s4b_degrades() -> None:
    reason = conductor_g1_pin_s4b_degraded_reason(
        body=_G1_PIN_NO_S4B,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason == "s4b_g1_pin_missing"


def test_conductor_g1_pin_with_s4b_not_degraded() -> None:
    reason = conductor_g1_pin_s4b_degraded_reason(
        body=_G1_PIN_S4B_OK,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason is None


def test_conductor_g3_pin_without_s4b_not_degraded() -> None:
    reason = conductor_g1_pin_s4b_degraded_reason(
        body=_G3_PIN_NO_S4B,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason is None


def test_prepare_closeout_delivery_conductor_g1_pin_missing_s4b(tmp_path: Path) -> None:
    outcome = SdkRunOutcome(
        body=_G1_PIN_NO_S4B,
        status="finished",
        duration_ms=100,
        tool_call_count=3,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-conductor-g1",
        outcome=outcome,
        degraded_reason=conductor_g1_pin_s4b_degraded_reason(
            body=_G1_PIN_NO_S4B,
            packet_text=_CONDUCTOR_PACKET,
            packet_kind="conductor",
        ),
        thread_id="t-conductor-g1",
        work_item_ref="todo:fixture-slug",
        packet_text=_CONDUCTOR_PACKET,
    )
    payload = json.loads(delivery.body)
    assert payload["status"] == "partial"
    assert payload["degraded_reason"] == "s4b_g1_pin_missing"
    assert "s4b_g1_pin_missing" in payload["summary"]
