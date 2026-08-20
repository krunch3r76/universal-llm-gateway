"""Define CDP registry row types, failure classes, and status vocabulary governing host lifecycle transitions and persistence."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A dormant row owns no Chrome process and no port: the CSE URL and the seeded
# profile are the durable identity, so relaunch picks whatever port is free.
STATUS_DORMANT = "dormant"

_RESERVED_STATUSES = frozenset(
    {
        "allocating",
        "active",
        "released",
        "retained",
        "orphaned_retry",
        "orphaned_alive",
    }
)
_RECLAIMABLE_STATUSES = frozenset({"released", "orphaned_retry"})
# Host-listable == "a Chrome process may still hold this CSE". Dormant is
# excluded so no live-attachment consumer mistakes a released host for an open
# tab. Seat identity is a separate axis (``seat_open``); do not put ``seat`` on
# this status set.
_HOST_LISTABLE_STATUSES = frozenset({"active", "orphaned_alive", "retained"})
# Occupancy axis — distinct from lifecycle status. Seating consults this set,
# not ``status == "active"``. ``retained`` still occupies a host after
# kill=False / hygiene keep; ``dormant`` does not (no Chrome). ``orphaned_alive``
# stays off this axis (existing seating contract). Registry-only: no CDP probe.
_CAPACITY_STATUSES = frozenset({"active", "retained"})
STALE_ACTIVE_TTL_S = 6 * 3600
_DEFAULT_DORMANT_TTL_S = 24 * 3600
_DEFAULT_DORMANT_MAX_ROWS = 16
# Chrome-host mission taxonomy (bus parent is parent_thread, not nest_under).
MISSION_KINDS = frozenset({"root", "hop", "side", "parallel"})

_LaunchFn = Callable[[int, Path], int]
_ListenFn = Callable[[int], bool]


class RegistryError(RuntimeError):
    """Base exception for failures while allocating, attaching, or managing CDP registry hosts and persistent rows."""


class RegistryBusyError(RegistryError):
    """Raised when a second driver attempts to attach a registration already held by another driver."""


class RegistryExhaustedError(RegistryError):
    """Raised when the configured CDP registry port pool has no free port remaining for a new host."""


@dataclass(frozen=True)
class Registration:
    registration_id: str
    port: int
    profile_suffix: str
    profile: Path
    cdp_url: str
    holder: str
    purpose: str | None = None
    display: str | None = None
    mission_kind: str | None = None
    parent_thread: str | None = None


@dataclass(frozen=True)
class DormantSeat:
    """A CSE whose Chrome process is gone while its URL and profile persist.

    Deliberately carries no ``cdp_url``: the last port is historical evidence and
    may already belong to another host, so a consumer must relaunch to get one.
    """

    registration_id: str
    chat_url: str
    profile_suffix: str
    profile: Path
    holder: str
    purpose: str | None = None
    mission_kind: str | None = None
    parent_thread: str | None = None
    dormant_at: float | None = None
    last_port: int | None = None


@dataclass(frozen=True)
class HygieneReclaimResult:
    reclaimed_ports: list[int]
    removed_profiles: list[str]


def dormant_ttl_s() -> float:
    """Age after which a dormant row's profile is reclaimed (``CDP_DORMANT_TTL_S``)."""
    return _positive_env_float("CDP_DORMANT_TTL_S", _DEFAULT_DORMANT_TTL_S)


def dormant_max_rows() -> int:
    """Dormant row cap before oldest profiles are reclaimed (``CDP_DORMANT_MAX_ROWS``)."""
    return int(_positive_env_float("CDP_DORMANT_MAX_ROWS", _DEFAULT_DORMANT_MAX_ROWS))


def seat_open(row: dict[str, Any], lane: str | None = None) -> bool:
    """Return True when *row* holds an unclosed seat, optionally for *lane*.

    ``seat_open(row, L) ≡ row.seat_lane == L ∧ row.seat_closed_at is None``.
    Status is irrelevant: a dormant row may still be the driving seat.
    """
    seat_lane = str(row.get("seat_lane") or "").strip()
    if not seat_lane:
        return False
    if row.get("seat_closed_at") is not None:
        return False
    if lane is not None and seat_lane != str(lane).strip():
        return False
    return True


def _positive_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _row_to_dormant_seat(row: dict[str, Any]) -> DormantSeat:
    from claude_bundles import cdp_lane

    suffix = str(row["profile_suffix"])
    port = row.get("port")
    return DormantSeat(
        registration_id=str(row["registration_id"]),
        chat_url=str(row.get("chat_url") or ""),
        profile_suffix=suffix,
        profile=cdp_lane.profile_for(suffix),
        holder=str(row["holder"]),
        purpose=row.get("purpose"),
        mission_kind=row.get("mission_kind"),
        parent_thread=row.get("parent_thread"),
        dormant_at=row.get("dormant_at"),
        last_port=port if isinstance(port, int) else None,
    )


def _row_to_registration(row: dict[str, Any]) -> Registration:
    from claude_bundles import cdp_lane

    suffix = str(row["profile_suffix"])
    port = int(row["port"])
    return Registration(
        registration_id=str(row["registration_id"]),
        port=port,
        profile_suffix=suffix,
        profile=cdp_lane.profile_for(suffix),
        cdp_url=f"http://127.0.0.1:{port}",
        holder=str(row["holder"]),
        purpose=row.get("purpose"),
        display=row.get("display"),
        mission_kind=row.get("mission_kind"),
        parent_thread=row.get("parent_thread"),
    )
