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


def clip_text(text: str | None, width: int) -> str:
    """Truncate ``text`` to ``width`` without padding — for optional suffixes."""
    value = text or ""
    if len(value) <= width:
        return value
    if width <= 1:
        return "…"[:width]
    return value[: width - 1] + "…"


def _root_line(row: CharterRootRow) -> str:
    """Render one charter root row."""
    step = row.arc_g_step or row.pickup_gid or "-"
    if row.objective:
        tail = f" obj: {_truncate(row.objective, 48)}"
    elif row.bus_summary or row.bus_slug:
        identity = row.bus_summary or row.bus_slug or ""
        tail = f" bus: {_truncate(identity, 48)}"
    elif row.skip_reason:
        tail = f" {_truncate(row.skip_reason, 28)}"
    else:
        tail = ""
    return (
        f"  {_truncate(row.root_id, 8)} {_truncate(row.state, 14)} "
        f"g={_truncate(step, 5)} worker={_truncate(row.worker_thread, 8)} "
        f"age={_ms(row.in_flight_age_ms):>7} skips={row.skip_streak:<3} "
        f"{tail}"
    )


def _sdk_line(row: SdkDispatchRow) -> str:
    """Render one cursor-sdk dispatch row, flagging GS2 divergence inline."""
    flag = "DIVERGENT" if row.divergent_fields else ",".join(row.emitters_seen) or "-"
    if row.terminal_ms is not None:
        timing = f"dur={_ms(row.duration_ms):>7}"
    else:
        timing = f"el={_ms(row.elapsed_ms):>7} idle={_ms(row.idle_age_ms)}"
    prov = f"from={row.caller_from or 'ide'} via={row.caller_via or 'http'}"
    topic_s = f" topic={clip_text(row.topic, 40)}" if row.topic else ""
    return (
        f"  {_truncate(row.dispatch_id, 14)} {_truncate(row.state, 10)} "
        f"root={_truncate(row.root_id, 8)} {_truncate(row.model, 18)} "
        f"{timing} {prov} [{flag}]{topic_s}"
    )


def cdp_id_legend() -> str:
    """One-line CDP identity legend. Does not name CSE or checkout lanes."""
    return (
        "  ids: req=cdp.generate request_id (fold key) · "
        "exec=execution_id · th=agent-bus thread"
    )


def _cdp_line(row: CdpLegRow, *, width: int | None = None) -> str:
    """Render one CDP leg with labeled ids so ATTENTION ``request_id`` can join.

    Identity tokens are unpadded. ``exec=`` / ``th=`` omit when unknown.
    Width drops trailing status tokens; it does not invent CSE/checkout lanes.
    """
    proof = "proof" if row.proof_present else "-"
    timing = row.elapsed_ms if row.terminal_ms is None else None
    parts = [f"req={row.request_id}"]
    if row.execution_id:
        parts.append(f"exec={row.execution_id}")
    if row.thread_id:
        parts.append(f"th={row.thread_id}")
    base = "  " + " ".join(parts)
    extras = [
        f" {row.state}",
        f" {row.model or '-'}",
        f" elapsed={_ms(timing)}",
        f" caller={row.caller_agent or '-'}",
        f" [{proof}]",
    ]
    if row.topic:
        extras.append(f" topic={clip_text(row.topic, 28)}")
    for token in extras:
        if width is None or len(base) + len(token) <= width:
            base += token
        elif width is not None:
            break
    if width is not None and len(base) > width:
        return clip_text(base, width)
    return base


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
        f"lease: holder={health.lease_holder or '-'}  th={health.lease_thread_id or '-'}  "
        f"model={health.lease_model or '-'}  hb={_ms(health.lease_heartbeat_age_ms)}  "
        f"queue={health.queue_depth}  "
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
    if projection.cdp:
        lines.append(cdp_id_legend())
    lines.extend(_cdp_line(row) for row in projection.cdp)
    lines.append(f"-- relations ({len(projection.relations)}) --")
    for edge in projection.relations:
        lines.append(
            f"  {edge.kind} {edge.from_id} → {edge.to_id} ({edge.evidence_signal})"
        )
    lines.append(f"-- arcs ({len(projection.arcs)}) -- v1: present-but-empty by design")
    lines.append(f"-- attention ({len(projection.attention)}) --")
    now_ms = projection.generated_at_ms
    for item in projection.attention:
        mark = SEVERITY_MARK.get(item.severity, "  ")
        elapsed = item.age_ms
        if elapsed is None and item.since_ms is not None:
            elapsed = max(0, now_ms - item.since_ms)
        age = f"{_ms(elapsed):>7}"
        lines.append(f"  {mark} {age} [{item.kind}] {item.subject}: {item.title}")
        if item.detail:
            lines.append(f"       {item.detail}")
    if projection.changed_hints:
        lines.append(f"hints (advisory): {', '.join(projection.changed_hints)}")
    return "\n".join(lines)
