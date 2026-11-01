#!/usr/bin/env python3
"""Backfill seat_name, address, and caller_agent on non-terminal dispatch ledger rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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
    _now,
)
from services.git_integration_worker.cursor_sdk_work_registry_admit import (  # noqa: E402
    build_cursor_sdk_registry_address,
    resolve_registry_seat_name,
)

_NON_TERMINAL_STATUSES = frozenset(
    {"queued", "admitted", "running", "parked_waiting"},
)
_OPEN_PARK_KINDS = frozenset({"park_for_restart", "await_cdp_reply"})
_BACKFILL_RECORD_KEYS = frozenset({"seat_name", "address"})

_SELECT_COLUMNS = (
    "dispatch_id, thread_id, caller_agent, work_key, status, contract, "
    "record_json, lease_key, park_kind, park_resumed_by, park_expires_at"
)


def _parse_record_json(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def row_eligible_for_backfill(row: Any, *, now: str | None = None) -> bool:
    """Non-terminal dispatches plus open restart/await parks (ledger vocabulary)."""
    if str(row["contract"] or "") == "claim":
        return False
    status = str(row["status"] or "")
    if status in _NON_TERMINAL_STATUSES:
        return True
    if status != "cancelled":
        return False
    park_kind = str(row["park_kind"] or "")
    if park_kind not in _OPEN_PARK_KINDS:
        return False
    if row["park_resumed_by"]:
        return False
    stamp = now if now is not None else _now()
    expires = row["park_expires_at"]
    if expires is not None and str(expires) <= stamp:
        return False
    return True


def row_needs_backfill(row: Any) -> bool:
    rec = _parse_record_json(row["record_json"])
    if rec.get("seat_name") and rec.get("address") and row["caller_agent"]:
        return False
    return True


def _worktree_from_record(rec: dict[str, Any], row: Any) -> str | None:
    for key in ("dispatch_workspace", "lane_worktree", "worktree"):
        val = rec.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    lease = row["lease_key"] if "lease_key" in row.keys() else None
    if isinstance(lease, str) and "/lane-" in lease:
        return lease
    return None


def snapshot_entry_from_row(row: Any) -> dict[str, Any]:
    rec = _parse_record_json(row["record_json"])
    return {
        "dispatch_id": row["dispatch_id"],
        "status": row["status"],
        "caller_agent": row["caller_agent"],
        "work_key": row["work_key"],
        "thread_id": row["thread_id"],
        "lease_key": row["lease_key"],
        "record_json": rec,
    }


def assert_record_json_backfill_only(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Raise when any record_json key outside seat_name/address changed."""
    for key, val in before.items():
        if key in _BACKFILL_RECORD_KEYS:
            continue
        if after.get(key) != val:
            raise AssertionError(
                f"record_json key {key!r} changed: {val!r} -> {after.get(key)!r}"
            )
    for key in after:
        if key not in before and key not in _BACKFILL_RECORD_KEYS:
            raise AssertionError(f"record_json gained disallowed key {key!r}")


def _planned_patch(row: Any) -> tuple[dict[str, Any], str | None]:
    """Return (new_record_json, caller_agent_if_null_only)."""
    rec = _parse_record_json(row["record_json"])
    merged = dict(rec)
    seat = rec.get("seat_name") or resolve_registry_seat_name(
        caller_agent=row["caller_agent"],
        seat="cursor-sdk",
    )
    if seat and str(seat).strip():
        merged["seat_name"] = str(seat).strip()
    address = rec.get("address")
    if not isinstance(address, dict) or not address:
        worktree = _worktree_from_record(rec, row)
        address = build_cursor_sdk_registry_address(
            dispatch_id=row["dispatch_id"],
            worker_thread=row["thread_id"],
            worktree=worktree,
        )
    if isinstance(address, dict) and address:
        merged["address"] = dict(address)
    caller: str | None = None
    if not row["caller_agent"]:
        caller = "cursor-sdk"
    return merged, caller


def apply_backfill_row(
    ledger: CursorDispatchLedger,
    row: Any,
) -> bool:
    """Patch only seat_name, address (record_json) and caller_agent when null."""
    before_rec = _parse_record_json(row["record_json"])
    merged, caller = _planned_patch(row)
    assert_record_json_backfill_only(before_rec, merged)
    record_json = json.dumps(merged, sort_keys=True, separators=(",", ":"))
    with ledger._connect() as conn:
        if caller:
            cur = conn.execute(
                "UPDATE cursor_sdk_dispatches SET record_json=?, caller_agent=? "
                "WHERE dispatch_id=? AND caller_agent IS NULL",
                (record_json, caller, row["dispatch_id"]),
            )
        else:
            cur = conn.execute(
                "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
                (record_json, row["dispatch_id"]),
            )
        conn.commit()
        return cur.rowcount == 1


def fetch_rows(ledger: CursorDispatchLedger) -> list[Any]:
    with ledger._connect() as conn:
        return conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM cursor_sdk_dispatches "
            "WHERE COALESCE(contract,'') != 'claim' "
            "ORDER BY rowid ASC"
        ).fetchall()


def collect_targets(
    rows: list[Any],
    *,
    only_ids: set[str] | None,
    now: str | None = None,
) -> list[Any]:
    out: list[Any] = []
    for row in rows:
        if not row_eligible_for_backfill(row, now=now):
            continue
        if not row_needs_backfill(row):
            continue
        if only_ids is not None and str(row["dispatch_id"]) not in only_ids:
            continue
        out.append(row)
    return out


def write_snapshot(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"rows": entries, "count": len(entries)}
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def run_backfill(
    *,
    apply: bool,
    limit: int | None,
    only_ids: set[str] | None,
    snapshot_path: Path | None,
    ledger: CursorDispatchLedger | None = None,
) -> dict[str, Any]:
    led = ledger or CursorDispatchLedger.instance()
    now = _now()
    rows = fetch_rows(led)
    stats: dict[str, Any] = {
        "scanned": len(rows),
        "skipped_claim": 0,
        "skipped_ineligible": 0,
        "skipped_complete": 0,
        "targeted": 0,
        "would_update": 0,
        "updated": 0,
        "applied_ids": [],
    }
    for row in rows:
        if str(row["contract"] or "") == "claim":
            stats["skipped_claim"] += 1
    candidates = collect_targets(rows, only_ids=only_ids, now=now)
    stats["targeted"] = len(candidates)
    stats["would_update"] = len(candidates)
    snapshot_entries = [snapshot_entry_from_row(r) for r in candidates]
    if snapshot_path is not None:
        write_snapshot(snapshot_path, snapshot_entries)
    to_apply = candidates
    if limit is not None:
        to_apply = candidates[:limit]
    if not apply:
        return stats
    for row in to_apply:
        if apply_backfill_row(led, row):
            stats["updated"] += 1
            stats["applied_ids"].append(str(row["dispatch_id"]))
    return stats


def load_only_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        tok = line.strip()
        if tok and not tok.startswith("#"):
            ids.add(tok)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="When applying, update at most N rows (snapshot order)",
    )
    parser.add_argument(
        "--only-ids",
        type=Path,
        default=None,
        metavar="FILE",
        help="One dispatch_id per line; default is all eligible rows",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write JSON pre-image of every targeted row before any apply",
    )
    args = parser.parse_args()
    only_ids = load_only_ids(args.only_ids) if args.only_ids else None
    stats = run_backfill(
        apply=args.apply,
        limit=args.limit,
        only_ids=only_ids,
        snapshot_path=args.snapshot,
    )
    mode = "apply" if args.apply else "dry-run"
    print(f"work_registry_backfill ({mode}): {json.dumps(stats, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
