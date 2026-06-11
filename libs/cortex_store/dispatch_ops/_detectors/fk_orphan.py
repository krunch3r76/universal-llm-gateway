"""Foreign-key orphan detector — child rows referencing missing entity parents."""

from __future__ import annotations

from typing import Any

from ...entity_id_registry import entity_id_references
from ._shared import _finding


def detect_foreign_key_orphan(conn, subject: str | None = None) -> list[dict[str, Any]]:
    """Surface entity FK orphans via registry NOT-IN scan (foreign_keys=ON)."""
    locations: dict[str, list[str]] = {}

    for ref in entity_id_references():
        if ref.kind != "fk":
            continue
        rows = conn.execute(
            f"SELECT DISTINCT {ref.column} FROM {ref.table} "
            f"WHERE {ref.column} NOT IN (SELECT id FROM entities)"
        ).fetchall()
        for row in rows:
            orphan_id = row[0]
            if not orphan_id:
                continue
            if subject and orphan_id != subject:
                continue
            locations.setdefault(orphan_id, []).append(f"{ref.table}.{ref.column}")

    findings: list[dict[str, Any]] = []
    for orphan_id, refs in sorted(locations.items()):
        ref_list = ", ".join(sorted(refs))
        findings.append(
            _finding(
                "foreign_key_orphan",
                orphan_id,
                f"Missing entity {orphan_id} referenced from {ref_list}",
            )
        )
    return findings


__all__ = ["detect_foreign_key_orphan"]
