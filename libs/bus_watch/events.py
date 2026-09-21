"""Liaison bus_watch lifecycle events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event, event_factory

_uds_publisher: Callable[[str, dict[str, Any]], None] | None = None


def register_uds_publisher(publisher: Callable[[str, dict[str, Any]], None]) -> None:
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
    _emit(LiaisonNightIdReset(**kwargs))


def emit_lease_forfeited(**kwargs: Any) -> None:
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
    _emit(LiaisonNowRowBound(**kwargs))


def emit_now_row_released(**kwargs: Any) -> None:
    _emit(LiaisonNowRowReleased(**kwargs))
