"""Guarded, observable hard-purge for genuinely-disposable cortex entities.

Replaces the FK-off ``sqlite3`` CLI ``DELETE FROM entities`` vector (thread 1533 Rec #2)
with a FK-ON-safe, guarded, event-emitting path. Disposable = probe / test fixtures
only; canonical knowledge is never reachable here. Cascade orphan-sweep is IMPORTED
from cascade_hygiene, not reimplemented (boundary with todo:reaper-cascade-fk-hygiene).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from .cascade_hygiene import purge_fk_orphans
from .db import table_exists
from .dispatch_ops._shared import record
from .entity_rekey_core import (
    _load_entity,
    begin_identity_txn,
    check_foreign_keys,
)

_DISPOSABLE_ID_PREFIXES: tuple[str, ...] = (
    "decision:rekey-probe-",
    "decision:fk-verify-probe-",
    "decision:probe-",
    "todo:fk-verify-",
    "todo:fk-test-",
)

_FUNCTIONAL_REF_DELETES: tuple[tuple[str, str], ...] = (
    ("entity_access_log", "entity_id = ?"),
    ("entity_access_summary", "entity_id = ?"),
    ("journal_links", "to_entity = ?"),
)


@dataclass(frozen=True)
class PurgeResult:
    entity_id: str
    entity_type: str
    assertions_deleted: int
    orphan_sweep: dict[str, int]


def _parse_attrs(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _assertion_ids(conn: sqlite3.Connection, entity_id: str) -> list[int]:
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM assertions WHERE entity_id = ?", (entity_id,)
        )
    ]


def _is_disposable(
    conn: sqlite3.Connection, entity_id: str, row: dict[str, Any]
) -> bool:
    if entity_id.startswith(_DISPOSABLE_ID_PREFIXES):
        return True
    if _parse_attrs(row.get("attributes")).get("disposable") is True:
        return True
    if str(row.get("lifecycle")) == "reaped":
        live = conn.execute(
            "SELECT COUNT(*) FROM assertions "
            "WHERE entity_id = ? AND superseded_by IS NULL",
            (entity_id,),
        ).fetchone()[0]
        if int(live) == 0:
            return True
    return False


def _inbound_reference_guard(
    conn: sqlite3.Connection, assertion_ids: list[int]
) -> None:
    if not assertion_ids:
        return
    placeholders = ", ".join("?" for _ in assertion_ids)
    aset = tuple(assertion_ids)
    inbound = [
        int(r[0])
        for r in conn.execute(
            f"SELECT id FROM assertions "
            f"WHERE id NOT IN ({placeholders}) "
            f"AND (superseded_by IN ({placeholders}) "
            f"OR fulfillment_assertion_id IN ({placeholders}))",
            aset + aset + aset,
        )
    ]
    neardup = [
        int(r[0])
        for r in conn.execute(
            f"SELECT id FROM near_duplicate_flags "
            f"WHERE (assertion_id IN ({placeholders}) "
            f"AND duplicate_of NOT IN ({placeholders})) "
            f"OR (duplicate_of IN ({placeholders}) "
            f"AND assertion_id NOT IN ({placeholders}))",
            aset * 4,
        )
    ]
    if inbound or neardup:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "detail": "entity is referenced by foreign assertions; not disposable",
                "inbound_assertion_ids": inbound,
                "inbound_neardup_ids": neardup,
            },
        )


def _delete_children(
    conn: sqlite3.Connection, entity_id: str, assertion_ids: list[int]
) -> None:
    if assertion_ids:
        ph = ", ".join("?" for _ in assertion_ids)
        ids = tuple(assertion_ids)
        conn.execute(f"DELETE FROM assertions_fts WHERE assertion_id IN ({ph})", ids)
        conn.execute(
            f"DELETE FROM near_duplicate_flags "
            f"WHERE assertion_id IN ({ph}) OR duplicate_of IN ({ph})",
            ids + ids,
        )
    conn.execute("DELETE FROM tag_assignments WHERE entity_id = ?", (entity_id,))
    conn.execute("DELETE FROM surface_forms WHERE entity_id = ?", (entity_id,))
    conn.execute(
        "DELETE FROM relationships WHERE from_entity = ? OR to_entity = ?",
        (entity_id, entity_id),
    )
    conn.execute("DELETE FROM entity_salience_cache WHERE entity_id = ?", (entity_id,))
    conn.execute("DELETE FROM event_chain_members WHERE event_id = ?", (entity_id,))
    conn.execute(
        "UPDATE event_chains SET root_event_id = NULL WHERE root_event_id = ?",
        (entity_id,),
    )
    if table_exists(conn, "session_edges"):
        conn.execute(
            "DELETE FROM session_edges WHERE from_node = ? OR to_node = ?",
            (entity_id, entity_id),
        )
    for table, where in _FUNCTIONAL_REF_DELETES:
        if table_exists(conn, table):
            conn.execute(f"DELETE FROM {table} WHERE {where}", (entity_id,))
    if assertion_ids:
        conn.execute("DELETE FROM assertions WHERE entity_id = ?", (entity_id,))


def purge_disposable_entity(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    actor: str,
    reason: str,
    force: bool = False,
) -> PurgeResult:
    """Hard-delete a disposable entity and its children on a FK-ON connection.

    Raises HTTPException(422) on any guard failure; HTTPException(404) if absent.
    Emits ``cortex.entity.purged`` post-commit. ``force`` bypasses the disposable
    + confirmed-band policy guards only; the FK-ON and inbound-reference integrity
    guards are never bypassed.
    """
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"detail": "fk_enforcement_off", "hint": "use cortex_conn() / _connect()"},
        )
    row = _load_entity(conn, entity_id)
    cid = str(row["id"])
    entity_type = str(row["type"])

    if not force:
        if str(row.get("confidence_band")) == "confirmed":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {
                    "detail": "confirmed-band entity; pass force=True to purge",
                    "id": cid,
                },
            )
        if not _is_disposable(conn, cid, row):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"detail": "entity is not disposable", "id": cid},
            )

    assertion_ids = _assertion_ids(conn, cid)
    _inbound_reference_guard(conn, assertion_ids)

    begin_identity_txn(conn)
    try:
        _delete_children(conn, cid, assertion_ids)
        deleted = conn.execute("DELETE FROM entities WHERE id = ?", (cid,)).rowcount
        if deleted != 1:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"detail": "entity row vanished before delete", "id": cid},
            )
        orphan_sweep = purge_fk_orphans(conn)
        check_foreign_keys(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    record(
        "cortex.entity.purged",
        entity_id=cid,
        entity_type=entity_type,
        actor=actor,
        reason=reason,
        assertions_deleted=len(assertion_ids),
        orphan_sweep=orphan_sweep,
    )
    return PurgeResult(
        entity_id=cid,
        entity_type=entity_type,
        assertions_deleted=len(assertion_ids),
        orphan_sweep=orphan_sweep,
    )
