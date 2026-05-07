#!/usr/bin/env python3
"""Backfill predicate_form for assertions that predate the post-write hook.

Rows with predicate_form IS NULL AND superseded_by IS NULL were inserted before
the Slice-3 trigger existed and will never be enriched by the normal insert path.

∀ row r: r.predicate_form IS NULL ∧ r.superseded_by IS NULL →
  dispatch predicate-extract pipeline → pipeline handler writes predicate_form back.

The pipeline handler is idempotent — rows already populated are skipped.
Rate-limited to --concurrency (default 4) to respect gpt-4o-mini's rate limits.

Silent-failure detection: the pipeline returns HTTP 200 even when it internally
cannot process a row (e.g. legacy enum values rejected by cortex-api). After each
batch the script verifies which dispatched rows actually have predicate_form written.
Rows that remain NULL after dispatch are recorded as permanent skips and excluded
from all subsequent batches, preventing infinite loops.

Usage:
    python scripts/backfill_predicate_form.py [--dry-run] [--concurrency N]
                                               [--batch-size N] [--delay-ms N]
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

import httpx

CORTEX_DB = Path.home() / ".cortex" / "cortex.db"
STARGATE_URL = "http://localhost:9999"
_PIPELINE_ID = "predicate-extract"
_REQUEST_TIMEOUT = 90.0


def _fetch_pending(
    db_path: Path,
    batch_size: int,
    skip: frozenset[int],
) -> list[dict]:
    """Return up to batch_size rows needing predicate_form backfill.

    Excludes IDs in `skip` — rows where a prior dispatch returned HTTP 200
    but predicate_form was never written (silent pipeline failure).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if skip:
        placeholders = ",".join("?" * len(skip))
        rows = conn.execute(
            f"SELECT id, claim, entity_id FROM assertions "
            f"WHERE predicate_form IS NULL AND superseded_by IS NULL "
            f"AND id NOT IN ({placeholders}) "
            f"ORDER BY id LIMIT ?",
            (*sorted(skip), batch_size),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, claim, entity_id FROM assertions "
            "WHERE predicate_form IS NULL AND superseded_by IS NULL "
            "ORDER BY id LIMIT ?",
            (batch_size,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _count_pending(db_path: Path, skip: frozenset[int] = frozenset()) -> int:
    conn = sqlite3.connect(str(db_path))
    if skip:
        placeholders = ",".join("?" * len(skip))
        (n,) = conn.execute(
            f"SELECT COUNT(*) FROM assertions "
            f"WHERE predicate_form IS NULL AND superseded_by IS NULL "
            f"AND id NOT IN ({placeholders})",
            tuple(sorted(skip)),
        ).fetchone()
    else:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM assertions "
            "WHERE predicate_form IS NULL AND superseded_by IS NULL"
        ).fetchone()
    conn.close()
    return n


def _check_written(db_path: Path, ids: list[int]) -> frozenset[int]:
    """Return the subset of ids that still have predicate_form IS NULL (silent failures)."""
    if not ids:
        return frozenset()
    conn = sqlite3.connect(str(db_path))
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id FROM assertions "
        f"WHERE id IN ({placeholders}) AND predicate_form IS NULL",
        ids,
    ).fetchall()
    conn.close()
    return frozenset(r[0] for r in rows)


async def _dispatch_one(
    client: httpx.AsyncClient,
    row: dict,
    *,
    dry_run: bool,
    delay_ms: int,
) -> tuple[int, bool]:
    """Fire the predicate-extract pipeline for one row.

    Returns (assertion_id, success).
    """
    assertion_id: int = row["id"]
    if dry_run:
        print(
            f"  DRY-RUN id={assertion_id} entity={row['entity_id']!r} "
            f"claim={row['claim'][:60]!r}"
        )
        return assertion_id, True

    payload = {
        "model": _PIPELINE_ID,
        "messages": [{"role": "user", "content": "extract"}],
        "pipeline_options": {
            "assertion_id": assertion_id,
            "claim": row["claim"],
            "entity_id": row["entity_id"],
        },
    }
    try:
        resp = await client.post(f"{STARGATE_URL}/v1/chat/completions", json=payload)
        ok = resp.status_code < 400
        status = "OK" if ok else f"HTTP {resp.status_code}"
    except Exception as exc:
        ok = False
        status = f"ERR {exc}"

    print(
        f"  id={assertion_id} entity={row['entity_id']!r} "
        f"claim={row['claim'][:60]!r} → {status}"
    )
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)
    return assertion_id, ok


async def _run_batch(
    rows: list[dict],
    *,
    dry_run: bool,
    concurrency: int,
    delay_ms: int,
) -> tuple[int, int, list[int]]:
    """Process rows with bounded concurrency.

    Returns (ok_count, err_count, dispatched_ids).
    Caller should verify which dispatched_ids actually got written.
    """
    sem = asyncio.Semaphore(concurrency)
    ok = err = 0
    dispatched: list[int] = []

    async def _guarded(row: dict) -> None:
        nonlocal ok, err
        async with sem:
            aid, success = await _dispatch_one(
                client, row, dry_run=dry_run, delay_ms=delay_ms
            )
            dispatched.append(aid)
            if success:
                ok += 1
            else:
                err += 1

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        await asyncio.gather(*[_guarded(r) for r in rows])

    return ok, err, dispatched


async def main(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"ERROR: cortex.db not found at {db_path}", file=sys.stderr)
        return 1

    skip: frozenset[int] = frozenset()
    total_pending = _count_pending(db_path, skip)
    print(
        f"Predicate-form backfill {'(DRY RUN) ' if args.dry_run else ''}"
        f"— {total_pending} rows pending"
    )
    if total_pending == 0:
        print("Nothing to do.")
        return 0

    rows_processed = ok_total = err_total = silent_fail_total = 0

    while True:
        batch = _fetch_pending(db_path, args.batch_size, skip)
        if not batch:
            break

        print(f"\nBatch: {len(batch)} rows (processed so far: {rows_processed})")
        ok, err, dispatched = await _run_batch(
            batch,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
            delay_ms=args.delay_ms,
        )
        ok_total += ok
        err_total += err
        rows_processed += len(batch)

        if args.dry_run:
            # dry-run can't deplete the pending set; one batch is enough
            break

        # Detect silent failures: pipeline returned HTTP 200 but predicate_form
        # was never written (e.g. cortex-api rejects the row internally).
        # Add these to the skip set so they don't re-queue indefinitely.
        silent = _check_written(db_path, dispatched)
        if silent:
            skip = skip | silent
            silent_fail_total += len(silent)
            for sid in sorted(silent):
                print(
                    f"  SKIP id={sid} — pipeline returned OK but predicate_form "
                    f"not written (silent failure; likely invalid enum value)"
                )

        remaining = _count_pending(db_path, skip)
        print(
            f"  batch done — ok={ok} err={err} silent_skip={len(silent)} | "
            f"total ok={ok_total} err={err_total} skipped={silent_fail_total} "
            f"remaining={remaining}"
        )
        if remaining == 0:
            break

    prefix = "DRY-RUN " if args.dry_run else ""
    print(
        f"\n{prefix}Done: {rows_processed} rows dispatched, "
        f"{ok_total} ok, {err_total} errors, {silent_fail_total} silent skips"
    )
    if silent_fail_total:
        print(
            f"  {silent_fail_total} row(s) could not be processed — likely have "
            f"invalid legacy field values. Run migration 021 to normalize them, "
            f"then re-run this script."
        )
    return 0 if (err_total == 0 and silent_fail_total == 0) else 1


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rows that would be dispatched without calling Stargate.",
    )
    p.add_argument(
        "--db",
        default=str(CORTEX_DB),
        help="Path to cortex.db (default: ~/.cortex/cortex.db).",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max concurrent pipeline calls (default 4; qwen3-14b has 5 slots).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Rows fetched per SELECT page (default 100).",
    )
    p.add_argument(
        "--delay-ms",
        type=int,
        default=200,
        help="Per-request delay in milliseconds after response (default 200).",
    )
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(_parse())))
