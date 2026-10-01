"""One assignment selects the conductor width seat.

G1, G2, G4, G3→G5, path-sim Q, the hop-5 check, and the dispatch-kernel
width cell read ``ACTIVE``. ``ACTIVE = RESTORE`` ships ``cdp/fable-5.1``.
Spending Opus again is assigning ``ACTIVE`` a ``ConductorWidthSeat`` with
``model="cdp/opus-5"`` and ``reasoning_effort="max"``. There is no second
switch, env var, or todo attr.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConductorWidthSeat:
    """CDP model and efforts for the conductor width rows.

    ``reasoning_effort`` is the string a later CDP ``team_dispatch`` passes.
    ``effort_when_bind_gates_wave`` applies only when a bind gates a wave.
    """

    model: str
    reasoning_effort: str
    effort_when_bind_gates_wave: str


RESTORE = ConductorWidthSeat(
    model="cdp/fable-5.1",
    reasoning_effort="high",
    effort_when_bind_gates_wave="max",
)
# Shipped seat. Assign a different ConductorWidthSeat to spend Opus again.
ACTIVE = RESTORE


def g3_g5_score_ratify_clause(seat: ConductorWidthSeat | None = None) -> str:
    """Name the width seat inside a G3→G5 score-ratify sentence.

    Called at format time so production reads ``ACTIVE`` and a test can pass
    ``RESTORE`` and still see ``cdp/fable-5.1`` without editing that assignment.
    """
    chosen = ACTIVE if seat is None else seat
    return (
        f"{chosen.model}, reasoning_effort={chosen.reasoning_effort}, "
        "effort_when_bind_gates_wave="
        f"{chosen.effort_when_bind_gates_wave} when a bind gates a wave"
    )
