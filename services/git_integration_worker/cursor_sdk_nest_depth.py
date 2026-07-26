"""Nest depth precheck for cursor-sdk park stacks under limit=1."""

from __future__ import annotations

import sqlite3

MAX_NEST_DEPTH = 10
MAX_WALK_HOPS = 11


class NestDepthExceeded(Exception):  # noqa: N818 — worker_error_code token
    """Raised when nesting would exceed the configured park-stack depth cap."""


class NestParentNotLive(Exception):  # noqa: N818 — worker_error_code token
    """Raised when ``nest_under`` does not name the live write-lease holder."""


def park_stack_depth(
    conn: sqlite3.Connection,
    *,
    nest_under: str,
    child_dispatch_id: str,
) -> int:
    """Return the child depth if nesting under ``nest_under`` is allowed.

    Root live holder depth is 0; each nested child increments by one. Raises
    ``NestDepthExceeded`` when depth would exceed ``MAX_NEST_DEPTH`` or the
    ancestor walk exceeds ``MAX_WALK_HOPS``. Self-nest is rejected.
    """
    if nest_under == child_dispatch_id:
        raise NestDepthExceeded(
            f"nest_under must not equal child dispatch_id {child_dispatch_id!r}"
        )
    parent_depth = _ancestor_depth(conn, nest_under)
    child_depth = parent_depth + 1
    if child_depth > MAX_NEST_DEPTH:
        raise NestDepthExceeded(
            f"nest depth {child_depth} exceeds max {MAX_NEST_DEPTH}"
        )
    return child_depth


def _ancestor_depth(conn: sqlite3.Connection, dispatch_id: str) -> int:
    depth = 0
    current = dispatch_id
    for _ in range(MAX_WALK_HOPS):
        row = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches "
            "WHERE park_child_dispatch_id=? LIMIT 1",
            (current,),
        ).fetchone()
        if row is None:
            return depth
        depth += 1
        current = row["dispatch_id"]
    raise NestDepthExceeded(
        f"nest ancestor walk exceeded {MAX_WALK_HOPS} hops from {dispatch_id!r}"
    )
