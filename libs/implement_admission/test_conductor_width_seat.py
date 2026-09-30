"""Width-seat rendering: RESTORE stays Fable while ACTIVE ships Opus."""

from __future__ import annotations

import pytest

from implement_admission.conductor_materialize import (
    attended_g3_g5_task_sentence,
    hop_invariant_g3_g5_fragment,
)
from implement_admission.conductor_width_seat import (
    ACTIVE,
    RESTORE,
    g3_g5_score_ratify_clause,
)
from services.git_integration_worker.cursor_sdk_packet import (
    _CONDUCTOR_ATTENDED_RESURFACE_TEMPLATE,
    _CONDUCTOR_HOP_TEMPLATE,
)


def _render_templates(seat_clause: str) -> tuple[str, str]:
    hop = _CONDUCTOR_HOP_TEMPLATE.format(
        hop_seq=1,
        thread_id="13550",
        lineage="",
        width_clause=seat_clause,
    )
    attended = _CONDUCTOR_ATTENDED_RESURFACE_TEMPLATE.format(
        caller_agent="cursor",
        summoning_thread_id="9638",
        width_clause=seat_clause,
    )
    return hop, attended


@pytest.mark.offline
def test_render_restore_and_active_width_seat() -> None:
    """RESTORE renders Fable without editing ACTIVE, which stays Opus max."""
    assert ACTIVE.model == "cdp/opus-5"
    assert ACTIVE.reasoning_effort == "max"
    assert RESTORE.model == "cdp/fable-5.1"
    assert RESTORE.reasoning_effort == "high"
    assert RESTORE.effort_when_bind_gates_wave == "max"

    restore_clause = g3_g5_score_ratify_clause(RESTORE)
    active_clause = g3_g5_score_ratify_clause(ACTIVE)
    restore_hop, restore_attended = _render_templates(restore_clause)
    active_hop, active_attended = _render_templates(active_clause)
    restore_materialize = (
        hop_invariant_g3_g5_fragment(RESTORE),
        attended_g3_g5_task_sentence(RESTORE),
    )
    active_materialize = (
        hop_invariant_g3_g5_fragment(ACTIVE),
        attended_g3_g5_task_sentence(ACTIVE),
    )

    for rendered in (
        restore_clause,
        restore_hop,
        restore_attended,
        *restore_materialize,
    ):
        assert "cdp/fable-5.1" in rendered
        assert "reasoning_effort=high" in rendered
        assert "effort_when_bind_gates_wave=max" in rendered
        assert "cdp/opus-5" not in rendered

    for rendered in (active_clause, active_hop, active_attended, *active_materialize):
        assert "cdp/opus-5" in rendered
        assert "cdp/opus-5.5" not in rendered
        assert "cdp/fable-5.1" not in rendered
        assert "reasoning_effort=max" in rendered
        assert "effort_when_bind_gates_wave=max" in rendered
