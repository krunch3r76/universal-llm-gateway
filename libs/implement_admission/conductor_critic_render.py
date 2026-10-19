"""Per-transport critic rendering and width-seat marker blocks."""

from __future__ import annotations

from implement_admission.impresario_envelope import render_knobs
from implement_admission.impresario_policy import ResolvedRoles, SeatSpec, resolve_roles

WIDTH_SEAT_MARKER_START = "<!-- width-seat:v1:start -->"
WIDTH_SEAT_MARKER_END = "<!-- width-seat:v1:end -->"

_CHANNEL_BY_FAMILY = {"opus": "Opus", "fable": "Fable", "gemini": "Cursor", "muse": "Cursor", "grok": "Cursor", "composer": "Cursor"}


def _usage_channel(model: str) -> str:
    if model.startswith("cursor/"):
        return "Cursor"
    cli_id = model.removeprefix("cdp/")
    family = cli_id.split("-", 1)[0]
    channel = _CHANNEL_BY_FAMILY.get(family)
    if channel is None:
        msg = f"unknown width-seat model family for {model!r}"
        raise ValueError(msg)
    return channel


def _knob_phrase(seat: SeatSpec) -> str:
    knobs = render_knobs(seat)
    if "reasoning_effort" in knobs:
        return f"`reasoning_effort={knobs['reasoning_effort']}`"
    mk = knobs.get("model_knobs") or {}
    parts = [f"`model_knobs={mk}`"]
    return " · ".join(parts)


def width_seat_block(roles: ResolvedRoles | None = None) -> str:
    """Render the width-seat marker block from resolved Pattern A roles."""
    resolved = roles or resolve_roles()
    critic = resolved.critic
    channel = _usage_channel(critic.model)
    line = (
        "> **Width seat** (generated from "
        "`libs/implement_admission/conductor_critic_render.py`; do not hand-edit): "
        f"critic = `{critic.model}` · {_knob_phrase(critic)} · "
        f"usage channel **{channel}**. "
        f"SKETCH (G1) = `{resolved.sketch.model}` · {_knob_phrase(resolved.sketch)}. "
        f"SKEPTIC (G4) = `{resolved.skeptic.model}` · {_knob_phrase(resolved.skeptic)} "
        "(cross-family review). "
        f"RESTORE = `{resolved.restore.model}` · {_knob_phrase(resolved.restore)} "
        "(operator restore preset)."
    )
    return "\n".join((WIDTH_SEAT_MARKER_START, line, WIDTH_SEAT_MARKER_END))


def embed_width_seat_block(text: str, roles: ResolvedRoles | None = None) -> str:
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
    block = width_seat_block(roles)
    return text[:start] + block + text[end + len(WIDTH_SEAT_MARKER_END) :]


def g3_g5_score_ratify_clause(roles: ResolvedRoles | None = None) -> str:
    """Name the width seat inside a G3→G5 score-ratify sentence."""
    resolved = roles or resolve_roles()
    seat = resolved.critic
    knobs = render_knobs(seat)
    if "reasoning_effort" in knobs:
        return f"{seat.model}, reasoning_effort={knobs['reasoning_effort']}"
    mk = knobs.get("model_knobs") or {}
    return f"{seat.model}, model_knobs={mk}"


def review_leg_transport_hint(seat: SeatSpec) -> str:
    """Return PARKED_TRANSPORT paragraph only for CDP parked critics."""
    if seat.transport == "cdp_parked":
        return (
            "After G6 admit, write poll_hint and execution_id into the CHECKPOINT, "
            "and write `NEXT_ADMIT: harvest <execution_id>` before `stop: PARKED_TRANSPORT`."
        )
    return (
        "After G6 admit, poll the nested cursor-sdk closeout in-hop; "
        "do not use `stop: PARKED_TRANSPORT` for cursor/* critics."
    )
