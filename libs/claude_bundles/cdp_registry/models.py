"""Define CDP registry row types, failure classes, and status vocabulary governing host lifecycle transitions and persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane

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
_LISTABLE_STATUSES = frozenset({"active", "orphaned_alive", "retained"})
STALE_ACTIVE_TTL_S = 6 * 3600
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
class HygieneReclaimResult:
    reclaimed_ports: list[int]
    removed_profiles: list[str]


def _row_to_registration(row: dict[str, Any]) -> Registration:
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
