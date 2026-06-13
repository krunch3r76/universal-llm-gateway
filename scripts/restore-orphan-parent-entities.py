#!/usr/bin/env python3
"""Restore hard-deleted orphan-parent entities from historical backup.

Discovery set: fk-repair backup child refs missing from live entities.
Row source: pre-wipe backup (entity rows absent from fk-repair backup).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cortex_store.cascade_hygiene import apply_reap_consistency_hygiene
from cortex_store.claim_hash import compute_claim_hash
from cortex_store.db import cortex_conn
from cortex_store.salience import compute_all_salience
from cortex_store.status_trait_write import write_entity_reaped

CORTEX_HOME = Path.home() / ".cortex"
LIVE_DB = CORTEX_HOME / "cortex.db"
FK_REPAIR_BACKUP = CORTEX_HOME / "cortex.db.bak-fk-repair-20260610-154051"
PREWIPE_BACKUP = CORTEX_HOME / "cortex.db.bak-2026-03-29-pre-wipe"

REAPED_PREFIXES = frozenset({"dream", "artifact", "memory", "observation", "pattern"})
REAPED_SUBSTRINGS = ("-probe", "throwaway", "test-")


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reap_entity(conn: sqlite3.Connection, entity_id: str, now_iso: str) -> None:
    conn.execute(
        "UPDATE assertions SET valid_until = ? "
        "WHERE entity_id = ? AND superseded_by IS NULL AND valid_until IS NULL",
        (now_iso, entity_id),
    )
    write_entity_reaped(conn, entity_id, now_iso)
    conn.execute(
        "UPDATE session_edges SET valid_until = ? "
        "WHERE (from_node = ? OR to_node = ?) AND valid_until IS NULL",
        (now_iso, entity_id, entity_id),
    )
    apply_reap_consistency_hygiene(conn, entity_id, now_iso)


def classify_entity(entity_id: str) -> tuple[str, str, bool]:
    """Return (lifecycle, class_label, borderline)."""
    prefix = entity_id.split(":", 1)[0]
    borderline = False
    if prefix in REAPED_PREFIXES:
        return "reaped", "scratch", borderline
    for marker in REAPED_SUBSTRINGS:
        if marker in entity_id:
            borderline = prefix == "event"
            return "reaped", "scratch", borderline
    return "active", "substantive", borderline


def discover_recovery_ids(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Entity ids referenced in fk-repair backup but missing from live."""
    entity_ids = sorted(
        {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT entity_id FROM entity_salience_cache "
                "WHERE entity_id NOT IN (SELECT id FROM live.entities)"
            )
        }
        | {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT from_entity FROM relationships "
                "WHERE from_entity NOT IN (SELECT id FROM live.entities)"
            )
        }
        | {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT to_entity FROM relationships "
                "WHERE to_entity NOT IN (SELECT id FROM live.entities)"
            )
        }
    )
    recoverable: list[str] = []
    unrecoverable: list[str] = []
    for entity_id in entity_ids:
        if conn.execute(
            "SELECT 1 FROM prewipe.entities WHERE id = ?", (entity_id,)
        ).fetchone():
            recoverable.append(entity_id)
        else:
            unrecoverable.append(entity_id)
    return recoverable, unrecoverable


def _map_entity_row(row: sqlite3.Row) -> dict[str, object]:
    data = dict(row)
    status = (data.pop("status", None) or "confirmed").lower()
    lifecycle = status if status in {"merged", "deprecated", "reaped"} else None
    confidence_band = None if lifecycle else status
    return {
        "id": data["id"],
        "type": data["type"],
        "name": data["name"],
        "description": data.get("description"),
        "notes": data.get("notes"),
        "aliases": data.get("aliases"),
        "attributes": data.get("attributes"),
        "source_uri": data.get("source_uri"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "content_hash": data.get("content_hash"),
        "retention_policy": "permanent",
        "retention_ttl_days": None,
        "last_accessed_at": None,
        "workflow_state": None,
        "lifecycle": lifecycle,
        "confidence_band": confidence_band,
        "confidence_score": None,
        "adoption": None,
    }


def _insert_entity(conn: sqlite3.Connection, mapped: dict[str, object]) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities ("
        "id, type, name, description, notes, aliases, attributes, source_uri, "
        "created_at, updated_at, content_hash, retention_policy, retention_ttl_days, "
        "last_accessed_at, workflow_state, lifecycle, confidence_band, "
        "confidence_score, adoption"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            mapped["id"],
            mapped["type"],
            mapped["name"],
            mapped["description"],
            mapped["notes"],
            mapped["aliases"],
            mapped["attributes"],
            mapped["source_uri"],
            mapped["created_at"],
            mapped["updated_at"],
            mapped["content_hash"],
            mapped["retention_policy"],
            mapped["retention_ttl_days"],
            mapped["last_accessed_at"],
            mapped["workflow_state"],
            mapped["lifecycle"],
            mapped["confidence_band"],
            mapped["confidence_score"],
            mapped["adoption"],
        ),
    )


def _insert_assertion(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    defer_superseded_by: bool = False,
    id_remap: dict[int, int] | None = None,
) -> int | None:
    data = dict(row)
    assertion_id = data["id"]
    if conn.execute(
        "SELECT 1 FROM assertions WHERE id = ?", (assertion_id,)
    ).fetchone():
        return None
    entity_id = data["entity_id"]
    claim = data["claim"]
    claim_hash = compute_claim_hash(entity_id, claim)
    existing = conn.execute(
        "SELECT id FROM assertions WHERE entity_id = ? AND claim_hash = ?",
        (entity_id, claim_hash),
    ).fetchone()
    if existing:
        if id_remap is not None:
            id_remap[assertion_id] = int(existing[0])
        return None
    chunk_id = data.get("chunk_id")
    if chunk_id is not None:
        chunk_id = str(chunk_id)
    superseded_by = None if defer_superseded_by else data.get("superseded_by")
    conn.execute(
        "INSERT INTO assertions ("
        "id, entity_id, claim, confidence, confidence_score, evidence, evidence_uris, "
        "derivation_type, chunk_id, reasoning_summary, extraction_run, is_atomic, "
        "is_decontextualized, observed_at, valid_from, valid_until, superseded_by, "
        "review_status, reviewer, reviewed_at, review_notes, created_at, updated_at, "
        "claim_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            assertion_id,
            entity_id,
            claim,
            data.get("confidence", "believed"),
            data.get("confidence_score"),
            data.get("evidence"),
            data.get("evidence_uris"),
            data.get("derivation_type", "inference"),
            chunk_id,
            data.get("reasoning_summary"),
            data.get("extraction_run"),
            data.get("is_atomic", True),
            data.get("is_decontextualized", True),
            data.get("observed_at"),
            data.get("valid_from"),
            data.get("valid_until"),
            superseded_by,
            data.get("review_status", "committed"),
            data.get("reviewer"),
            data.get("reviewed_at"),
            data.get("review_notes"),
            data.get("created_at"),
            data.get("updated_at"),
            claim_hash,
        ),
    )
    return assertion_id


def _apply_deferred_superseded_by(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    id_remap: dict[int, int],
) -> int:
    updated = 0
    for row in rows:
        superseded_by = dict(row).get("superseded_by")
        if superseded_by is None:
            continue
        assertion_id = row["id"]
        if not conn.execute(
            "SELECT 1 FROM assertions WHERE id = ?", (assertion_id,)
        ).fetchone():
            continue
        target = superseded_by
        while target in id_remap:
            target = id_remap[target]
        if not conn.execute(
            "SELECT 1 FROM assertions WHERE id = ?", (target,)
        ).fetchone():
            continue
        before = conn.total_changes
        conn.execute(
            "UPDATE assertions SET superseded_by = ? WHERE id = ? AND superseded_by IS NULL",
            (target, assertion_id),
        )
        if conn.total_changes > before:
            updated += 1
    return updated


def _insert_relationship(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    live_entity_ids: set[str],
) -> str | None:
    data = dict(row)
    rel_id = data["id"]
    if conn.execute("SELECT 1 FROM relationships WHERE id = ?", (rel_id,)).fetchone():
        return "exists"
    from_entity = data["from_entity"]
    to_entity = data["to_entity"]
    if from_entity not in live_entity_ids or to_entity not in live_entity_ids:
        return "skipped"
    conn.execute(
        "INSERT INTO relationships ("
        "id, type, from_entity, to_entity, role, strength, evidence, chunk_id, "
        "valid_from, valid_until, source_uri, created_at, updated_at, active"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (
            rel_id,
            data["type"],
            from_entity,
            to_entity,
            data.get("role"),
            data.get("strength", 1.0),
            data.get("evidence"),
            data.get("chunk_id"),
            data.get("valid_from"),
            data.get("valid_until"),
            data.get("source_uri"),
            data.get("created_at"),
            data.get("updated_at"),
        ),
    )
    return "restored"


def _insert_surface_form(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    data = dict(row)
    conn.execute(
        "INSERT OR IGNORE INTO surface_forms ("
        "id, entity_id, mention, mention_type, confidence, source_uri, "
        "created_at, updated_at"
        ") VALUES (?,?,?,?,?,?,?,?)",
        (
            data["id"],
            data["entity_id"],
            data["mention"],
            data.get("mention_type"),
            data.get("confidence"),
            data.get("source_uri"),
            data.get("created_at"),
            data.get("updated_at"),
        ),
    )


def build_manifest(source_conn: sqlite3.Connection) -> dict[str, object]:
    recoverable, unrecoverable = discover_recovery_ids(source_conn)
    child_counts: Counter[str] = Counter()
    dispositions: list[dict[str, object]] = []
    for entity_id in recoverable:
        lifecycle, class_label, borderline = classify_entity(entity_id)
        for table, sql in [
            (
                "assertions",
                "SELECT COUNT(*) FROM prewipe.assertions WHERE entity_id = ?",
            ),
            (
                "relationships",
                "SELECT COUNT(*) FROM prewipe.relationships "
                "WHERE from_entity = ? OR to_entity = ?",
            ),
            (
                "surface_forms",
                "SELECT COUNT(*) FROM prewipe.surface_forms WHERE entity_id = ?",
            ),
        ]:
            if table == "relationships":
                count = source_conn.execute(sql, (entity_id, entity_id)).fetchone()[0]
            else:
                count = source_conn.execute(sql, (entity_id,)).fetchone()[0]
            child_counts[table] += count
        dispositions.append(
            {
                "id": entity_id,
                "prefix": entity_id.split(":", 1)[0],
                "class": class_label,
                "lifecycle": lifecycle,
                "borderline": borderline,
            }
        )
    return {
        "entity_ids_total": len(recoverable) + len(unrecoverable),
        "recoverable_count": len(recoverable),
        "unrecoverable_count": len(unrecoverable),
        "unrecoverable_ids": unrecoverable,
        "child_counts": dict(child_counts),
        "prefix_buckets": dict(Counter(d["prefix"] for d in dispositions)),
        "dispositions": dispositions,
    }


def restore(
    source_conn: sqlite3.Connection,
    live_conn: sqlite3.Connection | None,
    *,
    dry_run: bool,
) -> dict[str, object]:
    manifest = build_manifest(source_conn)
    if dry_run:
        return {
            "manifest": manifest,
            "stats": {},
            "skipped_relationships": [],
        }

    assert live_conn is not None
    recoverable = [d["id"] for d in manifest["dispositions"]]
    live_entity_ids = {row[0] for row in live_conn.execute("SELECT id FROM entities")}
    stats: dict[str, object] = {
        "entities_inserted": 0,
        "assertions_inserted": 0,
        "assertions_skipped_duplicate": 0,
        "relationships_restored": 0,
        "relationships_skipped": 0,
        "surface_forms_inserted": 0,
        "reaped_applied": 0,
    }
    skipped_relationships: list[dict[str, str]] = []

    now_iso = _now_iso()
    live_conn.execute("BEGIN IMMEDIATE")
    try:
        for entity_id in recoverable:
            row = source_conn.execute(
                "SELECT * FROM prewipe.entities WHERE id = ?", (entity_id,)
            ).fetchone()
            if row is None:
                continue
            mapped = _map_entity_row(row)
            before = live_conn.total_changes
            _insert_entity(live_conn, mapped)
            if live_conn.total_changes > before:
                stats["entities_inserted"] += 1
            live_entity_ids.add(entity_id)

        assertion_rows = source_conn.execute(
            "SELECT * FROM prewipe.assertions WHERE entity_id IN ({}) ORDER BY id".format(
                ",".join("?" * len(recoverable))
            ),
            recoverable,
        ).fetchall()
        id_remap: dict[int, int] = {}
        for row in assertion_rows:
            inserted = _insert_assertion(
                live_conn, row, defer_superseded_by=True, id_remap=id_remap
            )
            if inserted is not None:
                stats["assertions_inserted"] += 1
            else:
                stats["assertions_skipped_duplicate"] += 1
        stats["superseded_by_linked"] = _apply_deferred_superseded_by(
            live_conn, assertion_rows, id_remap
        )

        for entity_id in recoverable:
            for row in source_conn.execute(
                "SELECT * FROM prewipe.relationships "
                "WHERE from_entity = ? OR to_entity = ? ORDER BY id",
                (entity_id, entity_id),
            ):
                outcome = _insert_relationship(live_conn, row, live_entity_ids)
                if outcome == "restored":
                    stats["relationships_restored"] += 1
                elif outcome == "skipped":
                    stats["relationships_skipped"] += 1
                    skipped_relationships.append(
                        {
                            "id": str(row["id"]),
                            "from": row["from_entity"],
                            "to": row["to_entity"],
                        }
                    )

        for entity_id in recoverable:
            for row in source_conn.execute(
                "SELECT * FROM prewipe.surface_forms WHERE entity_id = ? ORDER BY id",
                (entity_id,),
            ):
                before = live_conn.total_changes
                _insert_surface_form(live_conn, row)
                if live_conn.total_changes > before:
                    stats["surface_forms_inserted"] += 1

        for disp in manifest["dispositions"]:
            entity_id = disp["id"]
            if disp["lifecycle"] == "active":
                live_conn.execute(
                    "UPDATE entities SET lifecycle = 'active', updated_at = ? WHERE id = ?",
                    (now_iso, entity_id),
                )
            else:
                _reap_entity(live_conn, entity_id, now_iso)
                stats["reaped_applied"] += 1

        fk_violations = live_conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            raise RuntimeError(f"FK violations before commit: {fk_violations[:5]}")
        live_conn.commit()
    except Exception:
        live_conn.rollback()
        raise

    compute_all_salience(live_conn, force=True)
    live_conn.commit()

    return {
        "manifest": manifest,
        "stats": stats,
        "skipped_relationships": skipped_relationships,
        "fk_violations_post": len(
            live_conn.execute("PRAGMA foreign_key_check").fetchall()
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Manifest only")
    parser.add_argument("--execute", action="store_true", help="Run restore")
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    args = parser.parse_args()
    if args.dry_run == args.execute:
        parser.error("Specify exactly one of --dry-run or --execute")

    for path in (FK_REPAIR_BACKUP, PREWIPE_BACKUP, LIVE_DB):
        if not path.exists():
            print(f"Missing required DB: {path}", file=sys.stderr)
            return 1

    source_conn = sqlite3.connect(FK_REPAIR_BACKUP)
    source_conn.row_factory = sqlite3.Row
    source_conn.execute(f"ATTACH DATABASE '{LIVE_DB}' AS live")
    source_conn.execute(f"ATTACH DATABASE '{PREWIPE_BACKUP}' AS prewipe")

    backup_path: str | None = None
    if args.execute:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_path = str(CORTEX_HOME / f"cortex.db.bak-orphan-restore-{stamp}")
        shutil.copy2(LIVE_DB, backup_path)

    live_conn = cortex_conn() if args.execute else None
    try:
        result = restore(source_conn, live_conn, dry_run=args.dry_run)
        if backup_path:
            result["fresh_backup_path"] = backup_path
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            manifest = result["manifest"]
            print(f"Recoverable entities: {manifest['recoverable_count']}")
            print(f"Unrecoverable entities: {manifest['unrecoverable_count']}")
            if manifest["unrecoverable_ids"]:
                print("Unrecoverable:", ", ".join(manifest["unrecoverable_ids"]))
            print("Child counts:", manifest["child_counts"])
            if args.execute:
                print("Fresh backup:", result.get("fresh_backup_path"))
                print("Stats:", result["stats"])
                print("FK violations post:", result["fk_violations_post"])
    finally:
        source_conn.close()
        if live_conn is not None:
            live_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
