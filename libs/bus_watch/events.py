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
