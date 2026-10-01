"""Width-seat rendering: ACTIVE is the shipped width seat; RESTORE is Fable."""

from __future__ import annotations

import pytest

from implement_admission.conductor_materialize import (
    attended_g3_g5_task_sentence,
    hop_invariant_g3_g5_fragment,
)
from implement_admission.conductor_width_seat import (
    ACTIVE,
    RESTORE,
    ConductorWidthSeat,
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


_OPUS_SUBSTITUTE = ConductorWidthSeat(
    model="cdp/opus-5",
    reasoning_effort="max",
    effort_when_bind_gates_wave="max",
)


@pytest.mark.offline
def test_render_restore_and_active_width_seat() -> None:
    """Production ACTIVE renders cdp/opus-5.5 at reasoning_effort=high."""
    assert ACTIVE is not RESTORE
    assert ACTIVE.model == "cdp/opus-5.5"
    assert ACTIVE.reasoning_effort == "high"
    assert ACTIVE.effort_when_bind_gates_wave == "max"

    active_clause = g3_g5_score_ratify_clause()
    active_hop, active_attended = _render_templates(active_clause)
    active_materialize = (
        hop_invariant_g3_g5_fragment(),
        attended_g3_g5_task_sentence(),
    )

    for rendered in (active_clause, active_hop, active_attended, *active_materialize):
        assert "cdp/opus-5.5" in rendered
        assert "reasoning_effort=high" in rendered
        assert "effort_when_bind_gates_wave=max" in rendered
        assert "cdp/fable-5.1" not in rendered

    restore_clause = g3_g5_score_ratify_clause(RESTORE)
    restore_hop, restore_attended = _render_templates(restore_clause)
    restore_materialize = (
        hop_invariant_g3_g5_fragment(RESTORE),
        attended_g3_g5_task_sentence(RESTORE),
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


@pytest.mark.offline
def test_explicit_opus_seat_renders_without_editing_active() -> None:
    """A passed-in cdp/opus-5 seat renders while ACTIVE stays cdp/opus-5.5."""
    clause = g3_g5_score_ratify_clause(_OPUS_SUBSTITUTE)
    hop, attended = _render_templates(clause)
    materialize = (
        hop_invariant_g3_g5_fragment(_OPUS_SUBSTITUTE),
        attended_g3_g5_task_sentence(_OPUS_SUBSTITUTE),
    )
    for rendered in (clause, hop, attended, *materialize):
        assert "cdp/opus-5" in rendered
        assert "cdp/opus-5.5" not in rendered
        assert "cdp/fable-5.1" not in rendered
        assert "reasoning_effort=max" in rendered
        assert "effort_when_bind_gates_wave=max" in rendered
    assert ACTIVE.model == "cdp/opus-5.5"


def test_default_render_reads_active_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production render with no seat argument reads the ACTIVE assignment."""
    import implement_admission.conductor_width_seat as seat_mod

    monkeypatch.setattr(seat_mod, "ACTIVE", RESTORE)
    rendered = hop_invariant_g3_g5_fragment()
    assert "cdp/fable-5.1" in rendered
    assert "reasoning_effort=high" in rendered
