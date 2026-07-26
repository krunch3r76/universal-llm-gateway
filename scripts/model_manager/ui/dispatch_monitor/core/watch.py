"""``--watch`` text sink -- pure projection-to-text. No I/O, no sockets.

This is the v1 View, and per the project charter it is a gate, not a toy: *if
``--watch`` is not trustworthy, SDL is not ready.* It consumes exactly what an SDL
View will consume -- decoded projection frames -- so validating it validates the
production path rather than a debug side-channel.

Rendering only. It reads no thresholds, folds nothing, parses no CHECKPOINT, and
queries no bus. Every judgment on screen was made in :mod:`.attention` and arrived
in the frame. That is the standing View invariant, and keeping the reference View
honest is the cheapest way to keep it enforceable.
"""

from __future__ import annotations

from .dtos import CdpLegRow, CharterRootRow, SdkDispatchRow, SupervisorProjection

SEVERITY_MARK = {"crit": "!!", "warn": " !", "info": "  "}


def _ms(value: int | None) -> str:
    """Render a millisecond duration compactly, or ``-`` when unknown."""
    if value is None:
        return "-"
    seconds = value // 1000
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def _truncate(text: str | None, width: int) -> str:
    """Render ``text`` in exactly ``width`` columns, ellipsising when too long."""
    value = text or "-"
    if len(value) <= width:
        return value.ljust(width)
    return value[: width - 1] + "…"


def _root_line(row: CharterRootRow) -> str:
    """Render one charter root row."""
    step = row.arc_g_step or "-"
    return (
        f"  {_truncate(row.root_id, 8)} {_truncate(row.state, 14)} "
        f"g={_truncate(step, 5)} worker={_truncate(row.worker_thread, 8)} "
        f"age={_ms(row.in_flight_age_ms):>7} skips={row.skip_streak:<3} "
        f"{_truncate(row.skip_reason, 28)}"
    )


def _sdk_line(row: SdkDispatchRow) -> str:
    """Render one cursor-sdk dispatch row, flagging GS2 divergence inline."""
    flag = "DIVERGENT" if row.divergent_fields else ",".join(row.emitters_seen) or "-"
    timing = row.duration_ms if row.terminal_ms is not None else row.idle_age_ms
    label = "dur" if row.terminal_ms is not None else "idle"
    return (
        f"  {_truncate(row.dispatch_id, 14)} {_truncate(row.state, 10)} "
        f"root={_truncate(row.root_id, 8)} {_truncate(row.model, 18)} "
        f"{label}={_ms(timing):>7} [{flag}]"
    )


def _cdp_line(row: CdpLegRow) -> str:
    """Render one CDP leg row, marking proof presence."""
    proof = "proof" if row.proof_present else "-"
    timing = row.elapsed_ms if row.terminal_ms is None else None
    label = row.execution_id or row.request_id[:14]
    return (
        f"  {_truncate(label, 14)} {_truncate(row.state, 14)} "
        f"{_truncate(row.model, 18)} elapsed={_ms(timing):>7} "
        f"caller={_truncate(row.caller_agent, 8)} [{proof}]"
    )


def render(projection: SupervisorProjection) -> str:
    """Return the full text frame for ``projection``."""
    health = projection.health
    lines = [
        f"== dispatch supervisor  schema={projection.schema_version} "
        f"fp={projection.fingerprint} t={projection.generated_at_ms} ==",
        f"tick: last_scan={_ms(health.tick_last_scan_age_ms)} ago  "
        f"roots={health.tick_roots_scanned}  admitted={health.tick_admitted_last_scan}"
        f"/{health.tick_admitted_total}  folded={health.records_folded}  "
        f"seq={health.seq_high_water if health.seq_high_water is not None else '-'}",
        f"lease: holder={health.lease_holder or '-'}  queue={health.queue_depth}  "
        f"wip={health.wip_in_use}"
        f"/{health.wip_capacity if health.wip_capacity is not None else '?'}  "
        f"dropped(ingest/sub)={health.events_dropped_ingest}"
        f"/{health.events_dropped_subscribe}",
    ]
    if health.skipped_by_reason:
        histogram = "  ".join(
            f"{reason}={count}" for reason, count in health.skipped_by_reason.items()
        )
        lines.append(f"skips: {histogram}")
    if health.degraded:
        lines.append(f"DEGRADED: {', '.join(health.degraded)}")
    lines.append(f"-- roots ({len(projection.roots)}) --")
    lines.extend(_root_line(row) for row in projection.roots)
    lines.append(f"-- sdk dispatches ({len(projection.sdk)}) --")
    lines.extend(_sdk_line(row) for row in projection.sdk)
    lines.append(f"-- cdp legs ({len(projection.cdp)}) --")
    lines.extend(_cdp_line(row) for row in projection.cdp)
    lines.append(f"-- arcs ({len(projection.arcs)}) -- v1: present-but-empty by design")
    lines.append(f"-- attention ({len(projection.attention)}) --")
    for item in projection.attention:
        mark = SEVERITY_MARK.get(item.severity, "  ")
        lines.append(f"  {mark} [{item.kind}] {item.subject}: {item.title}")
        if item.detail:
            lines.append(f"       {item.detail}")
    if projection.changed_hints:
        lines.append(f"hints (advisory): {', '.join(projection.changed_hints)}")
    return "\n".join(lines)
