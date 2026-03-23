"""Migration 008: Convert todos from todos.db into Cortex entities with lifecycle events.

Reads all todos from the separate todos.db, creates:
- A 'todo' entity per todo
- A 'todo_created' event entity per todo
- subject_of relationships linking todo → creation event
- For done/resolved todos: completion event + precedes chain
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger("cortex-api.migration.008")


def migrate(conn: sqlite3.Connection) -> None:
    todos_db_path = Path(os.environ.get("TODOS_DB_PATH", "/data/cortex/todos.db"))
    if not todos_db_path.exists():
        logger.warning("todos.db not found at %s — skipping todo conversion", todos_db_path)
        return

    todos_conn = sqlite3.connect(str(todos_db_path))
    todos_conn.row_factory = sqlite3.Row
    try:
        todos = [dict(row) for row in todos_conn.execute("SELECT * FROM todos").fetchall()]
    finally:
        todos_conn.close()

    logger.info("Converting %d todos into Cortex entities", len(todos))

    for todo in todos:
        tid = todo["id"]
        title = todo["title"]
        desc = todo.get("description") or title
        domain = todo.get("domain", "")
        priority = todo.get("priority", "backlog")
        status = todo["status"]
        context = todo.get("context", "universal-llm-gateway")
        created_at = todo.get("created_at")

        entity_status = "deprecated" if status in ("done", "resolved") else "confirmed"

        attrs = json.dumps({
            "domain": domain,
            "priority": priority,
            "current_state": status,
            "context": context,
            "migrated_from": "todos_table",
            "original_id": tid,
        })

        conn.execute(
            "INSERT OR IGNORE INTO entities "
            "(id, type, name, description, status, attributes, created_at) "
            "VALUES (?, 'todo', ?, ?, ?, ?, ?)",
            (f"todo:{tid}", title, desc, entity_status, attrs, created_at),
        )

        event_attrs = json.dumps({
            "event_type": "occurrence",
            "trigger": "todo_created",
            "domain": domain,
            "source_session": "migration_008",
        })
        conn.execute(
            "INSERT OR IGNORE INTO entities "
            "(id, type, name, description, status, attributes, created_at) "
            "VALUES (?, 'event', ?, 'Todo created (migrated from todos table)', "
            "'confirmed', ?, ?)",
            (f"event:todo_{tid}_created", f"Created: {title}", event_attrs, created_at),
        )

        conn.execute(
            "INSERT INTO relationships (type, from_entity, to_entity, role, evidence) "
            "VALUES ('subject_of', ?, ?, 'creator', "
            "'Migrated from todos table in migration 008')",
            (f"todo:{tid}", f"event:todo_{tid}_created"),
        )

        if status in ("done", "resolved"):
            completed_attrs = json.dumps({
                "event_type": "occurrence",
                "trigger": "todo_completed",
                "domain": domain,
                "source_session": "migration_008",
            })
            conn.execute(
                "INSERT OR IGNORE INTO entities "
                "(id, type, name, description, status, attributes) "
                "VALUES (?, 'event', ?, "
                "'Todo completed (migrated from todos table)', 'confirmed', ?)",
                (f"event:todo_{tid}_completed", f"Completed: {title}", completed_attrs),
            )

            conn.execute(
                "INSERT INTO relationships "
                "(type, from_entity, to_entity, role, evidence) "
                "VALUES ('subject_of', ?, ?, 'completer', "
                "'Migrated from todos table in migration 008')",
                (f"todo:{tid}", f"event:todo_{tid}_completed"),
            )

            conn.execute(
                "INSERT INTO relationships "
                "(type, from_entity, to_entity, evidence) "
                "VALUES ('precedes', ?, ?, "
                "'Todo lifecycle: created → completed')",
                (f"event:todo_{tid}_created", f"event:todo_{tid}_completed"),
            )

    logger.info("Todo conversion complete: %d todos processed", len(todos))
