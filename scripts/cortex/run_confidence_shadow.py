#!/usr/bin/env python3
"""Run the shadow confidence-derivation batch over a cortex DB.

Reproducible entrypoint for the Phase 1/Phase 2 shadow derivation
(``decision:cortex-confidence-derivation-policy-v1`` → v2). Report-only by
default — it computes Φ*, the bands, and the §16 rule-vs-rule shadow diff
WITHOUT touching the DB. Pass ``--persist`` to write the migration-050 trait
columns (``confidence_band``/``confidence_score``) for in-scope entities;
``entities.status`` is NEVER flipped (Phase 1/2 are shadow-only).

Usage:
  python scripts/cortex/run_confidence_shadow.py [--db PATH] [--persist] [--top N]

The diff markdown is printed to stdout for capture into a thread report.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter

# Allow ``python scripts/cortex/run_confidence_shadow.py`` without PYTHONPATH set.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from cortex_store.confidence_derivation import (  # noqa: E402
    persist_traits,
    run_shadow_derivation,
)
from cortex_store.confidence_shadow_diff import (  # noqa: E402
    compute_shadow_diff,
    render_markdown,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")


def _gate_reason_breakdown(run) -> Counter:
    """Count gate-pass reasons across in-scope entities (v2 calibration view)."""
    reasons: Counter = Counter()
    for r in run.results.values():
        if r.in_scope and r.gate_pass:
            reasons[r.gate_reason] += 1
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Run shadow confidence derivation")
    parser.add_argument("--db", default=_DEFAULT_DB, help="cortex DB path")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Write confidence_band/score traits (shadow; never flips status)",
    )
    parser.add_argument("--top", type=int, default=0, help="Print N confirmed samples")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    run = run_shadow_derivation(conn)
    report = compute_shadow_diff(run)

    print(render_markdown(report))

    in_scope = [r for r in run.results.values() if r.in_scope]
    derived_confirmed = [r for r in in_scope if r.final_band == "confirmed"]
    print("\n## v2 calibration extras")
    print(f"- policy: `{run.policy_version}`")
    print(f"- in-scope entities: {len(in_scope)}")
    print(f"- derived-confirmed (final_band): {len(derived_confirmed)}")
    print("- gate-pass reason breakdown:")
    for reason, n in _gate_reason_breakdown(run).most_common():
        print(f"  - {reason}: {n}")

    if args.top and derived_confirmed:
        print(f"\n- sample confirmed (up to {args.top}):")
        for r in derived_confirmed[: args.top]:
            print(f"  - {r.entity_id} score={r.score:.3f} reason={r.gate_reason}")

    if args.persist:
        written = persist_traits(conn, run)
        conn.commit()
        print(
            f"\n[persisted] confidence traits written for {written} in-scope entities"
        )
    else:
        print("\n[report-only] no DB writes (pass --persist to write traits)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
