#!/usr/bin/env python3
"""Reconcile restored orphan-parent entities against live replacements.

Thread 1549 implementation — deprecate-default, keep-active when no replacement,
no merges. Disposition table is curated (semantic matching), not blind name join.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cortex_store.db import cortex_conn

CORTEX_HOME = Path.home() / ".cortex"
FK_REPAIR_BACKUP = CORTEX_HOME / "cortex.db.bak-fk-repair-20260610-154051"
PREWIPE_BACKUP = CORTEX_HOME / "cortex.db.bak-2026-03-29-pre-wipe"
RESTORE_BACKUP = CORTEX_HOME / "cortex.db.bak-orphan-restore-20260610-234416"


@dataclass(frozen=True)
class Disposition:
    entity_id: str
    replacement_id: str | None
    confidence: str
    disposition: str


# Curated per-id dispositions (thread 1549 / semantic judgment).
DISPOSITIONS: tuple[Disposition, ...] = (
    Disposition("artifact:cortex-workbench", None, "high", "deprecated-no-edge"),
    Disposition("discovery:rag-upsert-source-path", None, "low", "kept-active-flagged"),
    Disposition("document:journal-entry-121", None, "low", "kept-active-flagged"),
    Disposition("document:journal-entry-122", None, "low", "kept-active-flagged"),
    Disposition("dream:2026-03-19", None, "high", "deprecated-no-edge"),
    Disposition("event:503-routing-bug-resolved", None, "medium", "deprecated-no-edge"),
    Disposition(
        "event:capsule-application-submitted-2026-03-18",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition("event:car-purchase-note", None, "low", "kept-active-flagged"),
    Disposition("event:critical-10-day-window", None, "medium", "deprecated-no-edge"),
    Disposition(
        "event:fred-death",
        "event:death-of-fred-mansubi-2024-03-10",
        "high",
        "deprecated+lineage",
    ),
    Disposition(
        "event:green-tea-extract-ingestion-2026-03-18",
        None,
        "medium",
        "deprecated-no-edge",
    ),
    Disposition("event:journal-bridge-live", None, "medium", "deprecated-no-edge"),
    Disposition(
        "event:kaywan-chills-surface-heat-2026-03-16",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition(
        "event:kaywan-sherwin-house-finance-conversation-2026-03-16",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition("event:labcorp-777-knowles-closed", None, "low", "kept-active-flagged"),
    Disposition(
        "event:labcorp-test-2026-03-20",
        "medical:drug-screen-2026-03-20",
        "high",
        "deprecated+lineage",
    ),
    Disposition(
        "event:labcorp-urine-test-2026-03",
        "medical_result:carbon-health-urinalysis-2026-02-28",
        "high",
        "deprecated+lineage",
    ),
    Disposition("event:life-insurance-surrender", None, "medium", "deprecated-no-edge"),
    Disposition(
        "event:mehri-death", "person:mehri-mary-mansubi", "medium", "deprecated+lineage"
    ),
    Disposition("event:mehri-scammed", None, "medium", "deprecated-no-edge"),
    Disposition("event:met-lauren-2026-03-19", None, "low", "kept-active-flagged"),
    Disposition("event:osaic-letter-delivered", None, "medium", "deprecated-no-edge"),
    Disposition("event:paper-call-001", None, "medium", "deprecated-no-edge"),
    Disposition("event:safe-deposit-box-closed", None, "medium", "deprecated-no-edge"),
    Disposition("event:shakiba-call-2026-03-18", None, "low", "kept-active-flagged"),
    Disposition(
        "event:sherwin-asthma-attack-2026-03-16", None, "low", "kept-active-flagged"
    ),
    Disposition("event:testing-anomaly-place-1", None, "low", "kept-active-flagged"),
    Disposition(
        "event:ups-store-behavioral-shift", None, "medium", "deprecated-no-edge"
    ),
    Disposition("event:web-claude-first-session", None, "medium", "deprecated-no-edge"),
    Disposition(
        "job_application:costco-pharmacist", None, "low", "kept-active-flagged"
    ),
    Disposition("medical:drug-screen-2026-03-20", None, "low", "kept-active-flagged"),
    Disposition(
        "medical_result:carbon-health-urinalysis-2026-02-28",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition(
        "memory:mom-asset-division-discussions", None, "high", "deprecated-no-edge"
    ),
    Disposition(
        "observation:wifi-password-asymmetry", None, "high", "deprecated-no-edge"
    ),
    Disposition(
        "opportunity:oshaughnessy-fellowship", None, "low", "kept-active-flagged"
    ),
    Disposition("pattern:sherwin-asset-dismissal", None, "high", "deprecated-no-edge"),
    Disposition("payment:chase-cc-0727-2026-03-22", None, "low", "kept-active-flagged"),
    Disposition("payment:chase-cc-0780-2026-03-22", None, "low", "kept-active-flagged"),
    Disposition(
        "payment:chase-mortgage-8787-2026-03-25", None, "low", "kept-active-flagged"
    ),
    Disposition("person:avery-rph-on-the-go", None, "low", "kept-active-flagged"),
    Disposition("person:dawn-gray", None, "low", "kept-active-flagged"),
    Disposition(
        "person:fereydoun",
        "person:fereydun-fred-mansubi",
        "medium",
        "deprecated+lineage",
    ),
    Disposition(
        "person:fiona-walker", "person:fiona-amitychem", "medium", "deprecated+lineage"
    ),
    Disposition("person:hana-le", None, "low", "kept-active-flagged"),
    Disposition("person:helen-yu", "person:helen-bhrad", "high", "deprecated+lineage"),
    Disposition("person:jessica-huynh", None, "low", "kept-active-flagged"),
    Disposition(
        "person:katharine-zacher", "person:katharine", "medium", "deprecated+lineage"
    ),
    Disposition("person:lauren", None, "low", "kept-active-flagged"),
    Disposition("person:mary-ann", "person:mary-mansubi", "high", "deprecated+lineage"),
    Disposition("person:matt-nathan", None, "low", "kept-active-flagged"),
    Disposition(
        "person:mehri", "person:mehri-mary-mansubi", "high", "deprecated+lineage"
    ),
    Disposition("person:mohib", None, "low", "kept-active-flagged"),
    Disposition("person:par", "person:par-escandari", "high", "deprecated+lineage"),
    Disposition(
        "person:rajnish-rai", "person:rajnish-recruiter", "medium", "deprecated+lineage"
    ),
    Disposition("person:rebecca", None, "low", "kept-active-flagged"),
    Disposition("person:roy-bowden", None, "low", "kept-active-flagged"),
    Disposition("person:saeid-moliahe", None, "low", "kept-active-flagged"),
    Disposition(
        "person:shakiba", "person:shakiba-firouzi", "high", "deprecated+lineage"
    ),
    Disposition("person:shakiba-firouzi", None, "low", "kept-active-flagged"),
    Disposition("person:silvia-walker", None, "low", "kept-active-flagged"),
    Disposition(
        "statement:att-phone-8578-2026-02-17", None, "low", "kept-active-flagged"
    ),
    Disposition(
        "statement:bofa-credit_card-7200-2026-03-04", None, "low", "kept-active-flagged"
    ),
    Disposition(
        "statement:chase-checking-9733-2026-01-22",
        "statement:chase-checking-9733-2026-03-19",
        "high",
        "deprecated+lineage",
    ),
    Disposition(
        "statement:chase-checking-9733-2026-02-20",
        "statement:chase-checking-9733-2026-03-19",
        "high",
        "deprecated+lineage",
    ),
    Disposition(
        "statement:chase-checking-9733-2026-03-19", None, "low", "kept-active-flagged"
    ),
    Disposition(
        "statement:chase-credit_card-0480-2026-03-15",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition(
        "statement:chase-credit_card-0727-2026-03-03",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition(
        "statement:chase-credit_card-0780-2026-03-02",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition(
        "statement:discover-credit_card-2606-2026-03-06",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition(
        "statement:pge-utility-84-9-2026-02-11",
        "statement:pge-utility-84-9-2026-03-16",
        "high",
        "deprecated+lineage",
    ),
    Disposition(
        "statement:pge-utility-84-9-2026-03-16", None, "low", "kept-active-flagged"
    ),
    Disposition(
        "statement:schwab-individual-9921-2026-01-31",
        "statement:schwab-individual-9921-2026-02-28",
        "high",
        "deprecated+lineage",
    ),
    Disposition(
        "statement:schwab-individual-9921-2026-02-28",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition(
        "statement:wells-fargo-credit_card-9062-2026-02-26",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition(
        "statement:wells-fargo-ploc-4290-2026-03-06", None, "low", "kept-active-flagged"
    ),
    Disposition(
        "statement:west-valley-collection-rec-utility-3914-2026-03-31",
        None,
        "low",
        "kept-active-flagged",
    ),
    Disposition("system:orion", None, "low", "kept-active-flagged"),
    Disposition("trade:spx-0dte-2026-03-25", None, "low", "kept-active-flagged"),
    Disposition("trade:template", None, "low", "kept-active-flagged"),
)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def discover_restored_ids() -> list[str]:
    src = sqlite3.connect(str(FK_REPAIR_BACKUP))
    src.execute(f"ATTACH DATABASE '{RESTORE_BACKUP}' AS pre_restore")
    src.execute(f"ATTACH DATABASE '{PREWIPE_BACKUP}' AS prewipe")
    orphan_refs: set[str] = set()
    for sql in (
        "SELECT DISTINCT entity_id FROM entity_salience_cache",
        "SELECT DISTINCT from_entity FROM relationships",
        "SELECT DISTINCT to_entity FROM relationships",
    ):
        orphan_refs |= {row[0] for row in src.execute(sql)}
    missing = sorted(
        eid
        for eid in orphan_refs
        if not src.execute(
            "SELECT 1 FROM pre_restore.entities WHERE id=?", (eid,)
        ).fetchone()
    )
    return sorted(
        eid
        for eid in missing
        if src.execute("SELECT 1 FROM prewipe.entities WHERE id=?", (eid,)).fetchone()
    )


def _relationship_exists(
    conn: sqlite3.Connection, source_id: str, target_id: str, rel_type: str
) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM relationships WHERE from_entity=? AND to_entity=? "
            "AND type=? AND valid_until IS NULL AND active=1",
            (source_id, target_id, rel_type),
        ).fetchone()
    )


def apply(conn: sqlite3.Connection, *, dry_run: bool) -> dict[str, object]:
    restored = discover_restored_ids()
    table_ids = {d.entity_id for d in DISPOSITIONS}
    if table_ids != set(restored):
        missing = sorted(set(restored) - table_ids)
        extra = sorted(table_ids - set(restored))
        raise RuntimeError(
            f"Disposition table mismatch missing={missing} extra={extra}"
        )

    now_iso = _now_iso()
    stats = {
        "deprecated": 0,
        "lineage_edges": 0,
        "kept_active": 0,
        "already_deprecated": 0,
    }
    rows: list[dict[str, object]] = []

    conn.execute("BEGIN IMMEDIATE")
    try:
        for disp in DISPOSITIONS:
            row = conn.execute(
                "SELECT lifecycle FROM entities WHERE id=?", (disp.entity_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Missing entity: {disp.entity_id}")

            applied = "unchanged"
            if disp.disposition.startswith("deprecated"):
                if row[0] == "deprecated":
                    stats["already_deprecated"] += 1
                    applied = "already-deprecated"
                elif not dry_run:
                    conn.execute(
                        "UPDATE entities SET lifecycle='deprecated', updated_at=? WHERE id=?",
                        (now_iso, disp.entity_id),
                    )
                    stats["deprecated"] += 1
                    applied = "deprecated"
                else:
                    applied = "would-deprecate"
                    stats["deprecated"] += 1

                if disp.disposition == "deprecated+lineage" and disp.replacement_id:
                    rep = conn.execute(
                        "SELECT 1 FROM entities WHERE id=?", (disp.replacement_id,)
                    ).fetchone()
                    if rep is None:
                        raise RuntimeError(
                            f"Replacement missing: {disp.replacement_id} for {disp.entity_id}"
                        )
                    if not _relationship_exists(
                        conn, disp.entity_id, disp.replacement_id, "succeeded_by"
                    ):
                        if not dry_run:
                            conn.execute(
                                "INSERT INTO relationships "
                                "(type, from_entity, to_entity, role, strength, evidence, "
                                "created_at, updated_at, active) VALUES (?,?,?,?,?,?,?,?,1)",
                                (
                                    "succeeded_by",
                                    disp.entity_id,
                                    disp.replacement_id,
                                    None,
                                    1.0,
                                    "thread-1549 reconcile: restored entity superseded by replacement",
                                    now_iso,
                                    now_iso,
                                ),
                            )
                        stats["lineage_edges"] += 1
            else:
                stats["kept_active"] += 1
                applied = "kept-active"

            rows.append(
                {
                    "id": disp.entity_id,
                    "replacement_id": disp.replacement_id,
                    "confidence": disp.confidence,
                    "disposition": disp.disposition,
                    "applied": applied,
                }
            )

        if dry_run:
            conn.rollback()
        else:
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                raise RuntimeError(f"FK violations: {fk[:5]}")
            conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"rows": rows, "stats": stats, "restored_count": len(restored)}


def verify(
    conn: sqlite3.Connection, rows: list[dict[str, object]]
) -> dict[str, object]:
    deprecated_ids = [
        r["id"] for r in rows if str(r["disposition"]).startswith("deprecated")
    ]
    current_hits = [
        eid
        for eid in deprecated_ids
        if conn.execute("SELECT 1 FROM current_entities WHERE id=?", (eid,)).fetchone()
    ]
    fk_count = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    spot_checks = []
    for eid in (
        "event:fred-death",
        "person:shakiba",
        "statement:chase-checking-9733-2026-01-22",
    ):
        raw = conn.execute(
            "SELECT lifecycle FROM entities WHERE id=?", (eid,)
        ).fetchone()
        in_current = conn.execute(
            "SELECT 1 FROM current_entities WHERE id=?", (eid,)
        ).fetchone()
        spot_checks.append(
            {
                "id": eid,
                "lifecycle": raw[0] if raw else None,
                "in_current_entities": bool(in_current),
            }
        )
    return {
        "foreign_key_check": fk_count,
        "deprecated_in_current_entities": current_hits,
        "spot_checks": spot_checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    conn = cortex_conn()
    try:
        result = apply(conn, dry_run=args.dry_run)
        if not args.dry_run:
            result["verify"] = verify(conn, result["rows"])
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps(result["stats"], indent=2))
            if result.get("verify"):
                print(json.dumps(result["verify"], indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
