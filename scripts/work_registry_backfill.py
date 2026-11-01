#!/usr/bin/env python3
"""Backfill seat_name, address, and caller_agent on dispatch ledger rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for entry in (
    _REPO_ROOT,
    _REPO_ROOT / "libs",
    _REPO_ROOT / "services" / "universal-stargate",
):
    text = str(entry)
    if text not in sys.path:
        sys.path.insert(0, text)

from services.git_integration_worker.cursor_dispatch_ledger import (  # noqa: E402
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_work_registry_admit import (  # noqa: E402
    build_cursor_sdk_registry_address,
    record_dispatch_registry_best_effort,
    resolve_registry_seat_name,
)


def _parse_record_json(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _worktree_from_record(rec: dict, row) -> str | None:
    for key in ("dispatch_workspace", "lane_worktree", "worktree"):
        val = rec.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    lease = row["lease_key"] if "lease_key" in row.keys() else None
    if isinstance(lease, str) and "/lane-" in lease:
        return lease
    return None


def scan(*, apply: bool) -> dict[str, int]:
    ledger = CursorDispatchLedger.instance()
    stats = {
        "scanned": 0,
        "would_update": 0,
        "updated": 0,
        "skipped_claim": 0,
        "skipped_complete": 0,
    }
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, thread_id, caller_agent, work_key, status, "
            "contract, record_json, lease_key FROM cursor_sdk_dispatches "
            "WHERE COALESCE(contract,'') != 'claim' "
            "ORDER BY rowid ASC"
        ).fetchall()
    for row in rows:
        stats["scanned"] += 1
        if str(row["status"]) == "claimed":
            stats["skipped_claim"] += 1
            continue
        rec = _parse_record_json(row["record_json"])
        if rec.get("seat_name") and rec.get("address") and row["caller_agent"]:
            stats["skipped_complete"] += 1
            continue
        stats["would_update"] += 1
        if not apply:
            continue
        seat = rec.get("seat_name") or resolve_registry_seat_name(
            caller_agent=row["caller_agent"],
            seat="cursor-sdk",
        )
        worktree = _worktree_from_record(rec, row)
        address = rec.get("address")
        if not isinstance(address, dict) or not address:
            address = build_cursor_sdk_registry_address(
                dispatch_id=row["dispatch_id"],
                worker_thread=row["thread_id"],
                worktree=worktree,
            )
        rid = record_dispatch_registry_best_effort(
            dispatch_id=row["dispatch_id"],
            thread_id=row["thread_id"],
            caller_agent=row["caller_agent"] or "cursor-sdk",
            seat_name=str(seat) if seat else None,
            address=address if isinstance(address, dict) else None,
            work_key=row["work_key"],
            ledger=ledger,
        )
        if rid:
            stats["updated"] += 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    args = parser.parse_args()
    stats = scan(apply=args.apply)
    mode = "apply" if args.apply else "dry-run"
    print(f"work_registry_backfill ({mode}): {json.dumps(stats, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
