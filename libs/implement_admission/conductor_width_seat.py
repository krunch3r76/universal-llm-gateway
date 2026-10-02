"""One assignment selects the conductor width seat.

G2, G3→G5, path-sim Q, the hop-5 check, and the dispatch-kernel width
cell read ``ACTIVE``. Shipped ``ACTIVE`` is ``cdp/opus-5.5`` at
``reasoning_effort="high"``. G1 (sketcher) reads ``SKETCH`` and G4 (skeptic)
reads ``SKEPTIC``, both ``cdp/opus-5.5`` at ``reasoning_effort="extra"``:
one effort rung above the high rows. The skeptic stays on that rung because
a cross-family reviewer is not on the Opus channel. ``ACTIVE = RESTORE``
returns the width rows to ``cdp/fable-5.1``. There is no env var or todo attr.
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
# Ordinary width rows. ACTIVE = RESTORE returns those rows to cdp/fable-5.1.
ACTIVE = ConductorWidthSeat(
    model="cdp/opus-5.5",
    reasoning_effort="high",
    effort_when_bind_gates_wave="max",
)
# G1 shape sketch. One effort rung above ACTIVE.
SKETCH = ConductorWidthSeat(
    model="cdp/opus-5.5",
    reasoning_effort="extra",
    effort_when_bind_gates_wave="max",
)
# G4 skeptic. One effort rung above ACTIVE: the cross-family check
# while both seats remain on the Opus channel.
SKEPTIC = ConductorWidthSeat(
    model="cdp/opus-5.5",
    reasoning_effort="extra",
    effort_when_bind_gates_wave="max",
)


WIDTH_SEAT_MARKER_START = "<!-- width-seat:v1:start -->"
WIDTH_SEAT_MARKER_END = "<!-- width-seat:v1:end -->"

_CHANNEL_BY_FAMILY = {"opus": "Opus", "fable": "Fable"}


def _usage_channel(model: str) -> str:
    cli_id = model.removeprefix("cdp/")
    family = cli_id.split("-", 1)[0]
    channel = _CHANNEL_BY_FAMILY.get(family)
    if channel is None:
        msg = f"unknown width-seat model family for {model!r}"
        raise ValueError(msg)
    return channel


def width_seat_block(seat: ConductorWidthSeat | None = None) -> str:
    """Render the width-seat marker block from ACTIVE (or *seat*)."""
    chosen = ACTIVE if seat is None else seat
    cli_id = chosen.model.removeprefix("cdp/")
    channel = _usage_channel(chosen.model)
    line = (
        "> **Width seat** (generated from "
        "`libs/implement_admission/conductor_width_seat.py`; do not hand-edit): "
        f"ACTIVE = `{chosen.model}` · `reasoning_effort={chosen.reasoning_effort}` · "
        f"`effort_when_bind_gates_wave={chosen.effort_when_bind_gates_wave}` "
        "only when a bind gates a wave · "
        f"CLI `--model {cli_id}` · usage channel **{channel}**. "
        f"SKETCH (G1) = `{SKETCH.model}` · "
        f"`reasoning_effort={SKETCH.reasoning_effort}`. "
        f"SKEPTIC (G4) = `{SKEPTIC.model}` · "
        f"`reasoning_effort={SKEPTIC.reasoning_effort}` "
        "(one rung above ACTIVE; cross-family review stays on this effort). "
        f"RESTORE = `{RESTORE.model}` · "
        f"`reasoning_effort={RESTORE.reasoning_effort}` "
        "(only when Kaywan asks; set `ACTIVE = RESTORE`)."
    )
    return "\n".join((WIDTH_SEAT_MARKER_START, line, WIDTH_SEAT_MARKER_END))


def embed_width_seat_block(
    text: str, seat: ConductorWidthSeat | None = None
) -> str:
    """Replace the width-seat marker span with a freshly rendered block."""
    start_count = text.count(WIDTH_SEAT_MARKER_START)
    end_count = text.count(WIDTH_SEAT_MARKER_END)
    if start_count != 1 or end_count != 1:
        msg = (
            f"width-seat markers: expected exactly one start and one end, "
            f"got {start_count} start and {end_count} end"
        )
        raise ValueError(msg)
    start = text.index(WIDTH_SEAT_MARKER_START)
    end = text.index(WIDTH_SEAT_MARKER_END)
    if end < start:
        msg = "width-seat end marker precedes start marker"
        raise ValueError(msg)
    block = width_seat_block(seat)
    return text[:start] + block + text[end + len(WIDTH_SEAT_MARKER_END) :]


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
