"""AC3 — delivered followup body is the skill-reload template, then pointers."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.operator_wake_body import (
    CONTEXT_PRESSURE_LINE,
    SKILL_RELOAD_LINE,
    render_operator_wake_body,
)

_RELOAD = (
    "WAKE. First: reload skills per the opening prompt — "
    "Use the cdp-operator-proxy skill; "
    "Use the reasoning-posture skill; "
    "Use the hypothesize-simulate skill; "
    "Use the completion-provenance-discipline skill; "
    "Use the agent-bus-discipline skill; "
    "Use the retrieval-before-authoring skill. "
    "Then read cortex://notes/system/threads/12286-standing-handoff.md "
    "(first section) and cortex://notes/system/maestro/journal.md "
    "(loop + latest entry). Do not trust rank stated here."
)
_PRESSURE = (
    "Context-pressure check (journal step 9): if ≥2 of "
    "{≥6 closeouts harvested, skills reloaded more than once, "
    "a tool result spilled to file, replies summarizing not quoting} hold, "
    "bump the standing handoff and hop before harvesting."
)


def test_template_constants_match_sidecar_verbatim() -> None:
    assert SKILL_RELOAD_LINE.format(lane="12286") == _RELOAD
    assert CONTEXT_PRESSURE_LINE == _PRESSURE


def test_render_is_reload_pressure_then_pointers_only() -> None:
    body = render_operator_wake_body(
        owner_lane="12286",
        child_lane="13001",
        dispatch_id="auto-abc",
        closeout_turn="4",
    )
    lines = body.splitlines()
    assert lines[0] == _RELOAD
    assert lines[1] == _PRESSURE
    assert lines[2] == "lane: agent-bus:13001"
    assert lines[3] == "closeout: turn 4"
    assert lines[4] == (
        "sidecar: cortex://notes/system/threads/13001-cursor-sdk-closeout-auto-abc.md"
    )
    assert lines[5] == (
        "standing handoff: cortex://notes/system/threads/"
        "12286-standing-handoff.md — READ IT; do not trust state here."
    )
    assert len(lines) == 6
    lowered = body.lower()
    assert "next is" not in lowered
    assert "residual" not in lowered
    assert "status:" not in lowered
