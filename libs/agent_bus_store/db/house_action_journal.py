"""Append-only house action journal and joined house read (BIND 1–3)."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .connection import connect, write_connect
from .connection import now as bus_now

_HOUSE_BIRTH_WORK_KEY = re.compile(
    r"^HOUSE-BIRTH\s+\S+\s+work_key=(\S+)"
)
_MINT_ROOT_LITERAL = (
    "HOUSE-BIRTH {slug} work_key={work_key} — role:root continuity house on Glass."
)


class AppendHouseActionError(Exception):
    """Refused append. ``code`` is the HTTP detail code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_house_birth_mission(body: str) -> str:
    """Mission token from turn 1 when body starts with HOUSE-BIRTH."""
    text = str(body or "")
    if not text.startswith("HOUSE-BIRTH "):
        return ""
    match = _HOUSE_BIRTH_WORK_KEY.match(text)
    if not match:
        return ""
    return match.group(1)


def append_house_action(
    *,
    root_thread_id: str,
    kind: str,
    actor: str,
    action_why: str,
) -> dict[str, Any]:
    """Append one journal row under ``write_connect()``."""
    root = str(root_thread_id or "").strip()
    if not root:
        raise AppendHouseActionError("house_action_root_required")
    why = str(action_why or "").strip()
    if not why:
        raise AppendHouseActionError("house_action_why_required")
    with write_connect() as conn:
        exists = conn.execute(
            "SELECT id FROM threads WHERE id=?", (root,)
        ).fetchone()
        if exists is None:
            raise AppendHouseActionError("house_action_thread_missing")
        created_at = bus_now()
        cur = conn.execute(
            "INSERT INTO house_action_journal "
            "(root_thread_id, kind, actor, action_why, created_at) "
            "VALUES (?,?,?,?,?)",
            (root, str(kind), str(actor), why, created_at),
        )
        row_id = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM house_action_journal WHERE id=?", (row_id,)
        ).fetchone()
    return dict(row) if row is not None else {}


def list_joined_houses() -> list[dict[str, str]]:
    """Active liaison roots with newest journal row and HOUSE-BIRTH mission."""
    sql = """
        SELECT t.id AS root_id,
               t.slug AS slug,
               turn1.body AS turn1_body,
               j.action_why AS action_why,
               j.created_at AS action_at
        FROM threads t
        INNER JOIN thread_tags tt_root
            ON tt_root.thread_id = t.id AND tt_root.tag = 'role:root'
        INNER JOIN thread_tags tt_lane
            ON tt_lane.thread_id = t.id AND tt_lane.tag = 'lane:liaison'
        LEFT JOIN turns turn1
            ON turn1.thread = t.id AND turn1.turn_number = 1
        LEFT JOIN house_action_journal j
            ON j.id = (
                SELECT MAX(id) FROM house_action_journal h
                WHERE h.root_thread_id = t.id
            )
        WHERE t.status = 'active'
        ORDER BY t.id
    """
    with connect() as conn:
        rows = conn.execute(sql).fetchall()
    out: list[dict[str, str]] = []
    for row in rows:
        mission = parse_house_birth_mission(str(row["turn1_body"] or ""))
        out.append(
            {
                "root_id": str(row["root_id"]),
                "slug": str(row["slug"] or ""),
                "mission": mission,
                "action_why": str(row["action_why"] or ""),
                "as_of": str(row["action_at"] or ""),
            }
        )
    return out


def fold_newest_by_root(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Newest journal row per root (test/support helper)."""
    rows = conn.execute(
        "SELECT * FROM house_action_journal h WHERE id IN ("
        "SELECT MAX(id) FROM house_action_journal GROUP BY root_thread_id"
        ") ORDER BY root_thread_id"
    ).fetchall()
    return [dict(r) for r in rows]


__all__ = [
    "AppendHouseActionError",
    "append_house_action",
    "fold_newest_by_root",
    "list_joined_houses",
    "parse_house_birth_mission",
    "_MINT_ROOT_LITERAL",
]
