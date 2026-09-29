"""Delivered-body template for an operator-CSE closeout followup.

The wake arrives with the skill chip strip empty. Line 1 re-arms the seat;
line 2 is the context-pressure check; the rest are pointers. Rank, residual
lists, and ordinal state claims are forbidden in this body.
"""

from __future__ import annotations

SKILL_RELOAD_LINE = (
    "WAKE. First: reload skills per the opening prompt — "
    "Use the cdp-operator-proxy skill; "
    "Use the reasoning-posture skill; "
    "Use the hypothesize-simulate skill; "
    "Use the completion-provenance-discipline skill; "
    "Use the agent-bus-discipline skill; "
    "Use the lane-act-gates skill; "
    "Use the retrieval-before-authoring skill. "
    "Then read cortex://notes/system/threads/{lane}-standing-handoff.md "
    "(first section) and cortex://notes/system/maestro/journal.md "
    "(loop + latest entry). Do not trust rank stated here."
)

CONTEXT_PRESSURE_LINE = (
    "Context-pressure check (journal step 9): if ≥2 of "
    "{≥6 closeouts harvested, skills reloaded more than once, "
    "a tool result spilled to file, replies summarizing not quoting} hold, "
    "bump the standing handoff and hop before harvesting."
)


def render_operator_wake_body(
    *,
    owner_lane: str,
    child_lane: str,
    dispatch_id: str,
    closeout_turn: str,
) -> str:
    """Return the followup body: reload line, pressure check, pointers only."""
    lane = (owner_lane or child_lane or "").strip()
    child = (child_lane or lane).strip()
    turn = (closeout_turn or "").strip() or "unknown"
    reload_line = SKILL_RELOAD_LINE.format(lane=lane)
    sidecar = (
        f"cortex://notes/system/threads/{child}-cursor-sdk-closeout-{dispatch_id}.md"
    )
    handoff = (
        f"cortex://notes/system/threads/{lane}-standing-handoff.md "
        "— READ IT; do not trust state here."
    )
    return "\n".join(
        (
            reload_line,
            CONTEXT_PRESSURE_LINE,
            f"lane: agent-bus:{child}",
            f"closeout: turn {turn}",
            f"sidecar: {sidecar}",
            f"standing handoff: {handoff}",
        )
    )


__all__ = [
    "CONTEXT_PRESSURE_LINE",
    "SKILL_RELOAD_LINE",
    "render_operator_wake_body",
]
