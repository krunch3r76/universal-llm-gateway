"""GIW closeout grade: away conductor G3→G5 requires score-ratify posture."""

from __future__ import annotations

import json
from pathlib import Path

from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    prepare_closeout_delivery,
)
from services.git_integration_worker.cursor_sdk_closeout.degraded_reasons import (
    conductor_q2_score_ratify_degraded_reason,
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

_G3_DONE_NO_MARKERS = """\
| G3 | Densify | DONE |
status: complete
land_disposition: landed
"""

_G3_DONE_WITH_MARKERS = """\
| G3 | Densify | DONE |
Posture: do-not-fight; likely-optimal completion.
status: complete
land_disposition: landed
"""

_G3_ROW_PINNED = """\
| G3 | Densify | OPEN | ROW_PINNED |
resume_at: G3
status: complete
"""


def test_conductor_q2_missing_markers_degrades() -> None:
    reason = conductor_q2_score_ratify_degraded_reason(
        body=_G3_DONE_NO_MARKERS,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason == "q2_score_ratify_missing"


def test_conductor_q2_with_markers_not_degraded() -> None:
    reason = conductor_q2_score_ratify_degraded_reason(
        body=_G3_DONE_WITH_MARKERS,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason is None


def test_conductor_g3_row_pinned_not_degraded() -> None:
    reason = conductor_q2_score_ratify_degraded_reason(
        body=_G3_ROW_PINNED,
        packet_text=_CONDUCTOR_PACKET,
        packet_kind="conductor",
    )
    assert reason is None


def test_prepare_closeout_delivery_q2_score_ratify_missing(tmp_path: Path) -> None:
    outcome = SdkRunOutcome(
        body=_G3_DONE_NO_MARKERS,
        status="finished",
        duration_ms=100,
        tool_call_count=3,
    )
    delivery = prepare_closeout_delivery(
        source_repo=tmp_path,
        dispatch_id="disp-conductor-q2",
        outcome=outcome,
        degraded_reason=conductor_q2_score_ratify_degraded_reason(
            body=_G3_DONE_NO_MARKERS,
            packet_text=_CONDUCTOR_PACKET,
            packet_kind="conductor",
        ),
        thread_id="t-conductor-q2",
        work_item_ref="todo:fixture-slug",
        packet_text=_CONDUCTOR_PACKET,
    )
    payload = json.loads(delivery.body)
    assert payload["status"] == "partial"
    assert payload["degraded_reason"] == "q2_score_ratify_missing"
    assert "q2_score_ratify_missing" in payload["summary"]
