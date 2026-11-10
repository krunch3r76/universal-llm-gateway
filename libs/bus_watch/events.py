"""Liaison bus_watch lifecycle events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event, event_factory

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None


def register_uds_publisher(publisher: Callable[[str, dict[str, Any]], None]) -> None:
    """Install the process-local publisher used when mcp_events cannot be imported."""
    global _uds_publisher
    _uds_publisher = publisher


try:
    from mcp_events import record
except ImportError:

    def record(signal: str, **payload: Any) -> None:  # type: ignore[misc]
        if _uds_publisher is None:
            return
        _uds_publisher(signal, dict(payload))


def _emit(event: Event) -> None:
    record(event.signal, **event.payload)


@event_factory
def LiaisonNightIdReset(  # noqa: N802
    *,
    root_id: str,
    previous_night_id: str,
    new_night_id: str,
    previous_hops: int,
) -> Event:
    return Event(
        signal="liaison.night_id.reset",
        payload={
            "root_id": root_id,
            "previous_night_id": previous_night_id,
            "new_night_id": new_night_id,
            "previous_hops": previous_hops,
        },
        scope="global",
    )


@event_factory
def LiaisonLeaseForfeited(  # noqa: N802
    *,
    root_id: str,
    holder: str,
    expires_at: str,
    last_holder_turn: int | None,
) -> Event:
    return Event(
        signal="liaison.lease.forfeited",
        payload={
            "root_id": root_id,
            "holder": holder,
            "expires_at": expires_at,
            "last_holder_turn": last_holder_turn,
        },
        scope="global",
    )


def emit_night_id_reset(**kwargs: Any) -> None:
    """Publish a liaison night-id reset after the hop counter rolls."""
    _emit(LiaisonNightIdReset(**kwargs))


def emit_lease_forfeited(**kwargs: Any) -> None:
    """Publish a liaison lease forfeiture for the named root holder."""
    _emit(LiaisonLeaseForfeited(**kwargs))


@event_factory
def LiaisonPendingSpawnReleased(  # noqa: N802
    *,
    root_id: str,
    execution_id: str,
    thread_id: str,
    reason: str,
    spawned_at: str | None = None,
) -> Event:
    return Event(
        signal="liaison.pending_spawn.released",
        payload={
            "root_id": root_id,
            "execution_id": execution_id,
            "thread_id": thread_id,
            "reason": reason,
            "spawned_at": spawned_at,
        },
        scope="global",
    )


def emit_pending_spawn_released(**kwargs: Any) -> None:
    """Publish release of a pending liaison spawn that will not be admitted."""
    _emit(LiaisonPendingSpawnReleased(**kwargs))


@event_factory
def LiaisonCheckpointObserved(  # noqa: N802
    *,
    root: str,
    turn: int,
    prior_turn: int,
    source: str,
) -> Event:
    return Event(
        signal="liaison.checkpoint.observed",
        payload={
            "root": root,
            "turn": turn,
            "prior_turn": prior_turn,
            "source": source,
        },
        scope="global",
    )


def emit_checkpoint_observed(**kwargs: Any) -> None:
    """Publish that a liaison checkpoint turn was observed on a root."""
    _emit(LiaisonCheckpointObserved(**kwargs))


@event_factory
def LiaisonLaneCloseoutObserved(  # noqa: N802
    *,
    root: str,
    lane: str,
    terminal_status: str,
    abandoned: bool,
    turn: int | None,
) -> Event:
    return Event(
        signal="liaison.lane_closeout.emitted",
        payload={
            "root": root,
            "lane": lane,
            "terminal_status": terminal_status,
            "abandoned": abandoned,
            "turn": turn,
        },
        scope="global",
    )


def emit_lane_closeout_observed(**kwargs: Any) -> None:
    """Publish that a lane closeout turn was observed for liaison tracking."""
    _emit(LiaisonLaneCloseoutObserved(**kwargs))


@event_factory
def LiaisonNowRowBound(  # noqa: N802
    *,
    root_id: str,
    row: str,
    tier: str,
    lane_id: str,
    superseded: str | None,
    as_of: str,
) -> Event:
    return Event(
        signal="liaison.now_row.bound",
        payload={
            "root_id": root_id,
            "row": row,
            "tier": tier,
            "lane_id": lane_id,
            "superseded": superseded,
            "as_of": as_of,
        },
        scope="global",
    )


@event_factory
def LiaisonNowRowReleased(  # noqa: N802
    *,
    root_id: str,
    row: str,
    lane_id: str,
    reason: str,
    as_of: str,
) -> Event:
    return Event(
        signal="liaison.now_row.released",
        payload={
            "root_id": root_id,
            "row": row,
            "lane_id": lane_id,
            "reason": reason,
            "as_of": as_of,
        },
        scope="global",
    )


def emit_now_row_bound(**kwargs: Any) -> None:
    """Publish that a liaison now-row was bound to a lane."""
    _emit(LiaisonNowRowBound(**kwargs))


def emit_now_row_released(**kwargs: Any) -> None:
    """Publish that a liaison now-row binding was released from its lane."""
    _emit(LiaisonNowRowReleased(**kwargs))


@event_factory
def LiaisonRosterHireLatched(  # noqa: N802
    *,
    row_id: str,
    work_key: str,
    dispatch_id: str,
    readmit_count: int,
    readmit_from: str | None = None,
) -> Event:
    return Event(
        signal="liaison.roster.hire.latched",
        payload={
            "row_id": row_id,
            "work_key": work_key,
            "dispatch_id": dispatch_id,
            "readmit_count": readmit_count,
            "readmit_from": readmit_from,
        },
        scope="global",
    )


@event_factory
def LiaisonRosterHireReleased(  # noqa: N802
    *,
    row_id: str,
    dispatch_id: str,
    reason: str,
) -> Event:
    return Event(
        signal="liaison.roster.hire.released",
        payload={
            "row_id": row_id,
            "dispatch_id": dispatch_id,
            "reason": reason,
        },
        scope="global",
    )


@event_factory
def LiaisonRosterHireRefused(  # noqa: N802
    *,
    row_id: str,
    work_key: str,
    reason: str,
) -> Event:
    return Event(
        signal="liaison.roster.hire.refused",
        payload={
            "row_id": row_id,
            "work_key": work_key,
            "reason": reason,
        },
        scope="global",
    )


def emit_roster_hire_latched(**kwargs: Any) -> None:
    """Publish that a liaison roster hire latched onto a work key."""
    _emit(LiaisonRosterHireLatched(**kwargs))


def emit_roster_hire_released(**kwargs: Any) -> None:
    """Publish that a latched liaison roster hire was released from the roster."""
    _emit(LiaisonRosterHireReleased(**kwargs))


def emit_roster_hire_refused(**kwargs: Any) -> None:
    """Publish that a liaison roster hire was refused before latch."""
    _emit(LiaisonRosterHireRefused(**kwargs))


@event_factory
def BusWatchMultiplexRefused(  # noqa: N802
    *,
    root_id: str,
    occupied_mission: str,
    requested_row: str,
) -> Event:
    return Event(
        signal="bus.watch.multiplex.refused",
        payload={
            "root_id": root_id,
            "occupied_mission": occupied_mission,
            "requested_row": requested_row,
        },
        scope="global",
    )


def emit_multiplex_refused(**kwargs: Any) -> None:
    """Publish that a bus-watch multiplex request was refused for a busy root."""
    _emit(BusWatchMultiplexRefused(**kwargs))


@event_factory
def EndpointWakeAddressRoute(  # noqa: N802
    *,
    thread: str,
    turn_id: int | None,
    match: str,
    reason: str,
    rule_id: str | None = None,
    subject_preview: str = "",
    registration_id: str = "",
) -> Event:
    """Observation: a closeout wake matched the lane holder, the alias name, or neither."""
    return Event(
        signal="endpoint_wake.address_route",
        role="observation",
        payload={
            "thread": thread,
            "turn_id": turn_id,
            "match": match,
            "reason": reason,
            "rule_id": rule_id,
            "subject_preview": subject_preview,
            "registration_id": registration_id,
        },
        scope="global",
    )


def emit_endpoint_wake_address_route(**kwargs: Any) -> None:
    """Publish the address-route observation for a closeout or milestone wake decision."""
    _emit(EndpointWakeAddressRoute(**kwargs))


@event_factory
def EndpointWakeCseHolderResolution(  # noqa: N802
    *,
    thread: str,
    subscription_id: int | None,
    chosen_registration: str,
    chosen_source: str,
    bus_registration: str | None = None,
    giw_by_thread_registration: str | None = None,
    giw_by_thread_error: str | None = None,
) -> Event:
    """Observation: holder pick for a thread-scoped CSE wake when ``chosen_source`` is
    ``bus_association``, ``giw_by_thread``, or ``subscription_agent_id`` after bus/GIW consult."""
    return Event(
        signal="endpoint_wake.cse_holder_resolution",
        role="observation",
        payload={
            "thread": thread,
            "subscription_id": subscription_id,
            "chosen_registration": chosen_registration,
            "chosen_source": chosen_source,
            "bus_registration": bus_registration,
            "giw_by_thread_registration": giw_by_thread_registration,
            "giw_by_thread_error": giw_by_thread_error,
        },
        scope="global",
    )


def emit_endpoint_wake_cse_holder_resolution(**kwargs: Any) -> None:
    """Publish how a thread-scoped CSE wake resolved its delivery holder."""
    _emit(EndpointWakeCseHolderResolution(**kwargs))


@event_factory
def EndpointWakeSuppressed(  # noqa: N802
    *,
    thread_id: str,
    turn_id: int | None,
    to_agent: str,
    rule_id: str,
    subject_head: str = "",
    stale_catchup_count: int | None = None,
    min_turn_id: int | None = None,
    max_turn_id: int | None = None,
) -> Event:
    """Observation: a turn on a subscribed thread was suppressed by wake qualification."""
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "to_agent": to_agent,
        "rule_id": rule_id,
        "subject_head": subject_head,
    }
    if stale_catchup_count is not None:
        payload["stale_catchup_count"] = stale_catchup_count
    if min_turn_id is not None:
        payload["min_turn_id"] = min_turn_id
    if max_turn_id is not None:
        payload["max_turn_id"] = max_turn_id
    return Event(
        signal="endpoint_wake.suppressed",
        role="observation",
        payload=payload,
        scope="global",
    )


def emit_endpoint_wake_suppressed(**kwargs: Any) -> None:
    """Publish that endpoint wake qualification suppressed a turn on a subscribed thread."""
    _emit(EndpointWakeSuppressed(**kwargs))


@event_factory
def EndpointWakePassFailed(  # noqa: N802
    *,
    error_class: str,
    error_message: str,
    stage: str = "",
) -> Event:
    """Observation: one endpoint_wake_dispatcher outer pass aborted before completion."""
    return Event(
        signal="endpoint_wake.pass_failed",
        role="observation",
        payload={
            "error_class": error_class,
            "error_message": error_message,
            "stage": stage,
        },
        scope="global",
    )


def emit_endpoint_wake_pass_failed(**kwargs: Any) -> None:
    """Publish that an endpoint wake dispatcher pass failed."""
    _emit(EndpointWakePassFailed(**kwargs))


@event_factory
def EndpointWakePassDuration(  # noqa: N802
    *,
    total_pass_ms: float,
    normalize_legacy_pending_wakes_ms: float,
    escalate_composer_deferred_on_sweep_ms: float,
    expire_overdue_hops_ms: float,
    scan_once_ms: float,
    fire_due_wakes_ms: float,
    finalize_composer_defer_pass_ms: float,
    check_cursor_ide_context_hops_ms: float,
    run_patrol_pass_ms: float,
    display_post_count: int,
    display_post_cumulative_ms: float,
    fire_due_wakes_iteration_count: int,
) -> Event:
    """Observation: timing for one completed endpoint_wake_dispatcher outer pass."""
    return Event(
        signal="endpoint_wake.pass_duration",
        role="observation",
        payload={
            "total_pass_ms": total_pass_ms,
            "normalize_legacy_pending_wakes_ms": normalize_legacy_pending_wakes_ms,
            "escalate_composer_deferred_on_sweep_ms": (
                escalate_composer_deferred_on_sweep_ms
            ),
            "expire_overdue_hops_ms": expire_overdue_hops_ms,
            "scan_once_ms": scan_once_ms,
            "fire_due_wakes_ms": fire_due_wakes_ms,
            "finalize_composer_defer_pass_ms": finalize_composer_defer_pass_ms,
            "check_cursor_ide_context_hops_ms": check_cursor_ide_context_hops_ms,
            "run_patrol_pass_ms": run_patrol_pass_ms,
            "display_post_count": display_post_count,
            "display_post_cumulative_ms": display_post_cumulative_ms,
            "fire_due_wakes_iteration_count": fire_due_wakes_iteration_count,
        },
        scope="global",
    )


def emit_endpoint_wake_pass_duration(**kwargs: Any) -> None:
    """Publish per-pass duration breakdown for endpoint_wake_dispatcher."""
    _emit(EndpointWakePassDuration(**kwargs))


@event_factory
def IdeContextHopMonitorTick(  # noqa: N802
    *,
    outcome: str,
    check_ms: float,
) -> Event:
    """Observation: one ide_context_hop_monitor background tick."""
    return Event(
        signal="ide_context_hop_monitor.tick",
        role="observation",
        payload={
            "outcome": outcome,
            "check_ms": check_ms,
        },
        scope="global",
    )


def emit_ide_context_hop_monitor_tick(**kwargs: Any) -> None:
    """Publish timing for one ide_context_hop_monitor tick."""
    _emit(IdeContextHopMonitorTick(**kwargs))


@event_factory
def IdeTabBudgetSampled(  # noqa: N802
    *,
    root_id: str,
    transcript_id: str,
    tool_calls: int,
    tool_result_tokens: int | None,
    used_tokens: int,
    window_limit_tokens: int | None,
    stop_class: str | None,
    hop_weight_basis: str | None,
    matched_categories: list[str],
) -> Event:
    """Observation: one per-tab CONTEXT_BUDGET probe during ide_context_hop_monitor."""
    return Event(
        signal="ide.tab.budget.sampled",
        role="observation",
        payload={
            "root_id": root_id,
            "transcript_id": transcript_id,
            "tool_calls": tool_calls,
            "tool_result_tokens": tool_result_tokens,
            "used_tokens": used_tokens,
            "window_limit_tokens": window_limit_tokens,
            "stop_class": stop_class,
            "hop_weight_basis": hop_weight_basis,
            "matched_categories": matched_categories,
        },
        scope="global",
    )


def emit_ide_tab_budget_sampled(**kwargs: Any) -> None:
    """Publish a per-tab IDE budget sample from the context-hop monitor."""
    _emit(IdeTabBudgetSampled(**kwargs))
