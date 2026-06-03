#!/usr/bin/env python3
"""Trait-completeness certificate — post-052 / 1172-E rewrite.

**1172-E REWRITE** — replaces the pre-052 COALESCE-equivalence cert.  Migration
052 dropped ``entities.status``; the old cert's ``SELECT … status …`` and
``require_entities_status_column()`` guard are removed.  This script certifies
the post-052 state: trait columns present, ``status`` absent, and trait coverage
internally consistent.

Runs on a ``:memory:`` fixture or on ``~/.cortex/cortex.db`` (read-only, no
writes).  Exits 0 on PASS, 1 on FAIL.

Usage::

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

from cortex_store.status_trait_backfill import (  # noqa: E402
    count_graduated_null_lifecycle,
    count_null_confidence_band,
    run_trait_completeness_scan,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")
_DEFAULT_REPORT = (
    Path(__file__).resolve().parents[2]
    / "tmp/prompts/cortex-status-traits/trait-completeness-cert.md"
)

_REQUIRED_TRAIT_COLS: frozenset[str] = frozenset(
    {"lifecycle", "confidence_band", "adoption"}
)
_STATUS_COL = "status"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def run_cert(conn: sqlite3.Connection, db_path: str) -> tuple[bool, str]:
    """Post-052 trait-completeness certificate.

    Verifies:
      1. ``entities.status`` is ABSENT (migration 052 applied).
      2. Trait columns (lifecycle, confidence_band, adoption) are PRESENT.
      3. Trait coverage: null counts per column, band/lifecycle bucket distribution.
      4. entity-count smoke (total vs type breakdown).

    Returns (passed, report_markdown).
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entities'"
    ).fetchone():
        return (
            False,
            "# Trait-Completeness Certificate\n\n**FAIL** — `entities` table absent.\n",
        )

    cols = _columns(conn, "entities")

    status_absent = _STATUS_COL not in cols
    trait_cols_present = _REQUIRED_TRAIT_COLS <= cols

    counts = run_trait_completeness_scan(conn) if trait_cols_present else None
    total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]

    null_band_global = count_null_confidence_band(conn) if trait_cols_present else -1
    null_lc_graduated = (
        count_graduated_null_lifecycle(conn) if trait_cols_present else -1
    )
    null_lc_global = counts.null_lifecycle if counts else -1
    null_adp = counts.null_adoption_decisions if counts else -1

    band_buckets: dict[str, int] = {}
    lc_buckets: dict[str, int] = {}
    if trait_cols_present:
        band_buckets = {
            str(r[0] or "null"): r[1]
            for r in conn.execute(
                "SELECT confidence_band, COUNT(*) FROM entities GROUP BY confidence_band"
            )
        }
        lc_buckets = {
            str(r[0] or "null"): r[1]
            for r in conn.execute(
                "SELECT lifecycle, COUNT(*) FROM entities GROUP BY lifecycle"
            )
        }

    type_counts: dict[str, int] = {
        str(r[0]): r[1]
        for r in conn.execute("SELECT type, COUNT(*) FROM entities GROUP BY type")
    }

    passed = (
        status_absent
        and trait_cols_present
        and null_band_global == 0
        and null_lc_graduated == 0
        and null_adp == 0
    )

    verdict = "PASS" if passed else "FAIL"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        "# Trait-Completeness Certificate",
        "",
        f"**Generated:** {now}",
        f"**Database:** `{db_path}`",
        "**Rewrite:** 1172-E (post-052 trait-only; no COALESCE / no status column)",
        "**Lifecycle scope:** graduated entities (≥1 live non-staged assertion)",
        f"**Verdict:** **{verdict}**"
        + (
            " — post-052 trait state certified"
            if passed
            else " — post-052 state NOT clean (see checks below)"
        ),
        "",
        "## 1. Schema state",
        "",
        "| Check | Result | Required |",
        "|---|---|---|",
        f"| `entities.status` absent | {'yes ✓' if status_absent else '**NO — column still present**'} | yes |",
    ]
    for col in sorted(_REQUIRED_TRAIT_COLS):
        present = col in cols
        lines.append(f"| `{col}` present | {'yes ✓' if present else '**NO**'} | yes |")

    lines.extend(
        [
            "",
            "## 2. Trait NULL counts",
            "",
            "| Trait | NULL count | Scope | Threshold |",
            "|---|---|---|---|",
            f"| `confidence_band` | {null_band_global if null_band_global >= 0 else 'N/A'} | global | 0 |",
            f"| `lifecycle` | {null_lc_graduated if null_lc_graduated >= 0 else 'N/A'} | graduated (≥1 committed assertion) | 0 |",
            f"| `lifecycle` (informational) | {null_lc_global if null_lc_global >= 0 else 'N/A'} | global (staged-buffer exempt) | — |",
            f"| `adoption` (decisions only) | {null_adp if null_adp >= 0 else 'N/A'} | decisions | 0 |",
            "",
            "## 3. Bucket distribution",
            "",
            "### confidence_band",
            "",
            "| Bucket | Count |",
            "|---|---|",
        ]
    )
    for k, n in sorted(band_buckets.items()):
        lines.append(f"| `{k}` | {n} |")

    lines.extend(
        [
            "",
            "### lifecycle",
            "",
            "| Bucket | Count |",
            "|---|---|",
        ]
    )
    for k, n in sorted(lc_buckets.items()):
        lines.append(f"| `{k}` | {n} |")

    lines.extend(
        [
            "",
            "## 4. Entity counts",
            "",
            f"- Total: {total}",
            "",
            "| Type | Count |",
            "|---|---|",
        ]
    )
    for t, n in sorted(type_counts.items()):
        lines.append(f"| `{t}` | {n} |")

    lines.extend(["", "## 5. Verdict", ""])
    if passed:
        lines.extend(
            [
                "- **PASS** — entities.status dropped, all trait columns present and",
                "  populated.  Post-052 state certified.",
            ]
        )
    else:
        lines.extend(["- **FAIL** — one or more checks above failed."])
        if not status_absent:
            lines.append(
                "  - `entities.status` still present: migration 052 may not have run."
            )
        if not trait_cols_present:
            missing = sorted(_REQUIRED_TRAIT_COLS - cols)
            lines.append(f"  - Missing trait columns: {missing}")
        if null_band_global > 0:
            lines.append(
                f"  - `confidence_band` has {null_band_global} NULL rows (global)."
            )
        if null_lc_graduated > 0:
            lines.append(
                f"  - `lifecycle` has {null_lc_graduated} NULL rows on graduated entities "
                f"(global null lifecycle: {null_lc_global})."
            )

    return passed, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trait-completeness certificate (post-052 / 1172-E)"
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
