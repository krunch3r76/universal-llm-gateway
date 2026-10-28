"""Pure decision logic for the Grok Bot box MCP egress watch loop."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

_TUNNEL_STALE_S = 60.0
_LOADER_STUCK_AFTER_TOOLS_LIST_S = 180.0
_LOADER_DESKTOP_BACKSTOP_S = 600.0
_DESKTOP_RESTART_CAP_S = 1800.0
# Consecutive watch ticks with hm1_tools_call_ok=false before recovery (30s interval).
_HM1_FALSE_RECOVERY_CONSECUTIVE_N = 3
_DEFERRAL_CEILING_S = 600.0
_CONNECTED_UNDICI_GAP_WARN_S = 1200.0

TURN_INDEX_DUE = 25
GC1_STEP_INDEX_DUE = 180
HOUSE_STEP_INDEX_DUE = 160
HOP_COOLDOWN_S = 1800.0
HOP_DEFERRAL_CEILING_S = 600.0
HOP_STALE_WINDOW_MS = 6 * 60 * 60 * 1000
GC1_ROSTER_NAME = "Stage Manager (gc1)"
HOP_THREAD_ID = "15851"
HOP_SUBJECT_HEAD = "NUDGE|context-hop-due|"
_HOP_NAME_PREFIXES = ("Impresario ", "Reviewer R1 ", "Operator Liaison ")
_LAST_MESSAGE_RE = re.compile(r"^t(\d+)s(\d+)$")

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


def watch_connection_state(log_text: str, *, now_ms: int) -> tuple[bool, float | None]:
    """Return ``(connected, disconnected_for_s)``.

    ``disconnected_for_s`` is set when the latest watch state is not connected.
    """
    watch_lines = [ln for ln in log_text.splitlines() if _WATCH_PREFIX in ln]
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
    event_rows_trusted: bool = True


@dataclass
class GrokbotEgressLoopState:
    last_desktop_restart_at: float | None = None
    tunnel_stale_emitted_at: float | None = None
    loader_stuck_emitted_at: float | None = None
    loader_stuck_first_at: float | None = None
    deferral_started_at: float | None = None
    hm1_false_consecutive: int = 0
    hm1_false_sustained_since: float | None = None
    hm1_recovery_chain_ran: bool = False


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
    run_hm1_recovery_chain: bool = False


def _restart_cap_blocks(now: float, state: GrokbotEgressLoopState) -> bool:
    if state.last_desktop_restart_at is None:
        return False
    return (now - state.last_desktop_restart_at) < _DESKTOP_RESTART_CAP_S


def _should_defer_restart(snapshot: GrokbotEgressSnapshot) -> bool:
    return snapshot.in_flight_gc1_mcp or snapshot.open_gc1_bus_turn


def roster_name_allowed(name: str) -> bool:
    """Exact Stage Manager (gc1), or a house prefix; a bl1 name is excluded."""
    if "bl1" in name:
        return False
    if name == GC1_ROSTER_NAME:
        return True
    return name.startswith(_HOP_NAME_PREFIXES)


def parse_last_message_id(last_message_id: str) -> tuple[int, int] | None:
    """Parse ``t<N>s<M>`` into ``(turn_index, step_index)``."""
    match = _LAST_MESSAGE_RE.fullmatch(last_message_id)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def step_index_due_threshold(name: str) -> int:
    """gc1 step threshold is 180; every other allowed name uses 160."""
    if name == GC1_ROSTER_NAME:
        return GC1_STEP_INDEX_DUE
    return HOUSE_STEP_INDEX_DUE


def context_row_due(row: dict[str, object], *, now_ms: int) -> bool:
    """True when an allowlisted, fresh, non-retired row meets turn or step."""
    name = row.get("name")
    if not isinstance(name, str) or not roster_name_allowed(name):
        return False
    if "(retired)" in name:
        return False
    updated = row.get("updated_at_ms")
    if not isinstance(updated, int):
        return False
    if now_ms - updated > HOP_STALE_WINDOW_MS:
        return False
    raw_id = row.get("lastMessageId")
    if not isinstance(raw_id, str):
        return False
    parsed = parse_last_message_id(raw_id)
    if parsed is None:
        return False
    turn_index, step_index = parsed
    if turn_index >= TURN_INDEX_DUE:
        return True
    return step_index >= step_index_due_threshold(name)


def hop_subject(names: list[str]) -> str:
    """One combined ``NUDGE|context-hop-due|`` subject, names lexicographic."""
    ordered = sorted(names)
    return HOP_SUBJECT_HEAD + ",".join(ordered)


@dataclass
class ContextHopState:
    """In-memory cooldown and first-due stamps. Not read from the bus."""

    cooldown_until: dict[str, float]
    first_due_at: dict[str, float]

    def __init__(self) -> None:
        self.cooldown_until = {}
        self.first_due_at = {}


@dataclass
class ContextHopNotice:
    """One notice owed this tick."""

    names: list[str]
    agent_ids: list[str]
    subject: str


def _hop_busy(
    *,
    open_gc1_bus_turn: bool,
    in_flight_gc1_mcp: bool,
    is_running: bool,
) -> bool:
    return open_gc1_bus_turn or in_flight_gc1_mcp or is_running


def decide_context_hop(
    *,
    rows: list[dict[str, object]],
    now: float,
    now_ms: int,
    state: ContextHopState,
    open_gc1_bus_turn: bool,
    in_flight_gc1_mcp: bool,
) -> ContextHopNotice | None:
    """Pick due agents that should be named on one notice this tick."""
    names: list[str] = []
    agent_ids: list[str] = []
    for row in rows:
        if not context_row_due(row, now_ms=now_ms):
            continue
        agent_id = row.get("agent_id")
        name = row.get("name")
        if not isinstance(agent_id, str) or not isinstance(name, str):
            continue
        until = state.cooldown_until.get(agent_id)
        if until is not None and now < until:
            continue
        if state.first_due_at.get(agent_id) is None:
            state.first_due_at[agent_id] = now
        is_running = bool(row.get("is_running"))
        elapsed = now - state.first_due_at[agent_id]
        busy = _hop_busy(
            open_gc1_bus_turn=open_gc1_bus_turn,
            in_flight_gc1_mcp=in_flight_gc1_mcp,
            is_running=is_running,
        )
        if busy and elapsed < HOP_DEFERRAL_CEILING_S:
            continue
        names.append(name)
        agent_ids.append(agent_id)
    if not names:
        return None
    return ContextHopNotice(
        names=sorted(names),
        agent_ids=agent_ids,
        subject=hop_subject(names),
    )


def record_hop_notified(
    state: ContextHopState, agent_ids: list[str], *, now: float
) -> None:
    """Start the 1800s cooldown after a successful reply."""
    for agent_id in agent_ids:
        state.cooldown_until[agent_id] = now + HOP_COOLDOWN_S
        state.first_due_at.pop(agent_id, None)


def rebuild_hop_state(entries: list[dict[str, object]]) -> ContextHopState:
    """Restore cooldown_until from a journal; first_due_at stays empty."""
    state = ContextHopState()
    for entry in entries:
        agent_id = entry.get("agent_id")
        until = entry.get("cooldown_until")
        if isinstance(agent_id, str) and isinstance(until, (int, float)):
            state.cooldown_until[agent_id] = float(until)
    return state


def _deferral_ceiling_hit(now: float, state: GrokbotEgressLoopState) -> bool:
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
        state.hm1_false_consecutive = 0
        state.hm1_false_sustained_since = None
        state.hm1_recovery_chain_ran = False
        return decision

    if snapshot.health_http_status is not None and snapshot.health_http_status != 200:
        decision.clear_tunnel_episode = True
        decision.clear_loader_episode = True
        state.tunnel_stale_emitted_at = None
        state.loader_stuck_emitted_at = None
        state.loader_stuck_first_at = None
        state.deferral_started_at = None
        state.hm1_false_consecutive = 0
        state.hm1_false_sustained_since = None
        state.hm1_recovery_chain_ran = False
        return decision

    if (
        snapshot.watch_connected
        and snapshot.undici_idle_s is not None
        and snapshot.undici_idle_s >= _CONNECTED_UNDICI_GAP_WARN_S
    ):
        decision.warn_undici_gap = True

    if not snapshot.event_rows_trusted:
        state.hm1_false_consecutive = 0
        state.hm1_false_sustained_since = None
        state.hm1_recovery_chain_ran = False
    elif snapshot.hm1_tools_call_ok:
        state.hm1_false_consecutive = 0
        state.hm1_false_sustained_since = None
        state.hm1_recovery_chain_ran = False
    else:
        state.hm1_false_consecutive += 1
        if state.hm1_false_consecutive >= _HM1_FALSE_RECOVERY_CONSECUTIVE_N:
            if state.hm1_false_sustained_since is None:
                state.hm1_false_sustained_since = now
            if not state.hm1_recovery_chain_ran:
                decision.run_hm1_recovery_chain = True
                state.hm1_recovery_chain_ran = True

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
    hm1_sustained_age = (
        (now - state.hm1_false_sustained_since)
        if state.hm1_false_sustained_since is not None
        else 0.0
    )
    wants_hm1_backstop = (
        snapshot.event_rows_trusted
        and state.hm1_false_consecutive >= _HM1_FALSE_RECOVERY_CONSECUTIVE_N
        and hm1_sustained_age >= _LOADER_DESKTOP_BACKSTOP_S
    )
    wants_restart = wants_tunnel_restart or wants_loader_backstop or wants_hm1_backstop

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
    "ContextHopNotice",
    "ContextHopState",
    "GC1_ROSTER_NAME",
    "GC1_STEP_INDEX_DUE",
    "GrokbotEgressLoopState",
    "GrokbotEgressSnapshot",
    "GrokbotTickDecision",
    "HOP_COOLDOWN_S",
    "HOP_DEFERRAL_CEILING_S",
    "HOP_STALE_WINDOW_MS",
    "HOP_THREAD_ID",
    "HOUSE_STEP_INDEX_DUE",
    "TURN_INDEX_DUE",
    "context_row_due",
    "decide_context_hop",
    "decide_grokbot_tick",
    "hop_subject",
    "parse_last_message_id",
    "parse_watch_log_line_ts_ms",
    "record_hop_notified",
    "rebuild_hop_state",
    "roster_name_allowed",
    "step_index_due_threshold",
    "watch_connection_state",
    "_HM1_FALSE_RECOVERY_CONSECUTIVE_N",
    "_LOADER_DESKTOP_BACKSTOP_S",
    "_LOADER_STUCK_AFTER_TOOLS_LIST_S",
    "_TUNNEL_STALE_S",
]
