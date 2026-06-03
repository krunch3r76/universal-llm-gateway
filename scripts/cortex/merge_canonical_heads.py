#!/usr/bin/env python3
"""Merge duplicate committed canonical entity heads into a survivor (graph hygiene).

Migrates assertions, relationships, session_edges, tags, and surface_forms from
dup → survivor; unions aliases on survivor; sets dup lifecycle=merged.

Default dry-run. Agent-bus 1219 / mansubi-canonical-head-cleanup.md.

Usage::

  python scripts/cortex/merge_canonical_heads.py --dry-run
  python scripts/cortex/merge_canonical_heads.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_SCRIPTS_CORTEX = os.path.dirname(os.path.abspath(__file__))
_REPO_LIBS = os.path.join(_SCRIPTS_CORTEX, "..", "..", "libs")
if _REPO_LIBS not in sys.path:
    sys.path.insert(0, _REPO_LIBS)

from cortex_store.substantiation_sync import recompute_entity_substantiation_status  # noqa: E402

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")

# agent-bus 1219 merge set (person heads only)
MANSUBI_1219_MERGES: list[tuple[str, str, str]] = [
    ("person:kaywan-joseph-mansubi", "person:kaywan-mansubi", "Kaywan Joseph Mansubi"),
    ("person:kaywan-joe-mansubi", "person:kaywan-mansubi", "Kaywan Joseph Mansubi"),
    ("person:mary-morshedi-mansubi", "person:mary-mansubi", "Mary Morshedi Mansubi"),
]


@dataclass
class MergeCounts:
    assertions_moved: int = 0
    relationships_moved: int = 0
    relationships_retired_dup: int = 0
    session_edges_moved: int = 0
    surface_forms_moved: int = 0
    tags_moved: int = 0
    fts_entity_id_patched: int = 0
    aliases_added: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    if isinstance(val, list):
        return [str(x) for x in val]
    return [str(val)]


def _union_aliases(
    survivor_raw: str | None, dup_raw: str | None, dup_name: str, legal_alias: str
) -> tuple[str, list[str]]:
    seen: set[str] = set()
    out: list[str] = []
    for src in (
        _parse_aliases(survivor_raw),
        _parse_aliases(dup_raw),
        [dup_name, legal_alias],
    ):
        for item in src:
            key = item.strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item.strip())
    return json.dumps(out), [a for a in out if a not in _parse_aliases(survivor_raw)]


def _rel_key(row: sqlite3.Row) -> tuple[str, str, str, str]:
    return (
        str(row["type"]),
        str(row["from_entity"]),
        str(row["to_entity"]),
        str(row["role"] or ""),
    )


def _active_rel_exists(
    conn: sqlite3.Connection, key: tuple[str, str, str, str]
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM relationships
        WHERE active = 1 AND valid_until IS NULL
          AND type = ? AND from_entity = ? AND to_entity = ?
          AND COALESCE(role, '') = ?
        LIMIT 1
        """,
        key,
    ).fetchone()
    return row is not None


def merge_pair(
    conn: sqlite3.Connection,
    *,
    dup_id: str,
    survivor_id: str,
    legal_alias: str,
    dry_run: bool,
) -> MergeCounts:
    counts = MergeCounts()
    dup = conn.execute(
        "SELECT id, name, aliases, lifecycle FROM entities WHERE id = ?", (dup_id,)
    ).fetchone()
    survivor = conn.execute(
        "SELECT id, name, aliases, lifecycle FROM entities WHERE id = ?",
        (survivor_id,),
    ).fetchone()
    if not dup:
        raise SystemExit(f"dup entity missing: {dup_id}")
    if not survivor:
        raise SystemExit(f"survivor entity missing: {survivor_id}")
    if dup_id == survivor_id:
        raise SystemExit(f"dup and survivor are the same: {dup_id}")
    if dup["lifecycle"] == "merged":
        print(f"  skip {dup_id}: already lifecycle=merged")
        return counts

    new_aliases_json, added = _union_aliases(
        survivor["aliases"], dup["aliases"], str(dup["name"]), legal_alias
    )
    counts.aliases_added = added

    assertion_ids = [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM assertions WHERE entity_id = ?", (dup_id,)
        ).fetchall()
    ]
    counts.assertions_moved = len(assertion_ids)

    rels = conn.execute(
        """
        SELECT id, type, from_entity, to_entity, role
        FROM relationships
        WHERE active = 1 AND valid_until IS NULL
          AND (from_entity = ? OR to_entity = ?)
        """,
        (dup_id, dup_id),
    ).fetchall()

    edge_moves = conn.execute(
        """
        SELECT COUNT(*) FROM session_edges
        WHERE valid_until IS NULL AND (from_node = ? OR to_node = ?)
        """,
        (dup_id, dup_id),
    ).fetchone()[0]
    counts.session_edges_moved = int(edge_moves)

    sf_moves = conn.execute(
        "SELECT COUNT(*) FROM surface_forms WHERE entity_id = ?", (dup_id,)
    ).fetchone()[0]
    counts.surface_forms_moved = int(sf_moves)

    tag_moves = conn.execute(
        "SELECT COUNT(*) FROM tag_assignments WHERE entity_id = ?", (dup_id,)
    ).fetchone()[0]
    counts.tags_moved = int(tag_moves)

    if dry_run:
        print(
            f"  would merge {dup_id} → {survivor_id}: "
            f"assertions={counts.assertions_moved} rels={len(rels)} "
            f"edges={counts.session_edges_moved} aliases+={added}"
        )
        return counts

    now = _now_iso()
    conn.execute(
        "UPDATE assertions SET entity_id = ?, updated_at = ? WHERE entity_id = ?",
        (survivor_id, now, dup_id),
    )

    for rel in rels:
        new_from = survivor_id if rel["from_entity"] == dup_id else rel["from_entity"]
        new_to = survivor_id if rel["to_entity"] == dup_id else rel["to_entity"]
        if new_from == new_to:
            conn.execute(
                "UPDATE relationships SET active = 0, valid_until = ?, updated_at = ? "
                "WHERE id = ?",
                (now, now, rel["id"]),
            )
            counts.relationships_retired_dup += 1
            continue
        key = (str(rel["type"]), new_from, new_to, str(rel["role"] or ""))
        if _active_rel_exists(conn, key):
            conn.execute(
                "UPDATE relationships SET active = 0, valid_until = ?, updated_at = ? "
                "WHERE id = ?",
                (now, now, rel["id"]),
            )
            counts.relationships_retired_dup += 1
        else:
            conn.execute(
                """
                UPDATE relationships
                SET from_entity = ?, to_entity = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_from, new_to, now, rel["id"]),
            )
            counts.relationships_moved += 1

    conn.execute(
        """
        UPDATE session_edges
        SET from_node = CASE WHEN from_node = ? THEN ? ELSE from_node END,
            to_node = CASE WHEN to_node = ? THEN ? ELSE to_node END
        WHERE valid_until IS NULL AND (from_node = ? OR to_node = ?)
        """,
        (dup_id, survivor_id, dup_id, survivor_id, dup_id, dup_id),
    )

    conn.execute(
        "UPDATE surface_forms SET entity_id = ? WHERE entity_id = ?",
        (survivor_id, dup_id),
    )
    conn.execute(
        "UPDATE tag_assignments SET entity_id = ? WHERE entity_id = ?",
        (survivor_id, dup_id),
    )
    conn.execute(
        "UPDATE entity_salience_cache SET entity_id = ? WHERE entity_id = ?",
        (survivor_id, dup_id),
    )
    conn.execute(
        "UPDATE entity_access_log SET entity_id = ? WHERE entity_id = ?",
        (survivor_id, dup_id),
    )
    # access_summary is keyed (entity_id, agent, week_start) — drop dup rows that collide.
    dup_summaries = conn.execute(
        "SELECT agent, week_start FROM entity_access_summary WHERE entity_id = ?",
        (dup_id,),
    ).fetchall()
    for row in dup_summaries:
        agent, week = row[0], row[1]
        clash = conn.execute(
            """
            SELECT 1 FROM entity_access_summary
            WHERE entity_id = ? AND agent = ? AND week_start = ?
            """,
            (survivor_id, agent, week),
        ).fetchone()
        if clash:
            conn.execute(
                """
                DELETE FROM entity_access_summary
                WHERE entity_id = ? AND agent = ? AND week_start = ?
                """,
                (dup_id, agent, week),
            )
        else:
            conn.execute(
                """
                UPDATE entity_access_summary SET entity_id = ?
                WHERE entity_id = ? AND agent = ? AND week_start = ?
                """,
                (survivor_id, dup_id, agent, week),
            )

    conn.execute(
        """
        UPDATE entities SET aliases = ?, updated_at = ? WHERE id = ?
        """,
        (new_aliases_json, now, survivor_id),
    )
    conn.execute(
        """
        UPDATE entities SET lifecycle = 'merged', updated_at = ? WHERE id = ?
        """,
        (now, dup_id),
    )

    if assertion_ids:
        placeholders = ",".join("?" * len(assertion_ids))
        conn.execute(
            f"""
            UPDATE assertions_fts SET entity_id = ?
            WHERE assertion_id IN ({placeholders})
            """,
            [survivor_id, *assertion_ids],
        )
        counts.fts_entity_id_patched = len(assertion_ids)

    recompute_entity_substantiation_status(conn, survivor_id)
    recompute_entity_substantiation_status(conn, dup_id)
    return counts


def scan_dangling(conn: sqlite3.Connection, entity_id: str) -> dict[str, int]:
    """Post-merge scan: rows still pointing at a merged dup head."""
    out: dict[str, int] = {}
    for label, sql in (
        ("assertions", "SELECT COUNT(*) FROM assertions WHERE entity_id = ?"),
        (
            "relationships",
            "SELECT COUNT(*) FROM relationships WHERE active = 1 AND valid_until IS NULL "
            "AND (from_entity = ? OR to_entity = ?)",
        ),
        (
            "session_edges",
            "SELECT COUNT(*) FROM session_edges WHERE valid_until IS NULL "
            "AND (from_node = ? OR to_node = ?)",
        ),
    ):
        if "from_entity" in sql or "from_node" in sql:
            out[label] = int(conn.execute(sql, (entity_id, entity_id)).fetchone()[0])
        else:
            out[label] = int(conn.execute(sql, (entity_id,)).fetchone()[0])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge duplicate canonical entity heads"
    )
    parser.add_argument("--db", default=_DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="Commit (default dry-run)")
    parser.add_argument(
        "--pair",
        action="append",
        metavar="DUP,SURVIVOR,ALIAS",
        help="Extra merge triple (repeatable)",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    merges: list[tuple[str, str, str]] = list(MANSUBI_1219_MERGES)
    if args.pair:
        for raw in args.pair:
            parts = raw.split(",", 2)
            if len(parts) != 3:
                raise SystemExit(f"--pair needs DUP,SURVIVOR,ALIAS — got {raw!r}")
            merges.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))

    conn = sqlite3.connect(args.db, timeout=120.0)
    conn.execute("PRAGMA busy_timeout = 120000")
    conn.row_factory = sqlite3.Row
    mode = "dry-run" if dry_run else "apply"
    print(f"## merge_canonical_heads ({mode})")
    print(f"- db: {args.db}")
    print(f"- pairs: {len(merges)}")

    totals = MergeCounts()
    try:
        for dup_id, survivor_id, legal_alias in merges:
            print(f"\n### {dup_id} → {survivor_id}")
            c = merge_pair(
                conn,
                dup_id=dup_id,
                survivor_id=survivor_id,
                legal_alias=legal_alias,
                dry_run=dry_run,
            )
            totals.assertions_moved += c.assertions_moved
            totals.relationships_moved += c.relationships_moved
            totals.relationships_retired_dup += c.relationships_retired_dup
            totals.session_edges_moved += c.session_edges_moved
            totals.fts_entity_id_patched += c.fts_entity_id_patched
            if not dry_run:
                dangling = scan_dangling(conn, dup_id)
                print(f"  dangling on dup after merge: {dangling}")
        if not dry_run:
            conn.commit()
            print("\n## committed")
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(
        f"\n## totals: assertions={totals.assertions_moved} "
        f"rels_moved={totals.relationships_moved} rels_retired_dup="
        f"{totals.relationships_retired_dup} edges={totals.session_edges_moved} "
        f"fts_entity_id_patched={totals.fts_entity_id_patched}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
