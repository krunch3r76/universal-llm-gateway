from __future__ import annotations

import sqlite3


def _alias_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return [
        (str(row[0]), str(row[1]), str(row[2]))
        for row in conn.execute(
            """
            SELECT entities.id, entities.type, json_each.value
            FROM entities,
                 json_each(
                     CASE
                         WHEN json_valid(entities.aliases) THEN entities.aliases
                         ELSE '[]'
                     END
                 )
            WHERE entities.aliases IS NOT NULL
              AND json_each.type = 'text'
            """
        ).fetchall()
    ]


def _duplicate_aliases(rows: list[tuple[str, str, str]]) -> list[dict[str, object]]:
    by_type_alias: dict[tuple[str, str], list[str]] = {}
    by_entity_alias: dict[tuple[str, str], int] = {}
    duplicates: list[dict[str, object]] = []

    for entity_id, entity_type, alias in rows:
        by_type_alias.setdefault((entity_type, alias), []).append(entity_id)
        key = (entity_id, alias)
        by_entity_alias[key] = by_entity_alias.get(key, 0) + 1

    for (entity_type, alias), entity_ids in sorted(by_type_alias.items()):
        unique_ids = sorted(set(entity_ids))
        if len(unique_ids) > 1:
            duplicates.append(
                {
                    "kind": "cross_entity",
                    "entity_type": entity_type,
                    "alias": alias,
                    "entity_ids": unique_ids,
                }
            )
    for (entity_id, alias), count in sorted(by_entity_alias.items()):
        if count > 1:
            duplicates.append(
                {
                    "kind": "within_entity",
                    "entity_id": entity_id,
                    "alias": alias,
                    "count": count,
                }
            )
    return duplicates


def migrate(conn: sqlite3.Connection) -> None:
    rows = _alias_rows(conn)
    duplicates = _duplicate_aliases(rows)
    if duplicates:
        raise RuntimeError(
            "Cannot create entity_aliases uniqueness index until alias "
            f"collisions are resolved: {duplicates[:20]}"
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_aliases (
            entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            alias TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (entity_id, alias),
            UNIQUE (entity_type, alias)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_entity_aliases_alias ON entity_aliases(alias)"
    )
    conn.execute("DELETE FROM entity_aliases")
    conn.executemany(
        "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, ?, ?)",
        rows,
    )
