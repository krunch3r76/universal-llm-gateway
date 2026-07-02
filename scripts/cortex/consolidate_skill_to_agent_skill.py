#!/usr/bin/env python3
"""Fold legacy ``skill:`` entities into canonical ``agent_skill:``.

Arc 3924 retyped some guidance to ``skill:``; steady state is ``agent_skill:`` only
(``rule:`` remains for .cursor/rules). For each active ``skill:<slug>``:

- ``agent_skill:<slug>`` exists → fold (merge assertions/edges, tombstone skill)
- else → retype ``skill:<slug>`` → ``agent_skill:<slug>``

Default dry-run. Rebuilds ``entity_aliases`` on ``--apply``.

Usage::

  python scripts/cortex/consolidate_skill_to_agent_skill.py --dry-run
  python scripts/cortex/consolidate_skill_to_agent_skill.py --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from cortex_store.entity_aliases import rebuild_entity_aliases  # noqa: E402
from cortex_store.entity_merge import guidance_skill_fold_impl  # noqa: E402
from cortex_store.entity_rekey import entity_retype_impl  # noqa: E402

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")
_SUPPRESSED = frozenset({"merged", "retired", "archived"})


def _active_skill_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT id, lifecycle FROM entities WHERE type = 'skill' ORDER BY id"
    ).fetchall()
    out: list[str] = []
    for entity_id, lifecycle in rows:
        if (lifecycle or "active") in _SUPPRESSED:
            continue
        out.append(str(entity_id))
    return out


def _target_agent_skill(conn: sqlite3.Connection, slug: str) -> str | None:
    target = f"agent_skill:{slug}"
    row = conn.execute(
        "SELECT id, lifecycle FROM entities WHERE id = ?", (target,)
    ).fetchone()
    if not row:
        return None
    if (row[1] or "active") == "merged":
        return None
    return target


def _plan(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Return (source_id, action, target_or_new_id) rows."""
    plan: list[tuple[str, str, str]] = []
    for source_id in _active_skill_ids(conn):
        slug = source_id.split(":", 1)[1]
        target = _target_agent_skill(conn, slug)
        if target:
            plan.append((source_id, "fold", target))
        else:
            plan.append((source_id, "retype", f"agent_skill:{slug}"))
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--db", default=_DEFAULT_DB)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=120.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = _plan(conn)
        if not rows:
            print("OK no active skill: entities")
            return 0

        print(f"Plan: {len(rows)} skill: entities")
        for source_id, action, target in rows:
            print(f"  {action:6s}  {source_id}  ->  {target}")

        if args.dry_run:
            print("Dry-run only — pass --apply to execute")
            return 0

        folded = 0
        retyped = 0
        for source_id, action, target in rows:
            if action == "fold":
                guidance_skill_fold_impl(conn, source_id, target)
                folded += 1
            else:
                entity_retype_impl(conn, source_id, "agent_skill")
                retyped += 1

        rebuild_entity_aliases(conn)
        conn.commit()
        remaining = len(_active_skill_ids(conn))
        print(f"OK folded={folded} retyped={retyped} remaining_active_skill={remaining}")
        return 0 if remaining == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
