"""Pure decision logic for the Grok Bot box MCP egress watch loop."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

_TUNNEL_STALE_S = 60.0
_LOADER_STUCK_AFTER_TOOLS_LIST_S = 180.0
_LOADER_DESKTOP_BACKSTOP_S = 600.0
_DESKTOP_RESTART_CAP_S = 1800.0
_DEFERRAL_CEILING_S = 600.0
_CONNECTED_UNDICI_GAP_WARN_S = 1200.0

_WATCH_PREFIX = "[user-computer-watch]"
_TS_RE = re.compile(
    r"\[(?:user-computer-watch|local-exec-daemon|sand-local-exec-daemon)\]\s+"
    r"(\d{4}-\d{2}-\d{2}T[\d:.]+Z)"
)


def parse_watch_log_line_ts_ms(line: str) -> int | None:
    """Return epoch ms from a daemon log line, or None when absent."""
    match = _TS_RE.search(line)
    if match is None:
        return None
    try:
        dt = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        try:
            dt = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
        except ValueError:
            return None
    return int(dt.timestamp() * 1000)


def watch_connection_state(
    log_text: str, *, now_ms: int
) -> tuple[bool, float | None]:
    """Return ``(connected, disconnected_for_s)``.

    ``disconnected_for_s`` is set when the latest watch state is not connected.
    """
    watch_lines = [
        ln for ln in log_text.splitlines() if _WATCH_PREFIX in ln
    ]
    if not watch_lines:
        return False, None

    last = watch_lines[-1]
    if " connected after" in last:
        return True, None

    disconnected_ms: int | None = None
    for line in reversed(watch_lines):
        ts = parse_watch_log_line_ts_ms(line)
        if ts is None:
            continue
        if " connected after" in line:
            return False, max(0.0, (now_ms - ts) / 1000.0)
        if "dialing" in line or " ended " in line or " closed " in line:
            disconnected_ms = ts
            break
    if disconnected_ms is None:
        return False, None
    return False, max(0.0, (now_ms - disconnected_ms) / 1000.0)


@dataclass
class GrokbotEgressSnapshot:
    """Inputs for one watch tick (all fields supplied by the probe layer)."""

    desktop_running: bool
    desktop_pid: int | None
    watch_connected: bool
    watch_disconnected_s: float | None
    daemon_log_readable: bool
    health_http_status: int | None
    tunnel_stale: bool
    loader_stuck: bool
    loader_stuck_s: float | None
    hm1_tools_call_ok: bool
    undici_idle_s: float | None
    in_flight_gc1_mcp: bool
    open_gc1_bus_turn: bool


@dataclass
class GrokbotEgressLoopState:
    last_desktop_restart_at: float | None = None
    tunnel_stale_emitted_at: float | None = None
    loader_stuck_emitted_at: float | None = None
    loader_stuck_first_at: float | None = None
    deferral_started_at: float | None = None


@dataclass
class GrokbotTickDecision:
    emit_probe_failed: bool = False
    probe_error_class: str = ""
    probe_detail: str = ""
    emit_tunnel_stale: bool = False
    emit_loader_stuck: bool = False
    restart_desktop: bool = False
    warn_undici_gap: bool = False
    clear_tunnel_episode: bool = False
    clear_loader_episode: bool = False


def _restart_cap_blocks(now: float, state: GrokbotEgressLoopState) -> bool:
    if state.last_desktop_restart_at is None:
        return False
    return (now - state.last_desktop_restart_at) < _DESKTOP_RESTART_CAP_S


def _should_defer_restart(snapshot: GrokbotEgressSnapshot) -> bool:
    return snapshot.in_flight_gc1_mcp or snapshot.open_gc1_bus_turn


def _deferral_ceiling_hit(
    now: float, state: GrokbotEgressLoopState
) -> bool:
    if state.deferral_started_at is None:
        return False
    return (now - state.deferral_started_at) >= _DEFERRAL_CEILING_S


def decide_grokbot_tick(
    *,
    now: float,
    state: GrokbotEgressLoopState,
    snapshot: GrokbotEgressSnapshot,
) -> GrokbotTickDecision:
    """Map probe inputs to events and at most one desktop restart this tick."""
    decision = GrokbotTickDecision()

    if not snapshot.daemon_log_readable:
        decision.emit_probe_failed = True
        decision.probe_error_class = "unreadable_log"
        decision.probe_detail = "local-exec-daemon.log not readable"
        return decision

    if snapshot.health_http_status is not None and snapshot.health_http_status != 200:
        decision.clear_tunnel_episode = True
        decision.clear_loader_episode = True
        state.tunnel_stale_emitted_at = None
        state.loader_stuck_emitted_at = None
        state.loader_stuck_first_at = None
        state.deferral_started_at = None
        return decision

    if (
        snapshot.watch_connected
        and snapshot.undici_idle_s is not None
        and snapshot.undici_idle_s >= _CONNECTED_UNDICI_GAP_WARN_S
    ):
        decision.warn_undici_gap = True

    if snapshot.tunnel_stale:
        if state.tunnel_stale_emitted_at is None:
            decision.emit_tunnel_stale = True
            state.tunnel_stale_emitted_at = now
    else:
        decision.clear_tunnel_episode = True
        state.tunnel_stale_emitted_at = None

    if snapshot.loader_stuck and snapshot.hm1_tools_call_ok:
        if state.loader_stuck_first_at is None:
            state.loader_stuck_first_at = now
        if state.loader_stuck_emitted_at is None:
            decision.emit_loader_stuck = True
            state.loader_stuck_emitted_at = now
    else:
        decision.clear_loader_episode = True
        state.loader_stuck_emitted_at = None
        state.loader_stuck_first_at = None

    wants_tunnel_restart = (
        snapshot.tunnel_stale
        and snapshot.desktop_running
        and (snapshot.watch_disconnected_s or 0.0) >= _TUNNEL_STALE_S
    )
    loader_age = (
        (now - state.loader_stuck_first_at)
        if state.loader_stuck_first_at is not None
        else 0.0
    )
    wants_loader_backstop = (
        snapshot.loader_stuck
        and snapshot.hm1_tools_call_ok
        and loader_age >= _LOADER_DESKTOP_BACKSTOP_S
    )
    wants_restart = wants_tunnel_restart or wants_loader_backstop

    if not wants_restart:
        state.deferral_started_at = None
        return decision

    if _restart_cap_blocks(now, state):
        return decision

    if _should_defer_restart(snapshot):
        if state.deferral_started_at is None:
            state.deferral_started_at = now
        if not _deferral_ceiling_hit(now, state):
            return decision

    decision.restart_desktop = True
    state.last_desktop_restart_at = now
    state.deferral_started_at = None
    return decision


__all__ = [
    "GrokbotEgressLoopState",
    "GrokbotEgressSnapshot",
    "GrokbotTickDecision",
    "decide_grokbot_tick",
    "parse_watch_log_line_ts_ms",
    "watch_connection_state",
    "_LOADER_DESKTOP_BACKSTOP_S",
    "_LOADER_STUCK_AFTER_TOOLS_LIST_S",
    "_TUNNEL_STALE_S",
]
