#!/usr/bin/env python3
"""Migrate todos.db items into Cortex entities.

Phase 1: Delete stale v2.2 todo:*/event:todo_* entities, normalize todos.db.
Phase 2: Create todo entities + extract spec files for rich todos.

Usage:
    python scripts/migrate-todos-to-entities.py             # dry-run
    python scripts/migrate-todos-to-entities.py --execute    # live
    python scripts/migrate-todos-to-entities.py --phase 1    # phase 1 only
    python scripts/migrate-todos-to-entities.py --phase 2    # phase 2 only
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import textwrap
from pathlib import Path

from cortex_store.db import _connect

CORTEX_DB = Path.home() / ".cortex" / "cortex.db"
TODOS_DB = Path.home() / ".cortex" / "todos.db"
SPECS_DIR = Path(__file__).resolve().parent.parent / "tasks" / "specs"

PRIORITY_MAP: dict[str, str] = {
    "high": "high",
    "short_term": "high",
    "critical": "high",
    "medium": "medium",
    "medium_term": "medium",
    "normal": "medium",
    "low": "backlog",
    "backlog": "backlog",
    "deferred": "backlog",
    "long_term": "backlog",
    "experimental": "backlog",
}

DOMAIN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brag\b", re.I), "rag"),
    (re.compile(r"\bpipeline\b", re.I), "pipeline"),
    (re.compile(r"\bmcp\b", re.I), "mcp"),
    (re.compile(r"\bagent.bus\b", re.I), "agent"),
    (re.compile(r"\bcortex\b", re.I), "cortex"),
    (re.compile(r"\bconsult\b", re.I), "tooling"),
    (re.compile(r"\btooling\b|\btui\b|\bscript\b|\bcli\b", re.I), "tooling"),
    (re.compile(r"\bcloud.proxy\b|cloud_proxy\b", re.I), "infra"),
    (re.compile(r"\binfra\b|\bsecurity\b|\bgateway\b|\bcatalog\b", re.I), "infra"),
    (re.compile(r"\bstargate\b|\brouting\b|\bfederation\b", re.I), "infra"),
    (re.compile(r"\bevent\b|\bobserv\b|\bscheduling\b", re.I), "infra"),
    (re.compile(r"\boverhaul\b|\bdoc\b", re.I), "tooling"),
    (re.compile(r"\bclip\b", re.I), "mcp"),
    (re.compile(r"\btrading\b", re.I), "infra"),
    (re.compile(r"\bemployment\b|\blegal\b|\bpersonal\b", re.I), "infra"),
]

STATUS_MAP: dict[str, str] = {
    "open": "open",
    "in_progress": "open",
    "done": "done",
    "resolved": "done",
    "deferred": "deferred",
}

SPEC_MARKERS = ("\n## ", "\n- ", "\n1.", "\n```")
SPEC_LEN_THRESHOLD = 400


def normalize_domain(raw: str) -> str:
    """Map a multi-valued domain string to a single canonical domain."""
    if not raw:
        return "infra"
    for pattern, canonical in DOMAIN_RULES:
        if pattern.search(raw):
            return canonical
    return "infra"


def normalize_priority(raw: str) -> str:
    return PRIORITY_MAP.get(raw, "backlog")


def normalize_status(raw: str) -> str:
    return STATUS_MAP.get(raw, "open")


def needs_spec(description: str, refs_json: str) -> bool:
    """Structural heuristic: does this todo need a spec file?"""
    if not description:
        return False
    try:
        refs = json.loads(refs_json) if refs_json else {}
    except (json.JSONDecodeError, TypeError):
        refs = {}
    if refs.get("spec"):
        return True
    has_structure = any(m in description for m in SPEC_MARKERS)
    is_long = len(description) > SPEC_LEN_THRESHOLD
    return has_structure or is_long


def first_sentence(text: str) -> str:
    """Extract first sentence as entity description summary."""
    if not text:
        return ""
    lines = text.strip().split("\n")
    first = lines[0].strip()
    if len(first) <= 200:
        return first
    return first[:197] + "..."


def write_spec_file(slug: str, title: str, description: str, *, dry_run: bool) -> Path:
    """Write a spec file in the standardized format."""
    spec_path = SPECS_DIR / f"{slug}.md"
    if spec_path.exists():
        if not dry_run:
            print(f"  SKIP spec (exists): {spec_path.name}")
        return spec_path

    sections = _parse_description_to_spec(title, description)
    if dry_run:
        print(f"  WOULD write spec: {spec_path.name} ({len(description)} chars)")
        return spec_path

    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(sections, encoding="utf-8")
    print(f"  WROTE spec: {spec_path.name}")
    return spec_path


def _parse_description_to_spec(title: str, description: str) -> str:
    """Convert a todo description into a standardized spec file."""
    if "\n## " in description:
        return f"# {title}\n\n{description.strip()}\n"
    problem = description.strip()
    return textwrap.dedent(f"""\
        # {title}

        ## Problem

        {problem}
    """)


# ── Phase 1: Cleanup ─────────────────────────────────────────────────────


_TODO_ENTITY_ID_SQL = "entity_id LIKE 'todo:%' OR entity_id LIKE 'event:todo_%'"
_EVENT_CHAIN_EVENT_ID_SQL = "event_id LIKE 'event:todo_%'"


def phase1_delete_stale_entities(
    cortex: sqlite3.Connection, *, dry_run: bool
) -> dict[str, int]:
    """Delete v2.2 todo:*/event:todo_* entities and all referencing child rows."""
    counts: dict[str, int] = {}

    for label, sql in [
        (
            "assertions",
            f"DELETE FROM assertions WHERE {_TODO_ENTITY_ID_SQL}",
        ),
        (
            "surface_forms",
            f"DELETE FROM surface_forms WHERE {_TODO_ENTITY_ID_SQL}",
        ),
        (
            "tag_assignments",
            f"DELETE FROM tag_assignments WHERE {_TODO_ENTITY_ID_SQL}",
        ),
        (
            "event_chain_members",
            f"DELETE FROM event_chain_members WHERE {_EVENT_CHAIN_EVENT_ID_SQL}",
        ),
        (
            "salience_cache",
            f"DELETE FROM entity_salience_cache WHERE {_TODO_ENTITY_ID_SQL}",
        ),
        (
            "relationships",
            "DELETE FROM relationships WHERE from_entity LIKE 'todo:%' OR to_entity LIKE 'todo:%' OR from_entity LIKE 'event:todo_%' OR to_entity LIKE 'event:todo_%'",
        ),
        (
            "event_entities",
            "DELETE FROM entities WHERE id LIKE 'event:todo_%'",
        ),
        (
            "todo_entities",
            "DELETE FROM entities WHERE id LIKE 'todo:%'",
        ),
    ]:
        if dry_run:
            count_sql = sql.replace("DELETE FROM", "SELECT COUNT(*) FROM", 1)
            row = cortex.execute(count_sql).fetchone()
            counts[label] = row[0] if row else 0
        else:
            cursor = cortex.execute(sql)
            counts[label] = cursor.rowcount

    if not dry_run:
        cortex.commit()

    return counts


def phase1_normalize_todos(
    todos: sqlite3.Connection, *, dry_run: bool
) -> dict[str, int]:
    """Normalize priority, status, and domain values in todos.db."""
    counts: dict[str, int] = {}

    all_rows = todos.execute(
        "SELECT id, priority, status, domain FROM todos"
    ).fetchall()

    priority_changes = 0
    status_changes = 0
    domain_changes = 0

    for row in all_rows:
        tid, raw_pri, raw_stat, raw_dom = row
        new_pri = normalize_priority(raw_pri)
        new_stat = normalize_status(raw_stat)
        new_dom = normalize_domain(raw_dom)

        if new_pri != raw_pri:
            priority_changes += 1
            if not dry_run:
                todos.execute(
                    "UPDATE todos SET priority = ? WHERE id = ?", (new_pri, tid)
                )
        if new_stat != raw_stat:
            status_changes += 1
            if not dry_run:
                todos.execute(
                    "UPDATE todos SET status = ? WHERE id = ?", (new_stat, tid)
                )
        if new_dom != raw_dom:
            domain_changes += 1
            if not dry_run:
                todos.execute(
                    "UPDATE todos SET domain = ? WHERE id = ?", (new_dom, tid)
                )

    if not dry_run:
        todos.commit()

    counts["priority_normalized"] = priority_changes
    counts["status_normalized"] = status_changes
    counts["domain_normalized"] = domain_changes
    return counts


# ── Phase 2: Entity Creation ─────────────────────────────────────────────


def phase2_create_entities(
    cortex: sqlite3.Connection,
    todos: sqlite3.Connection,
    *,
    dry_run: bool,
) -> dict[str, int]:
    """Create todo entities from todos.db, extract spec files for rich todos."""
    rows = todos.execute(
        "SELECT id, title, status, priority, domain, description, refs, "
        "created_at, updated_at FROM todos"
    ).fetchall()

    counts = {"entities_created": 0, "specs_extracted": 0, "skipped_existing": 0}

    for row in rows:
        (
            tid,
            title,
            status_val,
            priority,
            domain,
            description,
            refs_json,
            created_at,
            updated_at,
        ) = row

        entity_id = f"todo:{tid}"
        desc = description or ""
        source_uri = None

        if needs_spec(desc, refs_json):
            spec_path = write_spec_file(tid, title, desc, dry_run=dry_run)
            source_uri = f"tasks/specs/{spec_path.name}"
            entity_desc = first_sentence(desc)
            counts["specs_extracted"] += 1
        else:
            entity_desc = desc

        attrs = json.dumps(
            {
                "priority": priority,
                "status": status_val,
                "domain": domain,
                "context": "code",
            }
        )

        if dry_run:
            existing = cortex.execute(
                "SELECT id FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if existing:
                counts["skipped_existing"] += 1
                continue
            counts["entities_created"] += 1
            print(
                f"  WOULD create: {entity_id} "
                f"[{priority}/{status_val}/{domain}] "
                f"spec={'yes' if source_uri else 'no'}"
            )
            continue

        try:
            cortex.execute(
                "INSERT INTO entities "
                "(id, type, name, description, status, attributes, source_uri, "
                "created_at, updated_at) "
                "VALUES (?, 'todo', ?, ?, 'confirmed', ?, ?, ?, ?)",
                (
                    entity_id,
                    title,
                    entity_desc,
                    attrs,
                    source_uri,
                    created_at,
                    updated_at,
                ),
            )
            counts["entities_created"] += 1
        except sqlite3.IntegrityError:
            counts["skipped_existing"] += 1
            print(f"  EXISTS: {entity_id}")

    if not dry_run:
        cortex.commit()

    return counts


# ── Main ──────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate todos.db → Cortex entities")
    parser.add_argument(
        "--execute", action="store_true", help="Actually execute (default: dry-run)"
    )
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2],
        default=None,
        help="Run only this phase (default: both)",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    if dry_run:
        print("=== DRY RUN (pass --execute to apply) ===\n")

    if not CORTEX_DB.exists():
        print(f"ERROR: cortex.db not found at {CORTEX_DB}", file=sys.stderr)
        sys.exit(1)
    if not TODOS_DB.exists():
        print(f"ERROR: todos.db not found at {TODOS_DB}", file=sys.stderr)
        sys.exit(1)

    cortex = _connect(CORTEX_DB)
    todos = _connect(TODOS_DB)

    try:
        if args.phase is None or args.phase == 1:
            print("── Phase 1: Delete stale v2.2 entities ──")
            stale = phase1_delete_stale_entities(cortex, dry_run=dry_run)
            for k, v in stale.items():
                action = "would delete" if dry_run else "deleted"
                print(f"  {action} {v} {k}")

            print("\n── Phase 1: Normalize todos.db ──")
            norm = phase1_normalize_todos(todos, dry_run=dry_run)
            for k, v in norm.items():
                action = "would change" if dry_run else "changed"
                print(f"  {action} {v} {k}")

        if args.phase is None or args.phase == 2:
            print("\n── Phase 2: Create entities ──")
            created = phase2_create_entities(cortex, todos, dry_run=dry_run)
            for k, v in created.items():
                print(f"  {k}: {v}")

        if not dry_run:
            print("\n✓ Migration complete")
        else:
            print("\n(dry run — no changes made)")
    finally:
        cortex.close()
        todos.close()


if __name__ == "__main__":
    main()
