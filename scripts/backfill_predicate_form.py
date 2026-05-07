#!/usr/bin/env python3
"""Backfill predicate_form for assertions that predate the post-write hook.

Rows with predicate_form IS NULL AND superseded_by IS NULL were inserted before
the Slice-3 trigger existed and will never be enriched by the normal insert path.

∀ row r: r.predicate_form IS NULL ∧ r.superseded_by IS NULL →
  dispatch predicate-extract pipeline → pipeline handler writes predicate_form back.

The pipeline handler is idempotent — rows already populated are skipped.
Rate-limited to --concurrency (default 4) to respect qwen3-14b's 5-slot context.

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


def _fetch_pending(db_path: Path, batch_size: int) -> list[dict]:
    """Return up to batch_size rows needing predicate_form backfill."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, claim, entity_id FROM assertions "
        "WHERE predicate_form IS NULL AND superseded_by IS NULL "
        "ORDER BY id "
        "LIMIT ?",
        (batch_size,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _count_pending(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM assertions "
        "WHERE predicate_form IS NULL AND superseded_by IS NULL"
    ).fetchone()
    conn.close()
    return n


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
) -> tuple[int, int]:
    """Process rows with bounded concurrency. Returns (ok_count, err_count)."""
    sem = asyncio.Semaphore(concurrency)
    ok = err = 0

    async def _guarded(row: dict) -> None:
        nonlocal ok, err
        async with sem:
            _, success = await _dispatch_one(
                client, row, dry_run=dry_run, delay_ms=delay_ms
            )
            if success:
                ok += 1
            else:
                err += 1

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        await asyncio.gather(*[_guarded(r) for r in rows])

    return ok, err


async def main(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"ERROR: cortex.db not found at {db_path}", file=sys.stderr)
        return 1

    total_pending = _count_pending(db_path)
    print(
        f"Predicate-form backfill {'(DRY RUN) ' if args.dry_run else ''}"
        f"— {total_pending} rows pending"
    )
    if total_pending == 0:
        print("Nothing to do.")
        return 0

    rows_processed = ok_total = err_total = 0

    while True:
        batch = _fetch_pending(db_path, args.batch_size)
        if not batch:
            break

        print(f"\nBatch: {len(batch)} rows (processed so far: {rows_processed})")
        ok, err = await _run_batch(
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

        remaining = _count_pending(db_path)
        print(
            f"  batch done — ok={ok} err={err} | "
            f"total ok={ok_total} err={err_total} remaining={remaining}"
        )
        if remaining == 0:
            break

    prefix = "DRY-RUN " if args.dry_run else ""
    print(
        f"\n{prefix}Done: {rows_processed} rows dispatched, "
        f"{ok_total} ok, {err_total} errors"
    )
    return 0 if err_total == 0 else 1


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
