"""Conductor ``<task_guidance>`` rendering — sparse birth vs compact hop view."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from implement_admission.conductor_score_io import tip_sha256
from implement_admission.conductor_score_journal import render_sparse_scoreboard
from implement_admission.conductor_score_table import (
    cell,
    row_id_in,
    status_index,
    status_token,
    stops_index,
)
from implement_admission.impresario_policy import ResolvedRoles, resolve_roles

_CLOSED_STATUS = frozenset({"DONE", "CLOSED", "SKIPPED"})


@dataclass(frozen=True, slots=True)
class ResolvedConductorSeats:
    """Threaded seat models for task guidance (no module-global ``resolve_roles()``)."""

    driver_model: str
    review_model: str
    critic_model: str
    summon_mode: str


def seats_from_roles(roles: ResolvedRoles | None = None) -> ResolvedConductorSeats:
    resolved = roles or resolve_roles()
    return ResolvedConductorSeats(
        driver_model=resolved.driver.model,
        review_model=resolved.review.model,
        critic_model=resolved.critic.model,
        summon_mode="",
    )


def _open_gated_rows(body: str) -> list[str]:
    lines: list[str] = []
    idx_status = status_index(body)
    idx_stops = stops_index(body)
    header_seen = False
    for line in body.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        if "Deliverable" in line and "Status" in line:
            header_seen = True
            lines.append(line)
            continue
        if not header_seen:
            continue
        rid = row_id_in(line)
        if not rid:
            if line.strip().startswith("|---"):
                lines.append(line)
            continue
        status = status_token(cell(line, idx_status))
        if status in _CLOSED_STATUS:
            continue
        lines.append(line)
        stops_val = cell(line, idx_stops)
        if stops_val:
            lines.append(f"**Stops ({rid}):** {stops_val}")
    return lines


def _sidecars_table(body: str) -> str:
    in_side = False
    rows: list[str] = []
    for line in body.splitlines():
        if line.strip().startswith("## Sidecars"):
            in_side = True
            rows.append(line)
            continue
        if in_side:
            if line.startswith("## ") and "Sidecars" not in line:
                break
            rows.append(line)
    return "\n".join(rows).strip()


def render_compact_folded_view(tip_body: str) -> str:
    """Open rows, active stops, sidecars, ``tip_sha256`` line (plan § compact view)."""
    sha = tip_sha256(tip_body)
    gated = _open_gated_rows(tip_body)
    sidecars = _sidecars_table(tip_body)
    parts = [
        "## Gated deliverables (open)",
        "",
        *gated,
        "",
        sidecars,
        "",
        f"tip_sha256: {sha}",
    ]
    return "\n".join(parts)


def _render_task_guidance_inner(
    *,
    source_ref: str,
    slug: str,
    entry_gate: str,
    stop_after: str | None,
    rows: tuple[str, ...],
    row_labels: dict[str, str] | None,
    summon_mode: str,
    seats: ResolvedConductorSeats,
    hop_seq: int,
    tip_body: str | None,
) -> str:
    from implement_admission.conductor_critic_render import g3_g5_score_ratify_clause
    from implement_admission.conductor_materialize import (
        attended_g3_g5_task_sentence,
        hop_invariant_g3_g5_fragment,
    )

    if summon_mode == "attended":
        g3_g5_lines = [
            (
                f"{attended_g3_g5_task_sentence()} "
                "CDP harvest alone does not close G5."
            ),
            "Human gate only on explicit see-score or OPERATOR_GATE: ROW_PINNED.",
        ]
    else:
        g3_g5_lines = [
            "G3→G5 default: in-process score-ratify "
            f"({g3_g5_score_ratify_clause()}) via "
            f"team_dispatch(op=generate, model={seats.critic_model}, contract=freeform, "
            "session=ask, dispatch_thread_id=<this worker thread id>).",
            "Explicit see-score: ROW_PINNED at G3 + ping.",
        ]
    ac = [
        "Spawn receipt quotes dispatch_id + scoreboard URI + Lane B.",
        f"Resume at persisted row (entry gate {entry_gate}).",
        *g3_g5_lines,
        (
            f"G6 review: team_dispatch model={seats.review_model}; "
            f"{hop_invariant_g3_g5_fragment()}."
        ),
        f"stop_after={stop_after!r}: run bound leg before ROW_PINNED when set.",
    ]
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(ac, start=1))
    if hop_seq >= 1 and tip_body:
        scoreboard_block = render_compact_folded_view(tip_body)
        hop_tail = (
            "Do not fs-read this tip; tip_sha256 is authoritative. "
            f"Hop verb applies to {source_ref}."
        )
    else:
        scoreboard_block = render_sparse_scoreboard(
            source_ref=source_ref,
            slug=slug,
            entry_gate=entry_gate,
            stop_after=stop_after,
            rows=rows,
            row_labels=row_labels,
        )
        hop_tail = ""
    blocks = [
        "contract: conductor session",
        numbered,
        "",
        f'<scoreboard_tip sha256="{tip_sha256(tip_body) if tip_body else "pending"}">',
        scoreboard_block,
        "</scoreboard_tip>",
    ]
    if hop_tail:
        blocks.extend(["", hop_tail])
    return "\n".join(blocks)


def render_task_guidance(
    ctx: Any,
    *,
    seats: ResolvedConductorSeats | None = None,
    hop_seq: int = 0,
    tip_body: str | None = None,
) -> str:
    """Public entry replacing ``conductor_materialize._render_task_guidance``."""
    resolved_seats = seats or seats_from_roles()
    rm = getattr(ctx, "rematerialize", None)
    seq = hop_seq
    if rm is not None and getattr(rm, "hop_seq", None) is not None:
        seq = int(rm.hop_seq)
    return _render_task_guidance_inner(
        source_ref=ctx.source_ref,
        slug=ctx.slug,
        entry_gate=ctx.entry_gate,
        stop_after=ctx.stop_after,
        rows=ctx.rows,
        row_labels=ctx.row_labels,
        summon_mode=ctx.summon_mode,
        seats=resolved_seats,
        hop_seq=seq,
        tip_body=tip_body,
    )


def closed_row_stop_paragraph_absent_in(text: str) -> bool:
    """True when text lacks long closed-row stop prose (AC3 helper)."""
    return not re.search(r"Stops \(G[1-7]\):.*landed", text, re.IGNORECASE)
