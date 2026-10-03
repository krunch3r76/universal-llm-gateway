"""Master-keyed land lease for S1a Amendment A1.

Complements ``FifoCapacityGate(limit=1)`` on ``kind=git_integrate`` with a
durable ledger row keyed by ``str(source_repo.resolve())``. Serializes
merge-out + green gate; refuses divergent path-overlap dirt on checked-out
master (see ``cursor_sdk_land_dirty``); releases on terminal.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from git_integrate.schema import RC_DIRTY_MASTER, RC_LAND_LEASE_TIMEOUT
from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import _connect
from services.git_integration_worker.cursor_sdk_land_dirty import (
    checked_out_master_dirty,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = get_logger(__name__)

_LAND_LEASE_DDL = """
CREATE TABLE IF NOT EXISTS cursor_sdk_land_leases (
    lease_key     TEXT PRIMARY KEY,
    holder_op_id  TEXT NOT NULL,
    acquired_at   TEXT NOT NULL
);
"""

_LAND_LEASE_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("holder_pid", "INTEGER"),
)

_DEFAULT_POLL_S = 0.02
_DEFAULT_ACQUIRE_TIMEOUT_S = 600.0
_UNCHECKABLE_HOLDER_BACKSTOP_S = 7200.0
_HEARTBEAT_INTERVAL_S = 300.0

# Re-export for callers/tests that imported the predicate from this module.
__all__ = (
    "DirtyMasterRefused",
    "LandLeaseAcquireTimeout",
    "acquire_land_lease_blocking",
    "checked_out_master_dirty",
    "dirty_master_envelope",
    "ensure_land_lease_schema",
    "land_lease_holder",
    "land_lease_timeout_envelope",
    "land_lease_waiter_report",
    "master_land_guard",
    "master_land_lease_key",
    "reap_stale_land_leases",
    "refresh_land_lease_heartbeat",
    "release_land_lease",
    "try_acquire_land_lease",
)


class DirtyMasterRefused(Exception):
    """Raised when checked-out master has divergent dirt on a landing path."""

    def __init__(self, *, reason: str, integration_id: str) -> None:
        self.integration_id = integration_id
        self.reason = reason
        super().__init__(reason)


class LandLeaseAcquireTimeout(TimeoutError):
    """Raised when another land holder does not release within the wait horizon."""

    def __init__(
        self,
        message: str = "",
        *,
        lease_key: str = "",
        waiter_op_id: str = "",
        holder_op_id: str | None = None,
        timeout_s: float = 0.0,
    ) -> None:
        self.lease_key = lease_key
        self.waiter_op_id = waiter_op_id
        self.holder_op_id = holder_op_id
        self.timeout_s = timeout_s
        self.report = land_lease_waiter_report(
            status="timeout",
            lease_key=lease_key,
            waiter_op_id=waiter_op_id,
            holder_op_id=holder_op_id,
            timeout_s=timeout_s,
        )
        super().__init__(message or str(self.report["reason"]))


def master_land_lease_key(source_repo: str | Path) -> str:
    """Ledger land lease identity — resolved ``source_repo`` path."""
    return str(Path(source_repo).resolve())


def ensure_land_lease_schema(conn: sqlite3.Connection) -> None:
    """Create the land-lease table when missing (shares dispatch ledger DB)."""
    conn.executescript(_LAND_LEASE_DDL)
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(cursor_sdk_land_leases)")
    }
    for name, decl in _LAND_LEASE_COLUMN_MIGRATIONS:
        if cols and name in cols:
            continue
        try:
            conn.execute(
                f"ALTER TABLE cursor_sdk_land_leases ADD COLUMN {name} {decl}"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def _holder_pid_alive(pid: int | None) -> bool | None:
    """Return True/False when pid is checkable on this host, else None."""
    if pid is None or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _now() -> str:
    return datetime.now(UTC).isoformat()


def try_acquire_land_lease(*, lease_key: str, holder_op_id: str) -> bool:
    """Attempt to take the master land lease; return False when held by another."""
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT holder_op_id, holder_pid FROM cursor_sdk_land_leases WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO cursor_sdk_land_leases "
                "(lease_key, holder_op_id, acquired_at, holder_pid) "
                "VALUES (?, ?, ?, ?)",
                (lease_key, holder_op_id, _now(), os.getpid()),
            )
            return True
        if row["holder_op_id"] == holder_op_id:
            conn.execute(
                "UPDATE cursor_sdk_land_leases SET holder_pid=?, acquired_at=? "
                "WHERE lease_key=? AND holder_op_id=?",
                (os.getpid(), _now(), lease_key, holder_op_id),
            )
            return True
        return False


def land_lease_holder(lease_key: str) -> str | None:
    """Current holder of the master land lease, or None when free."""
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        row = conn.execute(
            "SELECT holder_op_id FROM cursor_sdk_land_leases WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
    if row is None:
        return None
    return str(row["holder_op_id"])


def land_lease_waiter_report(
    *,
    status: str,
    lease_key: str,
    waiter_op_id: str,
    holder_op_id: str | None,
    timeout_s: float,
) -> dict[str, str | float | None]:
    """Waiter-facing report for a blocked G7 land. Names the holder."""
    return {
        "status": status,
        "lease_key": lease_key,
        "waiter_op_id": waiter_op_id,
        "holder_op_id": holder_op_id,
        "timeout_s": timeout_s,
        "reason": (
            f"master land lease {lease_key!r} held by {holder_op_id!r}; "
            f"waiter {waiter_op_id!r} status={status} timeout_s={timeout_s:.0f}"
        ),
    }


def land_lease_timeout_envelope(*, exc: LandLeaseAcquireTimeout) -> dict[str, Any]:
    """Rejected integrate/land envelope when the master lease wait expires."""
    integration_id = str(uuid.uuid4())
    return {
        "integration_id": integration_id,
        "status": "rejected",
        "reason_code": RC_LAND_LEASE_TIMEOUT,
        "reason": str(exc),
        "land_lease_waiter": exc.report,
    }


def release_land_lease(*, lease_key: str, holder_op_id: str) -> bool:
    """Release the land lease when ``holder_op_id`` still owns it."""
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        deleted = conn.execute(
            "DELETE FROM cursor_sdk_land_leases WHERE lease_key=? AND holder_op_id=?",
            (lease_key, holder_op_id),
        )
        return deleted.rowcount == 1


def refresh_land_lease_heartbeat(*, lease_key: str, holder_op_id: str) -> bool:
    """Refresh ``acquired_at`` for holders without a recorded pid.

    Legacy or rollback rows may lack ``holder_pid``; no current acquire path
    needs this. Callers that cannot record a pid must invoke at least every
    :data:`_HEARTBEAT_INTERVAL_S` while the lease is held.
    """
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        row = conn.execute(
            "SELECT holder_op_id, holder_pid FROM cursor_sdk_land_leases "
            "WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
        if row is None or str(row["holder_op_id"]) != holder_op_id:
            return False
        if row["holder_pid"] is not None:
            return True
        updated = conn.execute(
            "UPDATE cursor_sdk_land_leases SET acquired_at=? "
            "WHERE lease_key=? AND holder_op_id=? AND holder_pid IS NULL",
            (_now(), lease_key, holder_op_id),
        )
        return updated.rowcount == 1


def reap_stale_land_leases(
    *,
    backstop_s: float = _UNCHECKABLE_HOLDER_BACKSTOP_S,
) -> int:
    """Drop orphaned master land leases when the holder process is dead.

    Rows with a live ``holder_pid`` are never reaped by age. Rows whose pid
    cannot be checked are reaped only after ``backstop_s`` without a heartbeat.
    """
    now_ts = datetime.now(UTC).timestamp()
    cutoff = now_ts - backstop_s
    reaped = 0
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        rows = conn.execute(
            "SELECT lease_key, holder_op_id, acquired_at, holder_pid "
            "FROM cursor_sdk_land_leases"
        ).fetchall()
        for row in rows:
            pid = row["holder_pid"]
            alive = _holder_pid_alive(int(pid) if pid is not None else None)
            if alive is True:
                continue
            if alive is False:
                should_reap = True
            else:
                try:
                    seen = datetime.fromisoformat(row["acquired_at"]).timestamp()
                except ValueError:
                    seen = 0.0
                should_reap = seen < cutoff
            if should_reap:
                deleted = conn.execute(
                    "DELETE FROM cursor_sdk_land_leases "
                    "WHERE lease_key=? AND holder_op_id=? "
                    "AND acquired_at=? AND holder_pid IS ?",
                    (
                        row["lease_key"],
                        row["holder_op_id"],
                        row["acquired_at"],
                        row["holder_pid"],
                    ),
                )
                reaped += deleted.rowcount
    if reaped:
        logger.warning("reaped %d stale master land lease(s)", reaped)
    return reaped


async def acquire_land_lease_blocking(
    *,
    lease_key: str,
    holder_op_id: str,
    poll_interval_s: float = _DEFAULT_POLL_S,
    timeout_s: float = _DEFAULT_ACQUIRE_TIMEOUT_S,
) -> None:
    """Block until the master land lease is acquired or ``timeout_s`` elapses.

    The first missed acquire logs a waiter report naming the current holder.
    Timeout raises :class:`LandLeaseAcquireTimeout` with that report attached.
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    reported = False
    while True:
        acquired = await asyncio.to_thread(
            try_acquire_land_lease,
            lease_key=lease_key,
            holder_op_id=holder_op_id,
        )
        if acquired:
            return
        holder = await asyncio.to_thread(land_lease_holder, lease_key)
        if not reported:
            waiting = land_lease_waiter_report(
                status="waiting",
                lease_key=lease_key,
                waiter_op_id=holder_op_id,
                holder_op_id=holder,
                timeout_s=timeout_s,
            )
            logger.info("master land lease waiter %s", waiting["reason"])
            reported = True
        if asyncio.get_running_loop().time() >= deadline:
            raise LandLeaseAcquireTimeout(
                lease_key=lease_key,
                waiter_op_id=holder_op_id,
                holder_op_id=holder,
                timeout_s=timeout_s,
            )
        await asyncio.sleep(poll_interval_s)


@asynccontextmanager
async def master_land_guard(
    *,
    source_repo: str,
    holder_op_id: str,
    worktree_path: str,
) -> AsyncIterator[None]:
    """Acquire master land lease, refuse divergent path-overlap dirt, release.

    ``worktree_path`` is the arc worktree about to land — required so the dirty
    predicate can intersect porcelain paths with the landing change set.
    Lock order: caller must already hold ``FifoCapacityGate`` before entering.
    """
    lease_key = master_land_lease_key(source_repo)
    await acquire_land_lease_blocking(
        lease_key=lease_key,
        holder_op_id=holder_op_id,
        timeout_s=_DEFAULT_ACQUIRE_TIMEOUT_S,
    )
    try:
        dirty, reason = await asyncio.to_thread(
            checked_out_master_dirty, source_repo, worktree_path
        )
        if dirty:
            integration_id = str(uuid.uuid4())
            raise DirtyMasterRefused(reason=reason, integration_id=integration_id)
        yield
    finally:
        released = await asyncio.to_thread(
            release_land_lease, lease_key=lease_key, holder_op_id=holder_op_id
        )
        if not released:
            logger.warning(
                "master land lease release missed: lease_key=%s holder=%s",
                lease_key,
                holder_op_id,
            )


def dirty_master_envelope(*, exc: DirtyMasterRefused) -> dict[str, str]:
    """Rejected integrate/land envelope for dirty checked-out master."""
    porcelain = ""
    for line in exc.reason.splitlines():
        if line.startswith("hub-porcelain:"):
            porcelain = line[len("hub-porcelain:") :]
            break
    return {
        "integration_id": exc.integration_id,
        "status": "rejected",
        "reason_code": RC_DIRTY_MASTER,
        "reason": exc.reason,
        "working_tree": "NOT landed@working-tree",
        "hub_porcelain": porcelain,
    }
