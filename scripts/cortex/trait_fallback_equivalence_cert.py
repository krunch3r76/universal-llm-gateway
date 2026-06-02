#!/usr/bin/env python3
"""Trait-fallback equivalence certificate (plan 686612ed / gate 4511b3f6).

Compares COALESCE fallback predicates vs trait-only ID sets and stats buckets on
a live cortex database. Writes report to tmp/prompts/cortex-status-traits/.

Usage:
  ~/.venvs/universal/bin/python scripts/cortex/trait_fallback_equivalence_cert.py
  ~/.venvs/universal/bin/python scripts/cortex/trait_fallback_equivalence_cert.py \\
      --db ~/.cortex/cortex.db --report /path/to/cert.md
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")
_DEFAULT_REPORT = (
    Path(__file__).resolve().parents[2]
    / "tmp/prompts/cortex-status-traits/trait-fallback-equivalence-cert.md"
)

CONF_BAND = ("unsubstantiated", "provisional", "confirmed")
LIFECYCLE = (
    "active",
    "superseded",
    "merged",
    "deprecated",
    "reaped",
    "invalidated",
    "dismissed",
)
LIFECYCLE_LEGACY = ("merged", "deprecated", "reaped")


def _sym_diff(
    conn: sqlite3.Connection,
    old_sql: str,
    new_sql: str,
    old_params: tuple[object, ...] = (),
    new_params: tuple[object, ...] = (),
) -> tuple[int, int]:
    old_ids = {
        r[0]
        for r in conn.execute(f"SELECT id FROM entities WHERE {old_sql}", old_params)
    }
    new_ids = {
        r[0]
        for r in conn.execute(f"SELECT id FROM entities WHERE {new_sql}", new_params)
    }
    return len(old_ids - new_ids), len(new_ids - old_ids)


def _buckets(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {str(r[0] or "null"): r[1] for r in conn.execute(sql)}


def run_cert(conn: sqlite3.Connection, db_path: str) -> tuple[bool, str]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entities)").fetchall()}
    trait_cols = {"lifecycle", "confidence_band", "adoption"}
    columns_ok = trait_cols <= cols

    field_map = {
        r["entity_type"]: r["confidence_field"]
        for r in conn.execute(
            "SELECT entity_type, confidence_field FROM type_confidence_fields"
        )
    }

    def is_status_axis(entity_type: str) -> bool:
        field = field_map.get(entity_type, "confidence_band")
        return field in ("status", "confidence_band")

    all_rows = conn.execute(
        "SELECT id, type, status, confidence_band, lifecycle, adoption FROM entities"
    ).fetchall()

    band_gaps_all = sum(
        1 for r in all_rows if r["confidence_band"] is None and r["status"] in CONF_BAND
    )
    band_gaps_sa = sum(
        1
        for r in all_rows
        if is_status_axis(r["type"])
        and r["confidence_band"] is None
        and r["status"] in CONF_BAND
    )
    lc_gaps_all = sum(
        1 for r in all_rows if r["lifecycle"] is None and r["status"] in LIFECYCLE
    )
    lc_gaps_sa = sum(
        1
        for r in all_rows
        if is_status_axis(r["type"])
        and r["lifecycle"] is None
        and r["status"] in LIFECYCLE
    )
    dec_gaps = sum(
        1
        for r in all_rows
        if r["type"] == "decision" and r["adoption"] is None and r["status"] is not None
    )

    pred_rows: list[tuple[str, int, int, bool]] = []
    for band in CONF_BAND:
        oo, on = _sym_diff(
            conn,
            "(confidence_band = ? OR (confidence_band IS NULL AND status = ?))",
            "confidence_band = ?",
            (band, band),
            (band,),
        )
        pred_rows.append((f"confidence_band={band}", oo, on, oo == 0 and on == 0))

    for val in LIFECYCLE_LEGACY:
        oo, on = _sym_diff(
            conn,
            "(lifecycle IS NULL OR lifecycle != ?) AND "
            "(lifecycle IS NOT NULL OR status IS NULL OR status != ?)",
            "(lifecycle IS NULL OR lifecycle != ?)",
            (val, val),
            (val,),
        )
        pred_rows.append((f"lifecycle_not={val}", oo, on, oo == 0 and on == 0))
        oo, on = _sym_diff(
            conn,
            "(lifecycle = ? OR (lifecycle IS NULL AND status = ?))",
            "lifecycle = ?",
            (val, val),
            (val,),
        )
        pred_rows.append((f"lifecycle_is={val}", oo, on, oo == 0 and on == 0))

    oo, on = _sym_diff(
        conn,
        "(adoption IN ('proposed','adopted') OR "
        "(adoption IS NULL AND status IN ('confirmed')))",
        "adoption IN ('proposed','adopted')",
    )
    pred_rows.append(("adoption_in", oo, on, oo == 0 and on == 0))

    leg_lc = _buckets(
        conn,
        "SELECT COALESCE(lifecycle, CASE WHEN status IN "
        "('merged','deprecated','reaped') THEN status END) AS v, COUNT(*) "
        "FROM entities GROUP BY v",
    )
    trait_lc = _buckets(
        conn, "SELECT lifecycle, COUNT(*) FROM entities GROUP BY lifecycle"
    )
    leg_band = _buckets(
        conn,
        "SELECT COALESCE(confidence_band, CASE WHEN status IN "
        "('unsubstantiated','provisional','confirmed') THEN status END) AS v, "
        "COUNT(*) FROM entities GROUP BY v",
    )
    trait_band = _buckets(
        conn, "SELECT confidence_band, COUNT(*) FROM entities GROUP BY confidence_band"
    )

    stats_ok = leg_lc == trait_lc and leg_band == trait_band
    preds_ok = all(row[3] for row in pred_rows)
    completeness_ok = lc_gaps_sa == 0 and dec_gaps == 0 and band_gaps_all == 0

    null_status = conn.execute(
        "SELECT COUNT(*) FROM entities WHERE status IS NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    passed = columns_ok and preds_ok and stats_ok and completeness_ok
    verdict = "PASS" if passed else "FAIL"

    lines = [
        "# Trait-Fallback Equivalence Certificate",
        "",
        f"**Generated:** {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"**Database:** `{db_path}`",
        "**Plan:** gpt-5.5 reader pass (686612ed) / backfill gate (4511b3f6)",
        f"**Verdict:** **{verdict}**"
        + (
            " — COALESCE fallback may be stripped"
            if passed
            else " — COALESCE fallback MUST NOT be stripped"
        ),
        "",
        "## 1. Trait columns",
        "",
        "| Column | Present |",
        "|---|---|",
    ]
    for col in sorted(trait_cols):
        lines.append(f"| `{col}` | {'yes' if col in cols else 'NO'} |")

    lines.extend(
        [
            "",
            "## 2. Trait completeness",
            "",
            "| Check | All entities | Status-axis only | Threshold |",
            "|---|---|---|---|",
            f"| `confidence_band` NULL + status in band enum | {band_gaps_all} | "
            f"**{band_gaps_sa}** | 0 all (band in scope) |",
            f"| `lifecycle` NULL + status in lifecycle enum | {lc_gaps_all} | "
            f"**{lc_gaps_sa}** | 0 status-axis |",
            f"| `decision` + `adoption` NULL + status set | {dec_gaps} | {dec_gaps} | 0 |",
            "",
            "## 3. Predicate symmetric difference",
            "",
            "| Predicate | only_in_old | only_in_new | Pass |",
            "|---|---|---|---|",
        ]
    )
    for name, oo, on, ok in pred_rows:
        lines.append(f"| `{name}` | {oo} | {on} | {'yes' if ok else '**NO**'} |")

    lines.extend(["", "## 4. Stats bucket differential", ""])
    if stats_ok:
        lines.append("All lifecycle and confidence_band buckets match (Δ = 0).")
    else:
        lines.extend(
            [
                "### Lifecycle",
                "",
                "| Bucket | Legacy | Trait-only | Δ |",
                "|---|---|---|---|",
            ]
        )
        for k in sorted(set(leg_lc) | set(trait_lc)):
            l, t = leg_lc.get(k, 0), trait_lc.get(k, 0)
            if l != t:
                lines.append(f"| {k} | {l} | {t} | {t - l:+d} |")
        lines.extend(
            [
                "",
                "### Confidence band",
                "",
                "| Bucket | Legacy | Trait-only | Δ |",
                "|---|---|---|---|",
            ]
        )
        for k in sorted(set(leg_band) | set(trait_band)):
            l, t = leg_band.get(k, 0), trait_band.get(k, 0)
            if l != t:
                lines.append(f"| {k} | {l} | {t} | {t - l:+d} |")

    lines.extend(
        [
            "",
            "## 5. Post-writer-cutover rows",
            "",
            f"- Total entities: {total}",
            f"- Rows with `status IS NULL`: {null_status}",
        ]
    )

    if passed:
        lines.extend(
            [
                "",
                "## Action",
                "",
                "- **PASS** — reader pass Steps 1–8 (686612ed) may proceed.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Action",
                "",
                "- **STOP** — do NOT strip COALESCE in readers.",
                "- Re-run predicate-equivalence backfill and re-cert.",
            ]
        )

    return passed, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trait-fallback equivalence certificate"
    )
    parser.add_argument("--db", default=_DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=_DEFAULT_REPORT)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    passed, report = run_cert(conn, args.db)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report)
    print(report)
    print(f"Wrote {args.report}")
    print(f"VERDICT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
