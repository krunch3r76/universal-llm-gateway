"""L2 orientation generator — arrival card + handoff_prompt from live substrate.

Composes ``handoff_prompt = cse_state + lane tip + open obligations`` (7119 L2 /
CSR Phase 4 shape). Generation replaces hand-maintained leg docs for orientation;
regenerated snapshots carry live state with ``generated_at`` (see constitution
verdict in module docstring on ``L2_CONSTITUTION``).
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from claude_bundles.cdp_registry_store import load_sessions
from claude_bundles.cse_session_common import find_session_by_thread
from claude_bundles.cse_session_obligations import get_open_wake_owed

# Binding verdict (7119 L2 AC1): generated artifacts carry live state — safe
# because regenerated at read/generation time, not authored once. The v2 manual
# card's pointer-only constitution applies to static files only.
L2_CONSTITUTION = "live-snapshot"

_ARRIVAL_CARD_URI = "cortex://notes/system/threads/{thread_id}-arrival-card.md"
_STANDING_HANDOFF_URI = "cortex://notes/system/threads/{thread_id}-standing-handoff.md"
_ARRIVAL_RULES_URI = "cortex://notes/system/threads/{thread_id}-arrival-card.md"

# Screen budget: ~45 lines × ~90 cols (operator "one screen" on 7119 L2).
MAX_ARRIVAL_LINES = 45
MAX_ARRIVAL_CHARS = 4000

_ADMIT_SUBJECT_PREFIX = "status:admitted"
_FROM_AUTO = "cursor-auto"


@dataclass(frozen=True)
class SourceAttribution:
    """Where one composed slice came from — substrate vs hand-maintained."""

    slice_name: str
    source: str
    queryable: bool
    note: str = ""


@dataclass
class CseStateSlice:
    """Projection row fragment for one lane thread."""

    thread_id: str
    cse_id: str | None = None
    phase: str | None = None
    obligations: list[dict[str, Any]] = field(default_factory=list)
    source: str = "sessions.json"
    absent: bool = False


@dataclass
class LaneTipSlice:
    """Latest bus turn summary for the lane."""

    thread_id: str
    turn_number: int | None = None
    turn_id: int | None = None
    subject: str | None = None
    from_agent: str | None = None
    created_at: str | None = None
    body_excerpt: str | None = None
    source: str = "agent_bus.turns"
    absent: bool = False


@dataclass
class AdmitTurnBind:
    """Latest cursor-auto admit turn — closes L2 inheritance loop when present."""

    turn_number: int
    turn_id: int | None
    subject: str
    body_excerpt: str
    source: str = "agent_bus.turns"


@dataclass
class L2GenerationResult:
    """Fully composed L2 outputs plus provenance for closeout / render proof."""

    thread_id: str
    generated_at: str
    constitution: str
    arrival_card: str
    handoff_prompt: str
    sources: list[SourceAttribution]
    dropped_sections: list[str]
    admit_turn_bind: AdmitTurnBind | None
    inheritance_loop_closed: bool


def read_cse_state(*, thread_id: str) -> CseStateSlice:
    """Read CSR projection row for ``thread_id`` from ``sessions.json``."""
    sessions = load_sessions()
    found = find_session_by_thread(sessions, thread_id)
    if found is None:
        return CseStateSlice(thread_id=thread_id, absent=True, source="sessions.json")
    _, row = found
    return CseStateSlice(
        thread_id=thread_id,
        cse_id=row.get("cse_id"),
        phase=row.get("phase"),
        obligations=list(row.get("obligations") or []),
        source="~/.gateway/cdp-registry/sessions.json",
        absent=False,
    )


def extract_lane_tip(*, thread_id: str, turns: list[dict[str, Any]]) -> LaneTipSlice:
    """Summarize the latest turn on ``thread_id`` from fetched bus turns."""
    if not turns:
        return LaneTipSlice(thread_id=thread_id, absent=True)
    latest = max(turns, key=lambda t: int(t.get("turn_number") or 0))
    body = str(latest.get("body") or "")
    excerpt = _excerpt(body, max_chars=480)
    return LaneTipSlice(
        thread_id=thread_id,
        turn_number=int(latest["turn_number"]) if latest.get("turn_number") else None,
        turn_id=int(latest["id"]) if latest.get("id") else None,
        subject=str(latest.get("subject") or ""),
        from_agent=str(latest.get("from") or ""),
        created_at=str(latest.get("created_at") or ""),
        body_excerpt=excerpt,
        absent=False,
    )


def find_latest_admit_turn(turns: list[dict[str, Any]]) -> AdmitTurnBind | None:
    """Return the newest cursor-auto admit turn — inheritance-loop bind target."""
    admits = [
        t
        for t in turns
        if str(t.get("from") or "") == _FROM_AUTO
        and str(t.get("subject") or "").startswith(_ADMIT_SUBJECT_PREFIX)
    ]
    if not admits:
        return None
    latest = max(admits, key=lambda t: int(t.get("turn_number") or 0))
    body = str(latest.get("body") or "")
    return AdmitTurnBind(
        turn_number=int(latest["turn_number"]),
        turn_id=int(latest["id"]) if latest.get("id") else None,
        subject=str(latest.get("subject") or ""),
        body_excerpt=_excerpt(body, max_chars=600),
    )


def collect_open_obligations(
    *,
    thread_id: str,
    cse: CseStateSlice,
    lane_tip: LaneTipSlice,
) -> tuple[list[dict[str, Any]], list[SourceAttribution]]:
    """Merge queryable open obligations; attribute non-substrate gaps honestly."""
    items: list[dict[str, Any]] = []
    sources: list[SourceAttribution] = []

    sessions = load_sessions()
    wake = get_open_wake_owed(sessions, thread=thread_id)
    if wake:
        items.append(
            {
                "kind": wake.get("kind", "wake_owed"),
                "status": wake.get("status"),
                "since": wake.get("since"),
                "ttl_deadline": wake.get("ttl_deadline"),
                "source": "~/.gateway/cdp-registry/sessions.json",
            }
        )
    sources.append(
        SourceAttribution(
            slice_name="cse_obligations",
            source="sessions.json via get_open_wake_owed",
            queryable=True,
        )
    )

    for ob in cse.obligations:
        if ob.get("status") in ("open", "alarmed"):
            items.append({**ob, "source": cse.source})

    if lane_tip.turn_number and not lane_tip.absent:
        first_line = (lane_tip.body_excerpt or "").split("\n", 1)[0].strip()
        if first_line.startswith("TYPE: PARKED"):
            items.append(
                {
                    "kind": "parked_lane",
                    "status": "open",
                    "turn": lane_tip.turn_number,
                    "source": "agent_bus latest turn",
                }
            )

    sources.append(
        SourceAttribution(
            slice_name="lane_tip",
            source="agent_bus /turns?thread=" + thread_id,
            queryable=True,
        )
    )

    # Honest gap: arc-level open items (leg doc, commission carry-forward) are
    # still hand-maintained — L2 does not yet remove that cost.
    sources.append(
        SourceAttribution(
            slice_name="arc_open_items",
            source="cortex leg doc (6655-leg*-*.md) Open section",
            queryable=False,
            note="NOT YET SUBSTRATE — successors must still read leg doc for these",
        )
    )
    return items, sources


def format_cse_state_section(cse: CseStateSlice) -> str:
    """Render cse_state slice for handoff_prompt."""
    if cse.absent:
        return (
            f"cse_state: absent for thread {cse.thread_id} "
            f"(no sessions.json row; query project_ask op=cse_state when Phase 4 lands)"
        )
    ob_summary: dict[tuple[str, str], int] = {}
    for o in cse.obligations:
        if o.get("status") in ("open", "alarmed"):
            key = (str(o.get("kind", "?")), str(o.get("status", "?")))
            ob_summary[key] = ob_summary.get(key, 0) + 1
    parts = [
        f"{kind}:{status}" + (f" x{n}" if n > 1 else "")
        for (kind, status), n in sorted(ob_summary.items())
    ]
    return (
        f"cse_state: cse_id={cse.cse_id or 'unknown'} "
        f"phase={cse.phase or 'unset'} "
        f"open_obligations=[{', '.join(ob_summary) or 'none'}] "
        f"source={cse.source}"
    )


def format_lane_tip_section(tip: LaneTipSlice) -> str:
    """Render lane tip slice for handoff_prompt."""
    if tip.absent:
        return f"lane_tip: absent (no turns fetched for thread {tip.thread_id})"
    return (
        f"lane_tip: turn={tip.turn_number} from={tip.from_agent} "
        f"subject={tip.subject!r} at={tip.created_at}\n"
        f"excerpt: {tip.body_excerpt}"
    )


def format_obligations_section(obligations: list[dict[str, Any]]) -> str:
    """Render open obligations list for handoff_prompt."""
    if not obligations:
        return "open_obligations: (none queryable at generation time)"
    lines = ["open_obligations:"]
    counts: dict[tuple[str, str], int] = {}
    for ob in obligations:
        key = (str(ob.get("kind", "?")), str(ob.get("status", "?")))
        counts[key] = counts.get(key, 0) + 1
    for (kind, status), count in sorted(counts.items()):
        suffix = f" x{count}" if count > 1 else ""
        lines.append(f"  - {kind} status={status}{suffix} source=sessions.json|agent_bus")
    lines.append(
        "  NOTE: arc-level open items in leg doc are hand-maintained — not included above."
    )
    return "\n".join(lines)


def compose_handoff_prompt(
    *,
    cse: CseStateSlice,
    tip: LaneTipSlice,
    obligations: list[dict[str, Any]],
    admit_bind: AdmitTurnBind | None,
    generated_at: str,
) -> str:
    """Mechanical compose: cse_state + lane tip + open obligations (+ admit bind)."""
    parts = [
        f"handoff_prompt generated_at={generated_at} constitution={L2_CONSTITUTION}",
        format_cse_state_section(cse),
        format_lane_tip_section(tip),
        format_obligations_section(obligations),
    ]
    if admit_bind:
        parts.extend(
            [
                "",
                "admit_turn_bind (inheritance-loop closure — read this, do not mark-read-only):",
                f"  turn={admit_bind.turn_number} subject={admit_bind.subject!r}",
                f"  body_excerpt: {admit_bind.body_excerpt}",
            ]
        )
    else:
        parts.append(
            "admit_turn_bind: absent — inheritance loop NOT closed; fetch latest "
            f"cursor-auto admit on thread {tip.thread_id}"
        )
    return "\n".join(parts)


def render_arrival_card(
    *,
    thread_id: str,
    generated_at: str,
    cse: CseStateSlice,
    tip: LaneTipSlice,
    obligations: list[dict[str, Any]],
    admit_bind: AdmitTurnBind | None,
) -> tuple[str, list[str]]:
    """Render ≤1-screen generated arrival card; return (card, dropped_sections)."""
    dropped: list[str] = []
    rules_uri = _ARRIVAL_RULES_URI.format(thread_id=thread_id)
    standing_uri = _STANDING_HANDOFF_URI.format(thread_id=thread_id)

    lines = [
        f"# {thread_id} — GENERATED ARRIVAL CARD (L2)",
        "",
        f"generated_at: {generated_at}",
        f"constitution: {L2_CONSTITUTION} (regenerated; stale-on-read is a bug)",
        "",
        "## Live snapshot",
        f"- lane: turn {tip.turn_number} · {tip.from_agent} · {tip.subject}",
        f"- cse: id={cse.cse_id or '?'} phase={cse.phase or 'unset'}",
    ]
    if obligations:
        counts: dict[tuple[str, str], int] = {}
        for ob in obligations:
            key = (str(ob.get("kind", "?")), str(ob.get("status", "?")))
            counts[key] = counts.get(key, 0) + 1
        lines.append("- obligations (substrate):")
        for (kind, status), count in sorted(counts.items()):
            suffix = f" x{count}" if count > 1 else ""
            lines.append(f"  · {kind} ({status}){suffix}")
    else:
        lines.append("- obligations: none queryable")

    if admit_bind:
        lines.extend(
            [
                f"- latest admit: turn {admit_bind.turn_number} — included in handoff_prompt",
            ]
        )
    else:
        lines.append("- latest admit: absent — inheritance loop open")

    lines.extend(
        [
            "",
            "## Durable rules (abbreviated — full set in manual card)",
            "- Probe inherited NOs before obeying capability limits.",
            "- Set from_agent on every bus send/request.",
            "- Diff authored directive against minted row (field parity).",
            "- Queue = lane tip, not this card or standing handoff Next intent.",
            "- Never retire on commissioning claim; successor must author a turn.",
            "",
            "## Pointers",
            f"- Manual rules card: {rules_uri}",
            f"- Adjudication only: {standing_uri}",
            f"- Re-verify tip: agent_bus_read thread={thread_id}",
        ]
    )

    card = "\n".join(lines)
    if len(lines) > MAX_ARRIVAL_LINES:
        excess = lines[MAX_ARRIVAL_LINES:]
        dropped.append(f"lines over budget ({len(lines)}>{MAX_ARRIVAL_LINES})")
        card = "\n".join(lines[:MAX_ARRIVAL_LINES])
        if excess:
            card += f"\n\n… [{len(excess)} lines dropped for screen budget]"

    if len(card) > MAX_ARRIVAL_CHARS:
        dropped.append(f"chars over budget ({len(card)}>{MAX_ARRIVAL_CHARS})")
        card = card[: MAX_ARRIVAL_CHARS - 40] + "\n… [truncated for screen budget]"

    return card, dropped


def generate_l2_orientation(
    *,
    thread_id: str,
    turns: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> L2GenerationResult:
    """Generate arrival card + handoff_prompt from live substrate inputs."""
    ts = generated_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    turn_list = turns or []
    cse = read_cse_state(thread_id=thread_id)
    tip = extract_lane_tip(thread_id=thread_id, turns=turn_list)
    obligations, sources = collect_open_obligations(
        thread_id=thread_id, cse=cse, lane_tip=tip
    )
    admit_bind = find_latest_admit_turn(turn_list) if turn_list else None
    handoff = compose_handoff_prompt(
        cse=cse,
        tip=tip,
        obligations=obligations,
        admit_bind=admit_bind,
        generated_at=ts,
    )
    card, dropped = render_arrival_card(
        thread_id=thread_id,
        generated_at=ts,
        cse=cse,
        tip=tip,
        obligations=obligations,
        admit_bind=admit_bind,
    )
    return L2GenerationResult(
        thread_id=thread_id,
        generated_at=ts,
        constitution=L2_CONSTITUTION,
        arrival_card=card,
        handoff_prompt=handoff,
        sources=sources,
        dropped_sections=dropped,
        admit_turn_bind=admit_bind,
        inheritance_loop_closed=admit_bind is not None,
    )


def _excerpt(text: str, *, max_chars: int) -> str:
    cleaned = textwrap.dedent(text).strip().replace("\r\n", "\n")
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3] + "..."
