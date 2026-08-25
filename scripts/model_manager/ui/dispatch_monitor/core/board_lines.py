"""Pure projection-to-text helpers for the curses dispatch board (Rival A).

No I/O. Membership / order / ribbons only — View filter + tally over DTOs.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .dtos import (
    AttentionItem,
    CdpLegRow,
    CharterRootRow,
    HealthProjection,
    SdkDispatchRow,
)
from .sdk_posture import (
    ROW_TAG,
    SdkMultiPosture,
    classify_sdk_live,
    posture_legend,
    row_role,
    sort_sdk_live,
)
from .watch import SEVERITY_MARK, _ms, _truncate, clip_text

_TZ = ZoneInfo("America/Los_Angeles")


def la_clock() -> str:
    return datetime.now(_TZ).strftime("%H:%M:%S %Z")


def hold_label(held: bool | None) -> str:
    if held is True:
        return "yes"
    if held is False:
        return "no"
    return "?"


def resolve_hold(
    *,
    from_events: bool | None,
    from_poll: bool | None,
) -> bool | None:
    """Prefer event-folded hold; fall back to manage.sock poll."""
    if from_events is not None:
        return from_events
    return from_poll


def section_bar(title: str, live: int, window_total: int, width: int, *, unit: str) -> str:
    label = f" {title} ({live} live · {window_total} {unit}) "
    pad = max(0, width - len(label) - 2)
    return f"─{label}{'─' * pad}"


def la_wall_from_ms(ms: int) -> str:
    """Format an event timestamp as local wall clock (board TZ)."""
    return datetime.fromtimestamp(ms / 1000.0, tz=_TZ).strftime("%H:%M %Z")


def attention_line(
    item: AttentionItem,
    width: int,
    *,
    now_ms: int | None = None,
) -> str:
    mark = SEVERITY_MARK.get(item.severity, "  ")
    elapsed = item.age_ms
    if elapsed is None and item.since_ms is not None and now_ms is not None:
        elapsed = max(0, now_ms - item.since_ms)
    age = f"{_ms(elapsed):>7}"
    body = f"{mark} {age} [{item.kind}] {item.subject}: {item.title}"
    if item.detail and len(body) < width - 8:
        body = f"{body} — {item.detail[: width - len(body) - 3]}"
    return _truncate(body, width)


def primary_tick_objective(
    roots_active: list[CharterRootRow],
) -> tuple[str, str, str] | None:
    """Pick (root_id, kind, text) for the TICK section subtitle.

    Prefer an ``in_flight`` root with an objective; else any active root with
    scoreboard objective; else bus summary/slug. Truncation belongs to paint.
    """
    preferred = [r for r in roots_active if r.state == "in_flight" and r.objective]
    if not preferred:
        preferred = [r for r in roots_active if r.objective]
    if preferred:
        row = preferred[0]
        assert row.objective is not None
        return row.root_id, "obj", row.objective
    preferred = [
        r for r in roots_active if r.state == "in_flight" and (r.bus_summary or r.bus_slug)
    ]
    if not preferred:
        preferred = [r for r in roots_active if r.bus_summary or r.bus_slug]
    if not preferred:
        return None
    row = preferred[0]
    text = row.bus_summary or row.bus_slug
    assert text is not None
    return row.root_id, "bus", text


def tick_objective_line(root_id: str, text: str, width: int, *, kind: str = "obj") -> str:
    """One glance line under TICK / ACTIVE for charter identity."""
    prefix = "obj" if kind == "obj" else "bus"
    return _truncate(f"  {prefix}[{root_id}]: {text}", width)


def live_sdk(rows: tuple[SdkDispatchRow, ...]) -> list[SdkDispatchRow]:
    live = [row for row in rows if row.terminal_ms is None]
    return sort_sdk_live(live, classify_sdk_live(live))


def sdk_live_posture(live: list[SdkDispatchRow]) -> SdkMultiPosture:
    """Expose multi-row posture for section bar / legend paint."""
    return classify_sdk_live(live)


def sdk_posture_legend(posture: SdkMultiPosture) -> str | None:
    """Legend line under the SDK bar when multi; None for solo."""
    return posture_legend(posture)


def live_cdp(rows: tuple[CdpLegRow, ...]) -> list[CdpLegRow]:
    live = [row for row in rows if row.terminal_ms is None]
    live.sort(key=lambda row: -(row.elapsed_ms or 0))
    return live


#: Still owed work — enqueued / in flight / waiting operator or skip decision.
_ROOT_ACTIVE_STATES = frozenset(
    {"in_flight", "waiting_open", "skipped", "parked", "unknown", "consult_queued", "stuck"}
)

#: Parked residue — not enqueued. Shown under SET ASIDE, never mixed with active.
_ROOT_ASIDE_STATES = frozenset(
    {"failed", "window_closed", "intent_healed"}
)


def open_roots(roots: tuple[CharterRootRow, ...]) -> list[CharterRootRow]:
    """Non-closed / non-unenrolled roots (active ∪ set-aside)."""
    return [row for row in roots if not row.closed and not row.unenrolled]


def active_roots(roots: tuple[CharterRootRow, ...]) -> list[CharterRootRow]:
    """Enqueued or in-work roots only — what the operator expects at a glance."""
    rows = [
        row
        for row in open_roots(roots)
        if row.state in _ROOT_ACTIVE_STATES
    ]
    rows.sort(key=lambda row: (0 if row.state == "in_flight" else 1, row.root_id))
    return rows


def aside_roots(roots: tuple[CharterRootRow, ...]) -> list[CharterRootRow]:
    """Set-aside residue (failed / window_closed / …) — separate from enqueued."""
    active_ids = {row.root_id for row in active_roots(roots)}
    rows = [row for row in open_roots(roots) if row.root_id not in active_ids]
    rows.sort(
        key=lambda row: (
            0 if row.state == "failed" else 1,
            0 if row.state in _ROOT_ASIDE_STATES else 2,
            row.root_id,
        )
    )
    return rows


def oldest_idle_ms(rows: list[SdkDispatchRow] | list[CdpLegRow]) -> int | None:
    """Max stall-idle age (SDK) or wall elapsed (CDP) among live rows."""
    ages: list[int] = []
    for row in rows:
        if isinstance(row, SdkDispatchRow) and row.idle_age_ms is not None:
            ages.append(row.idle_age_ms)
        elif isinstance(row, CdpLegRow) and row.elapsed_ms is not None:
            ages.append(row.elapsed_ms)
    return max(ages) if ages else None


def oldest_elapsed_ms(rows: list[SdkDispatchRow] | list[CdpLegRow]) -> int | None:
    """Max wall elapsed among live rows (SDK ``elapsed_ms`` / CDP ``elapsed_ms``)."""
    ages: list[int] = []
    for row in rows:
        if row.elapsed_ms is not None:
            ages.append(row.elapsed_ms)
    return max(ages) if ages else None


def _sdk_live_timing(row: SdkDispatchRow) -> str:
    """Queue position, else wall elapsed + progress-idle (stall cue)."""
    if row.queue_position is not None:
        return f"q{row.queue_position}"
    parts: list[str] = [f"el={_ms(row.elapsed_ms)}"]
    if row.idle_age_ms is not None:
        parts.append(f"idle={_ms(row.idle_age_ms)}")
    return " ".join(parts)


def sdk_live_line(
    row: SdkDispatchRow,
    *,
    live: list[SdkDispatchRow] | None = None,
    posture: SdkMultiPosture | None = None,
    width: int = 120,
) -> str:
    peers = live if live is not None else [row]
    multi = posture if posture is not None else classify_sdk_live(peers)
    role = row_role(row, peers, multi)
    role_tag = f"{ROW_TAG[role]} " if role else ""
    flag = "DIVERGENT" if row.divergent_fields else ",".join(row.emitters_seen) or "-"
    timing = _sdk_live_timing(row)
    stall = f" stall={row.stall_stage}" if row.stall_stage else ""
    tc = f" tc={row.tool_call_count}" if row.tool_call_count is not None else ""
    topic_col = f" topic={clip_text(row.topic, 28)}" if row.topic else ""
    if row.last_tool_name:
        tool = row.last_tool_name
        if row.last_tool_status and row.last_tool_status != "completed":
            tool = f"{tool}:{row.last_tool_status}"
        tool_col = f" tool={_truncate(tool, 14)}"
    else:
        tool_col = ""
    prov = f" from={row.caller_from or 'ide'} via={row.caller_via or 'http'}"
    base = (
        f"  {role_tag}{_truncate(row.dispatch_id, 14)} {_truncate(row.state, 10)} "
        f"root={_truncate(row.root_id, 8)} "
        f"w={_truncate(row.thread_id, 8)} "
        f"{_truncate(row.model, 18)}{topic_col} "
        f"{timing}{tc}{tool_col}{prov} [{flag}]{stall}"
    )
    extras: list[str] = []
    if row.nest_under:
        extras.append(f" nest={clip_text(row.nest_under, 12)}")
    for token in extras:
        if len(base) + len(token) <= width:
            base += token
        else:
            break
    return _truncate(base, width)


def primary_sdk_dispatch_for_root(
    root_id: str,
    sdk_rows: tuple[SdkDispatchRow, ...],
) -> str | None:
    """Best dispatch_id linked to ``root_id`` — live first, else newest terminal."""
    linked = [row for row in sdk_rows if row.root_id == root_id]
    if not linked:
        return None
    live = [row for row in linked if row.terminal_ms is None]
    if live:
        live.sort(key=lambda row: -(row.idle_age_ms or 0))
        return live[0].dispatch_id
    linked.sort(key=lambda row: -(row.terminal_ms or 0))
    return linked[0].dispatch_id


def root_line_live(
    row: CharterRootRow,
    *,
    sdk_n: int,
    cdp_n: int,
    width: int = 120,
    sdk_dispatch_id: str | None = None,
    omit_identity_tail: bool = False,
) -> str:
    """Paint one root row.

    When ``omit_identity_tail`` is set (TICK subtitle already shows this root's
    ``obj:``/``bus:``), skip repeating that identity on the row — keep skip_reason
    only when it is the sole leftover cue.
    """
    step = row.arc_g_step or row.pickup_gid or "-"
    d_col = f" d={_truncate(sdk_dispatch_id, 12)}" if sdk_dispatch_id else ""
    if omit_identity_tail:
        # Subtitle owns purpose; row keeps operational columns only.
        if row.skip_reason and not (row.objective or row.bus_summary or row.bus_slug):
            tail = f" {_truncate(row.skip_reason, 20)}"
        else:
            tail = ""
    elif row.objective:
        tail = f" obj: {_truncate(row.objective, max(24, width - 84))}"
    elif row.bus_summary or row.bus_slug:
        identity = row.bus_summary or row.bus_slug or ""
        tail = f" bus: {_truncate(identity, max(24, width - 84))}"
    elif row.skip_reason:
        tail = f" {_truncate(row.skip_reason, 20)}"
    else:
        tail = ""
    return (
        f"  {_truncate(row.root_id, 8)} {_truncate(row.state, 14)} "
        f"g={_truncate(step, 5)} worker={_truncate(row.worker_thread, 8)} "
        f"age={_ms(row.in_flight_age_ms):>7} skips={row.skip_streak:<3} "
        f"sdk={sdk_n} cdp={cdp_n}{d_col}{tail}"
    )


def sdk_terminal_tally(
    rows: tuple[SdkDispatchRow, ...],
) -> tuple[int, int, int, int | None]:
    done = [row for row in rows if row.terminal_ms is not None]
    ok = sum(1 for row in done if not row.failure_reason and not row.delivery_failed)
    fail = sum(1 for row in done if row.failure_reason or row.delivery_failed)
    div = sum(1 for row in done if row.divergent_fields)
    last_ms = max((row.terminal_ms for row in done if row.terminal_ms), default=None)
    return ok, fail, div, last_ms


def sdk_ribbon(rows: tuple[SdkDispatchRow, ...], *, now_ms: int, window_m: int) -> str:
    ok, fail, div, last_ms = sdk_terminal_tally(rows)
    last = _ms(now_ms - last_ms) if last_ms is not None else "-"
    return f"  ✓{ok} ✗{fail} ⚠{div}div · last {last} ago · window {window_m}m"


def cdp_ribbon(rows: tuple[CdpLegRow, ...], *, now_ms: int, window_m: int) -> str:
    done = [row for row in rows if row.terminal_ms is not None]
    ok = sum(1 for row in done if not row.failure_reason)
    fail = sum(1 for row in done if row.failure_reason)
    proof = sum(1 for row in done if row.proof_present)
    no_proof = sum(1 for row in done if not row.proof_present and not row.failure_reason)
    last_ms = max((row.terminal_ms for row in done if row.terminal_ms), default=None)
    last = _ms(now_ms - last_ms) if last_ms is not None else "-"
    return (
        f"  ✓{ok} (proof {proof}/{len(done)}) ✗{fail}"
        f"{f' ⚠{no_proof}noproof' if no_proof else ''}"
        f" · last {last} ago · window {window_m}m"
    )


def count_by_root(rows: list[SdkDispatchRow] | list[CdpLegRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        rid = row.root_id
        if not rid:
            continue
        counts[rid] = counts.get(rid, 0) + 1
    return counts


def lease_body_lines(health: HealthProjection) -> list[tuple[str, int]]:
    """Return (line, color_pair) rows for the LEASE / QUEUE body."""
    lines: list[tuple[str, int]] = []
    cap = health.wip_capacity if health.wip_capacity is not None else "?"
    lines.append(
        (
            f"  holder={health.lease_holder or '-'}  queue={health.queue_depth}  "
            f"wip={health.wip_in_use}/{cap}  "
            f"dropped={health.events_dropped_ingest}/{health.events_dropped_subscribe}",
            0,
        )
    )
    if health.skipped_by_reason:
        hist = "  ".join(
            f"{reason}={count}" for reason, count in health.skipped_by_reason.items()
        )
        skips = f"  skips: {hist}"
    else:
        skips = "  skips: none"
    mismatch = int(health.skipped_by_reason.get("executor_mismatch", 0) or 0)
    lines.append((skips, 2 if mismatch > 0 else 0))
    if health.degraded:
        lines.append((f"  DEGRADED: {', '.join(health.degraded)}", 1))
    return lines
