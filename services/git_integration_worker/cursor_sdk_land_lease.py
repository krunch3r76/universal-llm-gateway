"""Master-keyed land lease for S1a Amendment A1.

Complements ``FifoCapacityGate(limit=1)`` on ``kind=git_integrate`` with a
durable ledger row keyed by ``str(source_repo.resolve())``. Serializes
merge-out + green gate; refuses dirty checked-out master; releases on terminal.
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from git_integrate.git_cas import is_dirty
from git_integrate.schema import RC_DIRTY_MASTER
from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import _connect

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

_DEFAULT_POLL_S = 0.02
_DEFAULT_ACQUIRE_TIMEOUT_S = 600.0
_STALE_LAND_LEASE_S = 900.0


class DirtyMasterRefused(Exception):
    """Raised when checked-out master has staged or unstaged dirt before merge-out."""

    def __init__(self, *, reason: str, integration_id: str) -> None:
        self.integration_id = integration_id
        self.reason = reason
        super().__init__(reason)


class LandLeaseAcquireTimeout(TimeoutError):
    """Raised when another land holder does not release within the wait horizon."""


def master_land_lease_key(source_repo: str | Path) -> str:
    """Ledger land lease identity — resolved ``source_repo`` path."""
    return str(Path(source_repo).resolve())


def ensure_land_lease_schema(conn: sqlite3.Connection) -> None:
    """Create the land-lease table when missing (shares dispatch ledger DB)."""
    conn.executescript(_LAND_LEASE_DDL)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_worktree_blocks(porcelain: str) -> list[tuple[str, str]]:
    """Return ``(worktree_path, branch_ref)`` pairs from porcelain output."""
    blocks: list[tuple[str, str]] = []
    path = ""
    branch = ""
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if path:
                blocks.append((path, branch))
            path = line[len("worktree ") :].strip()
            branch = ""
        elif line.startswith("branch "):
            branch = line[len("branch ") :].strip()
    if path:
        blocks.append((path, branch))
    return blocks


def checked_out_master_dirty(source_repo: str) -> tuple[bool, str]:
    """True when any worktree with ``refs/heads/master`` checked out is dirty."""
    repo = str(Path(source_repo).resolve())
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    if proc.returncode != 0:
        return False, ""

    for worktree_path, branch_ref in _parse_worktree_blocks(proc.stdout):
        if branch_ref != "refs/heads/master":
            continue
        if is_dirty(worktree_path):
            return True, (
                f"checked-out master worktree is dirty: {worktree_path!r}; "
                "refusing merge-out"
            )
    return False, ""


def try_acquire_land_lease(*, lease_key: str, holder_op_id: str) -> bool:
    """Attempt to take the master land lease; return False when held by another."""
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT holder_op_id FROM cursor_sdk_land_leases WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO cursor_sdk_land_leases (lease_key, holder_op_id, acquired_at) "
                "VALUES (?, ?, ?)",
                (lease_key, holder_op_id, _now()),
            )
            return True
        return row["holder_op_id"] == holder_op_id


def release_land_lease(*, lease_key: str, holder_op_id: str) -> bool:
    """Release the land lease when ``holder_op_id`` still owns it."""
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        deleted = conn.execute(
            "DELETE FROM cursor_sdk_land_leases "
            "WHERE lease_key=? AND holder_op_id=?",
            (lease_key, holder_op_id),
        )
        return deleted.rowcount == 1


def reap_stale_land_leases(*, threshold_s: float = _STALE_LAND_LEASE_S) -> int:
    """Drop orphaned land leases past ``threshold_s`` (worker restart recovery)."""
    cutoff = datetime.now(UTC).timestamp() - threshold_s
    reaped = 0
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        rows = conn.execute(
            "SELECT lease_key, acquired_at FROM cursor_sdk_land_leases"
        ).fetchall()
        for row in rows:
            try:
                seen = datetime.fromisoformat(row["acquired_at"]).timestamp()
            except ValueError:
                seen = 0.0
            if seen < cutoff:
                conn.execute(
                    "DELETE FROM cursor_sdk_land_leases WHERE lease_key=?",
                    (row["lease_key"],),
                )
                reaped += 1
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
    """Block until the master land lease is acquired or ``timeout_s`` elapses."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        acquired = await asyncio.to_thread(
            try_acquire_land_lease,
            lease_key=lease_key,
            holder_op_id=holder_op_id,
        )
        if acquired:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise LandLeaseAcquireTimeout(
                f"master land lease {lease_key!r} unavailable after {timeout_s:.0f}s"
            )
        await asyncio.sleep(poll_interval_s)


@asynccontextmanager
async def master_land_guard(
    *,
    source_repo: str,
    holder_op_id: str,
) -> AsyncIterator[None]:
    """Acquire master land lease, refuse dirty master, release on exit.

    Lock order: caller must already hold ``FifoCapacityGate`` before entering.
    """
    lease_key = master_land_lease_key(source_repo)
    await acquire_land_lease_blocking(lease_key=lease_key, holder_op_id=holder_op_id)
    try:
        dirty, reason = await asyncio.to_thread(checked_out_master_dirty, source_repo)
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
    return {
        "integration_id": exc.integration_id,
        "status": "rejected",
        "reason_code": RC_DIRTY_MASTER,
        "reason": exc.reason,
    }
