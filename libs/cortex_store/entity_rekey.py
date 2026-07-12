"""Public entry points for cortex entity identity primitives."""

from __future__ import annotations

import sqlite3
from typing import Any

from .entity_merge import entity_merge_impl
from .entity_rekey_core import entity_rekey_impl, entity_retype_impl

__all__ = ["entity_merge_impl", "entity_rekey_impl", "entity_retype_impl"]


def entity_rekey(conn: sqlite3.Connection, old_id: str, new_id: str) -> dict[str, Any]:
    return entity_rekey_impl(conn, old_id, new_id)


def entity_retype(
    conn: sqlite3.Connection,
    entity_id: str,
    new_type: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    return entity_retype_impl(conn, entity_id, new_type, force=force)


def entity_merge(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> dict[str, Any]:
    return entity_merge_impl(conn, source_id, target_id)
