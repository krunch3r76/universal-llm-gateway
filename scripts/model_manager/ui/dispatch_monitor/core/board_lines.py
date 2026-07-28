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
    SdkDispatchRow,
)
from .watch import SEVERITY_MARK, _ms, _truncate

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


def attention_line(item: AttentionItem, width: int) -> str:
    mark = SEVERITY_MARK.get(item.severity, "  ")
    body = f"{mark} [{item.kind}] {item.subject}: {item.title}"
    if item.detail and len(body) < width - 8:
        body = f"{body} — {item.detail[: width - len(body) - 3]}"
    return _truncate(body, width)


def live_sdk(rows: tuple[SdkDispatchRow, ...]) -> list[SdkDispatchRow]:
    live = [row for row in rows if row.terminal_ms is None]
    live.sort(
        key=lambda row: (
            0 if row.divergent_fields else 1,
            0 if row.queue_position is None else 1,
            -(row.idle_age_ms or 0),
        )
    )
    return live


def live_cdp(rows: tuple[CdpLegRow, ...]) -> list[CdpLegRow]:
    live = [row for row in rows if row.terminal_ms is None]
    live.sort(key=lambda row: -(row.elapsed_ms or 0))
    return live


#: Still owed work — enqueued / in flight / waiting operator or skip decision.
_ROOT_ACTIVE_STATES = frozenset(
    {"in_flight", "waiting_open", "skipped", "parked", "unknown"}
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
    ages: list[int] = []
    for row in rows:
        if isinstance(row, SdkDispatchRow) and row.idle_age_ms is not None:
            ages.append(row.idle_age_ms)
        elif isinstance(row, CdpLegRow) and row.elapsed_ms is not None:
            ages.append(row.elapsed_ms)
    return max(ages) if ages else None


def sdk_live_line(row: SdkDispatchRow) -> str:
    flag = "DIVERGENT" if row.divergent_fields else ",".join(row.emitters_seen) or "-"
    if row.queue_position is not None:
        timing = f"q{row.queue_position}"
    else:
        timing = f"idle={_ms(row.idle_age_ms)}"
    stall = f" stall={row.stall_stage}" if row.stall_stage else ""
    if row.last_tool_name:
        tool = row.last_tool_name
        if row.last_tool_status and row.last_tool_status != "completed":
            tool = f"{tool}:{row.last_tool_status}"
        tool_col = f" tool={_truncate(tool, 14)}"
    else:
        tool_col = ""
    return (
        f"  {_truncate(row.dispatch_id, 14)} {_truncate(row.state, 10)} "
        f"root={_truncate(row.root_id, 8)} {_truncate(row.model, 18)} "
        f"{timing:<12}{tool_col} [{flag}]{stall}"
    )


def root_line_live(row: CharterRootRow, *, sdk_n: int, cdp_n: int) -> str:
    step = row.arc_g_step or "-"
    return (
        f"  {_truncate(row.root_id, 8)} {_truncate(row.state, 14)} "
        f"g={_truncate(step, 5)} worker={_truncate(row.worker_thread, 8)} "
        f"age={_ms(row.in_flight_age_ms):>7} skips={row.skip_streak:<3} "
        f"sdk={sdk_n} cdp={cdp_n} {_truncate(row.skip_reason, 20)}"
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
